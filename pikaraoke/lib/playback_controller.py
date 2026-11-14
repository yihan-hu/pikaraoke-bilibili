"""Playback controller for managing video playback state and coordination."""

import logging
import os
import time
from typing import TYPE_CHECKING, Callable

from flask_babel import _

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.file_resolver import delete_tmp_dir
from pikaraoke.lib.preference_manager import PreferenceManager
from pikaraoke.lib.stream_manager import PlaybackResult, StreamManager

if TYPE_CHECKING:
    import subprocess


class PlaybackController:
    """Controller for managing playback state and stream coordination.

    Owns all "now playing" state and coordinates with StreamManager for
    FFmpeg transcoding and playback.

    Attributes:
        now_playing: Title of the currently playing song.
        now_playing_filename: File path of the currently playing song.
        now_playing_user: User who queued the current song.
        now_playing_transpose: Semitones to transpose current song.
        now_playing_duration: Duration of current song in seconds.
        now_playing_url: Stream URL for current song.
        now_playing_subtitle_url: URL path for subtitles.
        now_playing_position: Current playback position in seconds.
        is_paused: Whether playback is paused.
        is_playing: Whether a song is currently playing.
        ffmpeg_process: Currently running FFmpeg subprocess.
    """

    now_playing: str | None = None
    now_playing_filename: str | None = None
    now_playing_user: str | None = None
    now_playing_transpose: int = 0
    now_playing_duration: int | None = None
    now_playing_url: str | None = None
    now_playing_subtitle_url: str | None = None
    now_playing_position: float | None = None
    is_paused: bool = True
    is_playing: bool = False
    is_buffering: bool = False
    resume_position: float | None = None
    expected_duration: float | None = None
    cached_file: str | None = None
    _cache_switching_for: str | None = None

    def __init__(
        self,
        preferences: PreferenceManager,
        events: EventSystem,
        filename_from_path: Callable[[str, bool], str],
        streaming_format: str = "hls",
        base_path: str = "",
    ) -> None:
        """Initialize the playback controller.

        Args:
            preferences: PreferenceManager instance for configuration.
            events: EventSystem instance for event emission.
            filename_from_path: Function to extract display name from path.
            streaming_format: Video streaming format ('hls' or 'mp4').
            base_path: URL path prefix when PiKaraoke is hosted under a subpath.
        """
        self.preferences = preferences
        self.events = events
        self.filename_from_path = filename_from_path
        self.stream_manager = StreamManager(preferences, streaming_format, base_path)

    @property
    def ffmpeg_process(self) -> "subprocess.Popen | None":
        """Get the current FFmpeg process."""
        return self.stream_manager.ffmpeg_process

    def play_file(self, file_path: str, user: str, semitones: int = 0) -> PlaybackResult:
        """Start playback of a media file.

        Blocks until client connects or timeout occurs.

        Args:
            file_path: Path to the media file to play.
            user: User who queued the song.
            semitones: Number of semitones to transpose (0 = no change).

        Returns:
            PlaybackResult with success status and stream information.
        """
        if not os.path.isfile(file_path):
            error_msg = _("Song file not found: %s") % file_path
            logging.warning(error_msg)
            self.now_playing_filename = None
            return PlaybackResult(success=False, error=error_msg)

        logging.info(
            f"Playing file: {file_path} for user: {user}, transposed {semitones} semitones"
        )

        self.claim(file_path)

        result = self.stream_manager.play_file(file_path, semitones)

        if not result.success:
            self.now_playing_filename = None
            return result

        self.now_playing = self.filename_from_path(file_path, remove_youtube_id=True)
        self.now_playing_user = user
        self.now_playing_transpose = semitones
        self.now_playing_duration = result.duration
        self.expected_duration = result.duration
        self.now_playing_url = result.stream_url
        self.now_playing_subtitle_url = result.subtitle_url
        self.is_paused = False

        self.events.emit("playback_started")

        return self._wait_for_client(result)

    def play_online(self, url: str, user: str, title: str, semitones: int = 0) -> PlaybackResult:
        """Play an uncached Bilibili item through the local MP4 relay immediately."""
        from urllib.parse import quote
        from uuid import uuid4

        from pikaraoke.lib.bilibili import normalize_url

        url = normalize_url(url)
        self.claim(url)
        self.now_playing = title or "B站视频"
        self.now_playing_user = user
        self.now_playing_transpose = 0
        self.now_playing_duration = None
        self.expected_duration = None
        self.now_playing_position = 0.0
        self.now_playing_url = self.stream_manager._with_base_path(
            f"/bilibili-preview?url={quote(url, safe='')}&instance={uuid4().hex}"
        )
        self.now_playing_subtitle_url = None
        self.is_paused = False
        self.is_playing = True  # Reserve the current turn, including unlimited buffering.
        self.is_buffering = True
        self.cached_file = None
        self.resume_position = None
        if semitones:
            self.events.emit("notification", "在线歌曲先用原调播放，缓存完成后可以变调", "info")
        result = PlaybackResult(success=True, stream_url=self.now_playing_url)
        self.events.emit("playback_started")
        return result

    def load_online_metadata(self, video_url: str, stream_url: str) -> None:
        """Keep the full video duration so a short CDN response is not treated as completion."""
        from urllib.parse import parse_qs, urlparse
        from pikaraoke.lib.bilibili import details

        try:
            info = details(video_url)
            number = int(parse_qs(urlparse(video_url).query).get("p", ["1"])[0])
            part = next((item for item in info["parts"] if item["part"] == number), None)
            if self.now_playing_url != stream_url or not part:
                return
            duration = part.get("duration")
            if isinstance(duration, (float, int)) and duration > 0:
                self.expected_duration = duration
                self.now_playing_duration = duration
                self.events.emit("now_playing_update")
        except (ValueError, OSError):
            logging.info("Online duration unavailable; retaining the current song")

    def cache_ready(self, path: str, video_id: str | None) -> None:
        """Use a newly completed cache if this online track is currently buffering."""
        from pikaraoke.lib.bilibili import media_id

        if not self.now_playing_filename or not self.now_playing_filename.startswith("https://"):
            return
        if media_id(self.now_playing_filename) != video_id:
            return
        self.cached_file = path
        if self.is_buffering:
            self.set_buffering(True)

    def set_buffering(self, buffering: bool) -> None:
        """Buffering never removes the current song or resumes background music."""
        self.is_buffering = buffering
        self.events.emit("now_playing_update")
        if buffering and self.cached_file and self._cache_switching_for is None:
            from gevent import spawn

            self._cache_switching_for = self.now_playing_url
            spawn(self._resume_cached, self.now_playing_url, self.cached_file)

    def _resume_cached(self, source_url: str, path: str) -> None:
        """Prepare a local stream, then resume the same performance at its position."""
        manager = StreamManager(
            self.preferences, self.stream_manager.streaming_format, self.stream_manager.base_path
        )
        try:
            result = manager.play_file(path, 0)
            if self.now_playing_url != source_url or not self.is_playing:
                manager.kill_ffmpeg()
                return
            if not result.success:
                manager.kill_ffmpeg()
                return
            expected = self.expected_duration
            if (
                expected
                and result.duration
                and result.duration < expected - max(5, expected * 0.03)
            ):
                manager.kill_ffmpeg()
                self.cached_file = None
                self.events.emit(
                    "notification", "缓存文件长度异常，继续等待在线播放或手动切歌", "warning"
                )
                return
            position = self.now_playing_position or 0.0
            self.stream_manager.kill_ffmpeg()
            self.stream_manager = manager
            self.now_playing_filename = path
            self.now_playing_url = result.stream_url
            self.now_playing_duration = result.duration
            self.now_playing_subtitle_url = result.subtitle_url
            self.resume_position = position
            self.is_buffering = True
            self.events.emit("now_playing_update")
            self.events.emit("notification", "缓存已完成，正在从原进度继续播放", "success")
        finally:
            if self._cache_switching_for == source_url:
                self._cache_switching_for = None

    def _wait_for_client(self, result: PlaybackResult) -> PlaybackResult:
        """Use the same start/end protocol for local and online media."""
        if not self.is_playing:
            self.is_playing = True
            self.is_buffering = True
            self.events.emit("now_playing_update")
        return result

    def claim(self, file_path: str) -> None:
        """Mark a file as the one playback owns, before any of it is read.

        This field is what tells a rename request the file is spoken for, so it
        must be set before the first yield point: play_file() sleeps through
        transcoding and gevent serves HTTP requests during those sleeps.
        """
        self.now_playing_filename = file_path

    def start_song(self) -> None:
        """Mark the current song as actively playing.

        Called by Flask route when client connects to stream.
        Idempotent - safe to call multiple times.
        """
        self.is_buffering = False
        self.resume_position = None
        if not self.is_playing:
            logging.info(f"Song starting: {self.now_playing}")
            self.is_playing = True
        self.events.emit("now_playing_update")

    def end_song(self, reason: str | None = None) -> None:
        """End the current song and clean up resources.

        Args:
            reason: Optional reason for ending (e.g., 'complete', 'skip', 'timeout').
        """
        logging.info(f"Song ending: {self.now_playing}")
        if reason:
            logging.info(f"Reason: {reason}")
            if reason not in ("complete", "skip", "transpose"):
                # MSG: Message shown when the song ends abnormally
                self.events.emit("notification", _("Song ended abnormally: %s") % reason, "danger")

        self.reset_now_playing()
        self.stream_manager.kill_ffmpeg()
        # Small delay to ensure FFmpeg fully terminates and file handles close
        # Critical on Raspberry Pi with slow SD cards and hardware encoder cleanup
        time.sleep(0.3)
        delete_tmp_dir()
        logging.debug("Cleanup complete")

        self.events.emit("song_ended", reason)

    def skip(self, log_action: bool = True, reason: str = "skip") -> bool:
        """Skip the currently playing song.

        Args:
            log_action: Whether to log and notify about the skip.
            reason: End reason passed to listeners. Callers that end the stream
                without ending the performance (a transpose restarts the same
                song in a new key) pass their own, so play history can tell the
                difference between a real skip and a restart.

        Returns:
            True if a song was skipped, False if nothing playing.
        """
        if self.is_playing:
            if log_action:
                # MSG: Message shown after the song is skipped, will be followed by song name
                self.events.emit("notification", _("Skip: %s") % self.now_playing, "info")
            self.end_song(reason=reason)
            return True
        else:
            logging.warning("Tried to skip, but no file is playing!")
            return False

    def pause(self) -> bool:
        """Toggle pause state of the current song.

        Returns:
            True if successful, False if nothing playing.
        """
        if self.is_playing:
            if self.is_paused:
                # MSG: Message shown after the song is resumed, will be followed by song name
                self.events.emit("notification", _("Resume: %s") % self.now_playing, "info")
            else:
                # MSG: Message shown after the song is paused, will be followed by song name
                self.events.emit("notification", _("Pause: %s") % self.now_playing, "info")
            self.is_paused = not self.is_paused
            self.events.emit("now_playing_update")
            return True
        else:
            logging.warning("Tried to pause, but no file is playing!")
            return False

    def media_key(self) -> str | None:
        if not self.now_playing_filename:
            return None
        import hashlib

        from pikaraoke.lib.bilibili import media_id
        from pikaraoke.lib.library_scanner import _extract_youtube_id

        path = self.now_playing_filename
        return (
            media_id(path)
            or _extract_youtube_id(path)
            or hashlib.sha256(path.encode("utf-8")).hexdigest()[:20]
        )

    def get_now_playing(self) -> dict[str, str | int | float | bool | None]:
        """Get the current playback state.

        Returns:
            Dictionary with now playing information.
        """
        return {
            "now_playing": self.now_playing,
            "now_playing_user": self.now_playing_user,
            "now_playing_duration": self.now_playing_duration,
            "now_playing_transpose": self.now_playing_transpose,
            "now_playing_url": self.now_playing_url,
            "now_playing_subtitle_url": self.now_playing_subtitle_url,
            "now_playing_position": self.now_playing_position,
            "is_paused": self.is_paused,
            "is_buffering": self.is_buffering,
            "media_key": self.media_key(),
            "resume_position": self.resume_position,
        }

    def reset_now_playing(self) -> None:
        """Reset all now playing state to defaults."""
        self.now_playing = None
        self.now_playing_filename = None
        self.now_playing_user = None
        self.now_playing_url = None
        self.now_playing_subtitle_url = None
        self.is_paused = True
        self.is_playing = False
        self.now_playing_transpose = 0
        self.now_playing_duration = None
        self.now_playing_position = None
        self.is_buffering = False
        self.resume_position = None
        self.cached_file = None
        self.expected_duration = None
        self._cache_switching_for = None

    def log_output(self) -> None:
        """Log any pending FFmpeg output."""
        self.stream_manager.log_ffmpeg_output()
