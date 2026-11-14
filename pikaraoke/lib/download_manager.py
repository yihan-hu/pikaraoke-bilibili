"""Bounded concurrent downloads with isolated progress and ordered playback handoff."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from collections import deque
from time import monotonic

import psutil
from gevent import Greenlet, joinall, killall, spawn
from gevent.local import local
from gevent.queue import JoinableQueue as Queue

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.get_platform import is_windows
from pikaraoke.lib.preference_manager import PreferenceManager
from pikaraoke.lib.queue_manager import QueueManager
from pikaraoke.lib.song_manager import SongManager
from pikaraoke.lib.youtube_dl import (
    POSTPROCESS_PREFIX,
    PROGRESS_PREFIX,
    SIZE_PREFIX,
    build_ytdl_download_command,
    get_youtube_id_from_url,
)

# YouTube refuses a freshly resolved URL with a 403 on roughly half of all downloads,
# regardless of the format selected, and re-extracting from scratch is the only known
# remedy. At that rate three attempts would still leave one download in eight failing.
MAX_DOWNLOAD_ATTEMPTS = 5

# The bar is one 0-100 scale of downloaded bytes; the video and audio passes are bands
# on it. Seam between them until the real sizes arrive.
_FALLBACK_VIDEO_END = 90.0

# A speed and an ETA describe the instant yt-dlp printed them. Between the two passes of
# a "+" selector nothing is printed for as long as the next connection takes to open, and
# the last pair goes on claiming a transfer that has already stopped.
STALE_PROGRESS_SECONDS = 1.5

# The only statuses that carry a rate, and so the only ones that can go stale.
_RATE_BEARING_STATUSES = ("downloading", "downloading audio")


def _selects_separate_streams(cmd: list[str]) -> bool:
    """True when the format selector downloads video and audio as separate passes.

    Read off the selector, not the high_quality preference, which only caps resolution.
    """
    try:
        selector = cmd[cmd.index("-f") + 1]
    except (ValueError, IndexError):
        return False
    return "+" in selector.split("/")[0]


def _video_band_end(line: str, fallback: float) -> float:
    """Place the video/audio seam by the formats' real sizes, e.g. "[12577843, 4205572]".

    Progress lines only ever see their own pass, so this is the only whole-job figure.
    """
    try:
        sizes = [int(n) for n in line[len(SIZE_PREFIX) :].strip().strip("[]").split(",")]
    except ValueError:
        return fallback
    if len(sizes) != 2 or not sum(sizes):
        return fallback
    return round(100 * sizes[0] / sum(sizes), 1)


# The progress templates share the stream yt-dlp's prose goes to, so a download killed
# mid-transfer ends on one of these. They carry no diagnosis a person can read.
_TEMPLATED_PREFIXES = (PROGRESS_PREFIX, POSTPROCESS_PREFIX, SIZE_PREFIX)


def _summarise_ytdl_failure(output: str) -> str:
    """Reduce yt-dlp's captured output to the line that says what went wrong.

    yt-dlp reports warnings before it attempts a download, so the whole transcript on an
    error card buries the diagnosis past the truncation point. The full output is logged
    either way.
    """
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("ERROR:"):
            return stripped.removeprefix("ERROR:").strip()
    tail = [
        stripped
        for line in output.splitlines()
        if (stripped := line.strip()) and not stripped.startswith(_TEMPLATED_PREFIXES)
    ]
    return tail[-1] if tail else "Unknown error"


def _use_spare_capacity(process: subprocess.Popen) -> None:
    """Drop a download to background priority so it never competes with playback.

    Priority is inherited, which is what covers the ffmpeg merge yt-dlp spawns.
    """
    try:
        child = psutil.Process(process.pid)
        if is_windows():
            child.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
            child.ionice(psutil.IOPRIO_VERYLOW)
        else:
            child.nice(10)
            # macOS has no ionice at all, hence AttributeError below.
            child.ionice(psutil.IOPRIO_CLASS_IDLE)
    except (psutil.Error, AttributeError, NotImplementedError, OSError) as e:
        logging.debug(f"Could not lower download priority: {e}")


class DownloadManager:
    """Download with bounded concurrency and per-worker progress.

    Two workers share the playback-safe process priority while completion
    handoff preserves request order for download-first sources.

    Attributes:
        download_queue: Queue holding pending download requests.
    """

    def __init__(
        self,
        events: EventSystem,
        preferences: PreferenceManager,
        song_manager: SongManager,
        queue_manager: QueueManager,
        download_path: str,
        youtubedl_proxy: str | None = None,
        additional_ytdl_args: str | None = None,
        max_concurrent_downloads: int = 2,
    ) -> None:
        """Initialize the download manager.

        Args:
            events: Event system for notifications and broadcasts.
            preferences: Configuration manager for persistent settings.
            song_manager: Manager for song library operations.
            queue_manager: Manager for playback queue.
            download_path: Directory where downloads are saved.
            youtubedl_proxy: Optional proxy URL for yt-dlp.
            additional_ytdl_args: Optional additional arguments for yt-dlp.
        """
        self._events = events
        self._preferences = preferences
        self._song_manager = song_manager
        self._queue_manager = queue_manager
        self._download_path = download_path
        self._youtubedl_proxy = youtubedl_proxy
        self._additional_ytdl_args = additional_ytdl_args
        self.download_queue: Queue = Queue()
        self.pending_downloads: list[dict] = []  # Shadow queue for visibility
        self.download_errors: list[dict] = []  # Track failed downloads
        self.max_concurrent_downloads = max(1, min(4, max_concurrent_downloads))
        self._context = local()
        self._active_downloads: dict[str, dict] = {}
        self._enqueue_order: deque[str] = deque()
        self._finished_for_queue: dict = {}
        self._workers: list[Greenlet] = []
        self._worker: Greenlet | None = None
        self._is_downloading: bool = False  # Track if a download is currently in progress

    @property
    def active_download(self) -> dict | None:
        """Worker-local progress; outside a worker, retain the legacy first-active view."""
        if hasattr(self._context, "active"):
            return self._context.active
        return next(iter(self._active_downloads.values()), None)

    @active_download.setter
    def active_download(self, value: dict | None) -> None:
        self._context.active = value

    def _supervise_workers(self) -> None:
        """Keep all workers on one gevent hub and reap them together on shutdown."""
        self._workers = [spawn(self._process_queue) for _ in range(self.max_concurrent_downloads)]
        try:
            joinall(self._workers, raise_error=True)
        finally:
            killall(self._workers, block=True)

    def start(self) -> None:
        """Start the download worker greenlet.

        Runs as a greenlet on the main gevent hub rather than a raw OS thread:
        a separate thread touching monkey-patched primitives (Queue, subprocess)
        spins up a second hub, which races the main hub at WSGIServer startup and
        can deadlock (observed hanging PiKaraoke launch on macOS).
        """
        self._worker = spawn(self._supervise_workers)
        logging.debug("Download queue worker started")

    def get_downloads_status(self) -> dict:
        """Get the status of active and pending downloads.

        Returns:
            Dict containing 'active' download info and list of 'pending' downloads.
        """
        active = [self._active_download_status(state) for state in self._active_downloads.values()]
        if not active and self.active_download:
            active = [self._active_download_status()]
        return {
            "active": active[0] if active else None,
            "active_downloads": active,
            "max_concurrent": self.max_concurrent_downloads,
            "pending": self.pending_downloads,
            "errors": self.download_errors,
            "max_attempts": MAX_DOWNLOAD_ATTEMPTS,
        }

    def _active_download_status(self, state: dict | None = None) -> dict | None:
        """The active download as the UI reads it, with an expired rate marked stale.

        The byte count behind the bar stays true through a silence; the speed and the
        ETA do not, so the panel needs to know which of the two it is holding.
        """
        state = state if state is not None else self.active_download
        if not state:
            return None
        printed_at = state.get("progress_at")
        active = {key: value for key, value in state.items() if key != "progress_at"}
        active["stalled"] = state["status"] in _RATE_BEARING_STATUSES and (
            printed_at is None or monotonic() - printed_at > STALE_PROGRESS_SECONDS
        )
        return active

    def remove_error(self, error_id: str) -> bool:
        """Remove an error from the list by ID.

        Args:
            error_id: The ID of the error to remove.

        Returns:
            True if removed, False if not found.
        """
        initial_len = len(self.download_errors)
        self.download_errors = [e for e in self.download_errors if e["id"] != error_id]
        return len(self.download_errors) < initial_len

    def retry_error(self, error_id: str) -> bool:
        """Re-queue a failed download and drop its error entry.

        Args:
            error_id: The ID of the error to retry.

        Returns:
            True if re-queued, False if the error was not found.
        """
        entry = next((e for e in self.download_errors if e["id"] == error_id), None)
        if entry is None:
            return False

        self.remove_error(error_id)
        self.queue_download(
            entry["url"],
            enqueue=entry["enqueue"],
            user=entry["user"],
            title=entry["title"],
        )
        return True

    def queue_download(
        self,
        video_url: str,
        enqueue: bool = False,
        user: str = "Pikaraoke",
        title: str | None = None,
    ) -> None:
        """Queue a video for download.

        Up to two downloads run concurrently; additional requests wait.

        Args:
            video_url: YouTube video URL.
            enqueue: Whether to add to playback queue after download.
            user: Username to attribute the download to.
            title: Display title (defaults to URL if not provided).
        """
        from flask_babel import _

        # Strip playlist parameter to avoid downloading entire playlists
        if "&list=" in video_url:
            video_url = video_url.split("&list=")[0]

        if any(item["video_url"] == video_url for item in self.pending_downloads) or any(
            item["url"] == video_url for item in self._active_downloads.values()
        ):
            raise ValueError("此歌曲已在下载列表中")
        displayed_title = title if title else video_url

        # Check how many items are ahead (in queue + currently downloading)
        pending_count = self.download_queue.qsize() + (
            len(self._active_downloads) or int(self._is_downloading)
        )

        if pending_count > 0:
            # MSG: Message shown when download is added to queue (not first in line)
            self._events.emit(
                "notification",
                _("Download queued (#%d): %s") % (pending_count + 1, displayed_title),
            )
        else:
            # MSG: Message shown when download is added and will start immediately
            self._events.emit("notification", _("Download starting: %s") % displayed_title)

        # If queue was just started (was not downloading before), emit event
        if not self._is_downloading and self.download_queue.empty():
            self._events.emit("download_started")

        download_data = {
            "id": uuid.uuid4().hex,
            "video_url": video_url,
            "enqueue": enqueue,
            "user": user,
            "title": title,
            "display_title": displayed_title,
            "attempts": 0,
        }

        if enqueue:
            self._enqueue_order.append(download_data["id"])

        # Add to the download queue and shadow list
        self.download_queue.put(download_data)
        self.pending_downloads.append(download_data)

    def _process_queue(self) -> None:
        """Worker thread that processes downloads from the queue serially.

        Runs indefinitely, blocking on queue.get() until items are available.
        Each download is processed completely before the next one starts.
        """
        while True:
            download_request = self.download_queue.get()

            self.pending_downloads = [
                item for item in self.pending_downloads if item is not download_request
            ]
            job_id = download_request.setdefault("id", uuid.uuid4().hex)
            self._is_downloading = True

            # Initialize active download state
            attempts = download_request["attempts"]
            self.active_download = {
                "id": job_id,
                "title": download_request.get("display_title", download_request["video_url"]),
                "url": download_request["video_url"],
                "user": download_request["user"],
                "progress": 0.0,
                "status": "retrying" if attempts else "starting",
                "attempts": attempts,
                "eta": "--:--",
                "speed": "---",
            }

            self._active_downloads[job_id] = self.active_download

            try:
                self._execute_download(download_request)
            except Exception as e:
                logging.error(f"Error processing download: {e}")
                self._complete_for_queue(download_request, None)
            finally:
                self._active_downloads.pop(job_id, None)
                self._is_downloading = bool(self._active_downloads)
                self.active_download = None
                self.download_queue.task_done()

                # Check if we are done with all downloads
                if self.download_queue.empty() and not self._active_downloads:
                    self._events.emit("download_stopped")

    def _complete_for_queue(self, request: dict, song_path: str | None) -> None:
        """Finish in request order even when a later small download completes first."""
        if not request["enqueue"]:
            return
        job_id = request.get("id")
        if job_id not in self._enqueue_order:
            if song_path:
                self._queue_manager.enqueue(song_path, request["user"], log_action=False)
            return
        self._finished_for_queue[job_id] = (song_path, request["user"])
        while self._enqueue_order and self._enqueue_order[0] in self._finished_for_queue:
            path, user = self._finished_for_queue.pop(self._enqueue_order.popleft())
            if path:
                self._queue_manager.enqueue(path, user, log_action=False)

    def _run_ytdl(self, cmd: list[str]) -> tuple[int, str]:
        """Give each concurrent extractor a private cookie snapshot to write back."""
        if "--cookies" not in cmd:
            return self._run_ytdl_process(cmd)
        index = cmd.index("--cookies") + 1
        with tempfile.TemporaryDirectory(prefix="pikaraoke-download-") as directory:
            snapshot = os.path.join(directory, "cookies.txt")
            shutil.copyfile(cmd[index], snapshot)
            private_cmd = cmd[:]
            private_cmd[index] = snapshot
            return self._run_ytdl_process(private_cmd)

    def _run_ytdl_process(self, cmd: list[str]) -> tuple[int, str]:
        """Run yt-dlp to completion, streaming its progress into active_download.

        Returns:
            Tuple of (return code, captured output).
        """
        # Use Popen to capture output in real-time
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,  # Line buffered
            universal_newlines=True,
        )
        _use_spare_capacity(process)

        output_buffer = []
        video_end = _FALLBACK_VIDEO_END if _selects_separate_streams(cmd) else 100.0

        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            if line:
                output_buffer.append(line)
                if line.startswith(SIZE_PREFIX):
                    video_end = _video_band_end(line, video_end)
                else:
                    self._apply_progress_line(line, video_end)

        rc = process.poll()
        output = "".join(output_buffer)
        if rc == 0:
            # A retry that succeeds is only diagnosable against the attempt that failed,
            # so the winning attempt's output has to be kept as well.
            logging.debug(f"yt-dlp output: {output}")
        return rc, output

    def _apply_progress_line(self, line: str, video_end: float) -> None:
        """Fold one templated yt-dlp progress line into active_download."""
        if not self.active_download:
            return

        if line.startswith(POSTPROCESS_PREFIX):
            if line[len(POSTPROCESS_PREFIX) :].strip() == "Merger":
                # Every byte is down; the last speed and ETA describe nothing by then.
                self.active_download["progress"] = 100.0
                self.active_download["status"] = "merging"
                self.active_download["speed"] = "---"
                self.active_download["eta"] = "--:--"
            return

        if not line.startswith(PROGRESS_PREFIX):
            return
        fields = line[len(PROGRESS_PREFIX) :].strip().split("|")
        if len(fields) != 4:
            return

        percent_str, speed, eta, vcodec = (field.strip() for field in fields)
        audio_pass = vcodec == "none"
        self.active_download["status"] = "downloading audio" if audio_pass else "downloading"
        self.active_download["speed"] = speed
        self.active_download["eta"] = eta
        self.active_download["progress_at"] = monotonic()

        try:
            percent = float(percent_str.rstrip("%"))
        except ValueError:
            return  # "Unknown%": hold the last real percentage rather than reset the bar
        if audio_pass:
            start, span = video_end, 100.0 - video_end
        else:
            start, span = 0.0, video_end
        self.active_download["progress"] = round(start + percent / 100 * span, 1)

    def _requeue(self, request: dict) -> bool:
        """Put a failed download back at the end of the queue.

        Back-of-queue placement is the backoff: any download waiting behind the failed
        one overtakes it, so a dead link can never block the serialized worker.

        Returns:
            True if re-queued, False once the attempt cap is reached.
        """
        attempts = request["attempts"] + 1
        if attempts >= MAX_DOWNLOAD_ATTEMPTS:
            return False

        request["attempts"] = attempts
        if self.active_download:
            self.active_download["progress"] = 0.0
            self.active_download["status"] = "retrying"
            self.active_download["attempts"] = attempts
            # The dead attempt's last speed and ETA describe nothing once it has stopped.
            self.active_download["speed"] = "---"
            self.active_download["eta"] = "--:--"

        self.download_queue.put(request)
        self.pending_downloads.append(request)
        logging.info(
            f"Re-queueing failed download {request['video_url']} "
            f"(attempt {attempts + 1} of {MAX_DOWNLOAD_ATTEMPTS})"
        )
        return True

    def _execute_download(self, request: dict) -> int:
        """Execute a video download, re-queueing it if it fails with attempts to spare.

        Args:
            request: Download request as built by queue_download.

        Returns:
            Return code from the download process (0 = success).
        """
        from flask_babel import _

        video_url = request["video_url"]
        enqueue = request["enqueue"]
        user = request["user"]
        displayed_title = request["title"] if request["title"] else video_url

        # A retry is a quiet safety net, and the queue page already shows it. Repeating
        # this toast on every attempt just reads as a glitch.
        if not request["attempts"]:
            # MSG: Message shown when download actually starts (after waiting in queue)
            self._events.emit("notification", _("Downloading video: %s") % displayed_title)

        cmd = build_ytdl_download_command(
            video_url,
            self._download_path,
            self._preferences.get_or_default("high_quality"),
            self._youtubedl_proxy,
            self._additional_ytdl_args,
        )
        logging.debug("yt-dlp command: " + " ".join(cmd))

        try:
            rc, output = self._run_ytdl(cmd)
        except Exception as e:
            # Deliberately broad: whatever goes wrong spawning or reading yt-dlp, the request
            # has to reach the retry and error card below rather than vanish out of the worker.
            logging.error(f"yt-dlp invocation failed for {video_url}: {e}")
            rc, output = 1, f"{type(e).__name__}: {e}"

        if rc != 0:
            logging.error(f"yt-dlp stderr: {output}")
            if self._requeue(request):
                return rc

            # MSG: Message shown after the download process is completed but the song is not found
            self._events.emit(
                "notification",
                _("Error downloading song: ") + displayed_title,
                "danger",
            )
            self._complete_for_queue(request, None)
            self.download_errors.append(
                {
                    "id": str(uuid.uuid4()),
                    "title": displayed_title,
                    "url": video_url,
                    "user": user,
                    "enqueue": enqueue,
                    "error": _summarise_ytdl_failure(output),
                }
            )
        else:
            if self.active_download:
                self.active_download["progress"] = 100
                self.active_download["status"] = "complete"

            if enqueue:
                # MSG: Message shown after the download is completed and queued
                self._events.emit(
                    "notification",
                    _("Downloaded and queued: %s") % displayed_title,
                    "success",
                )
            else:
                # MSG: Message shown after the download is completed but not queued
                self._events.emit("notification", _("Downloaded: %s") % displayed_title, "success")

            # After download, find the file path by ID
            song_path = None
            video_id = get_youtube_id_from_url(video_url)
            if video_id:
                logging.debug(f"Searching for downloaded file by ID: {video_id}")
                song_path = self._song_manager.songs.find_by_id(self._download_path, video_id)
            else:
                logging.warning("No video ID available to find downloaded song")

            if video_id and video_id.startswith("bilibili-"):
                for line in output.splitlines():
                    if line.startswith("[pk-file]|"):
                        candidate = os.path.abspath(line.split("|", 1)[1].strip())
                        if os.path.commonpath(
                            [candidate, os.path.abspath(self._download_path)]
                        ) == os.path.abspath(self._download_path) and os.path.isfile(candidate):
                            song_path = candidate
            if song_path:
                self._events.emit("song_downloaded", song_path, video_id)
            else:
                logging.warning(
                    f"Could not find downloaded song in {self._download_path} matching ID: {video_id}"
                )

            if enqueue:
                if song_path:
                    self._complete_for_queue(request, song_path)
                else:
                    self._complete_for_queue(request, None)
                    # MSG: Message shown after the download is completed but the adding to queue fails
                    self._events.emit(
                        "notification",
                        _("Error queueing song: ") + displayed_title,
                        "danger",
                    )

        return rc
