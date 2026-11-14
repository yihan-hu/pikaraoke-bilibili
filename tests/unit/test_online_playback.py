from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import gevent
import pytest
from gevent.event import Event

from pikaraoke.karaoke import Karaoke
from pikaraoke.lib.download_manager import DownloadManager
from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.playback_controller import PlaybackController
from pikaraoke.lib.preference_manager import PreferenceManager
from pikaraoke.lib.stream_manager import PlaybackResult

URL = "https://www.bilibili.com/video/BV1xx411c7mD?p=1"
KEY = "bilibili-BV1xx411c7mD-p1"


@pytest.mark.parametrize("cached", [False, True])
def test_playback_prefers_complete_cache_at_its_turn(tmp_path, cached):
    path = tmp_path / "cached.mp4"
    if cached:
        path.write_bytes(b"video")
    subject = SimpleNamespace(
        db=MagicMock(), playback_controller=MagicMock(), play_history=MagicMock()
    )
    subject.db.get_paths_by_youtube_ids.return_value = {KEY: str(path)}
    subject.db.get_song_identity.return_value = (None, None)
    subject.playback_controller.play_online.return_value = PlaybackResult(success=True)
    subject.playback_controller.play_file.return_value = PlaybackResult(success=True)
    song = {"file": URL, "title": "Song", "user": "Singer", "semitones": 0}
    result = Karaoke._play_queued_song(subject, song)
    assert result.success
    if cached:
        subject.playback_controller.play_file.assert_called_once_with(str(path), "Singer", 0)
        subject.playback_controller.play_online.assert_not_called()
    else:
        subject.playback_controller.play_online.assert_called_once_with(URL, "Singer", "Song", 0)
        subject.playback_controller.play_file.assert_not_called()
    assert subject.play_history.record_play.call_args.args[1] == KEY


def test_online_player_uses_local_relay_and_existing_end_protocol(tmp_path):
    events = EventSystem()
    controller = PlaybackController(
        PreferenceManager(config_file_path=str(tmp_path / "config.ini")),
        events,
        lambda value, **kwargs: value,
    )
    events.on("playback_started", controller.start_song)
    info = {"title": "Song", "parts": [{"part": 1, "title": "Song", "duration": 120}]}
    with (
        patch("pikaraoke.lib.bilibili.details", return_value=info),
        patch(
            "pikaraoke.lib.bilibili_preview.resolve_preview",
            return_value="https://media.bilivideo.com/test.mp4",
        ),
    ):
        result = controller.play_online(URL, "Singer", "Song")
    assert result.success and controller.is_playing
    assert result.stream_url.startswith("/bilibili-preview?url=")
    assert controller.now_playing_user == "Singer"
    assert result.duration is None  # Filled by the player once metadata arrives.


def test_two_workers_run_together_with_separate_progress(tmp_path):
    manager = DownloadManager(
        EventSystem(),
        PreferenceManager(config_file_path=str(tmp_path / "config.ini")),
        MagicMock(),
        MagicMock(),
        str(tmp_path),
    )
    gate = Event()
    started = []
    active = []
    max_active = []

    def execute(request):
        active.append(request["video_url"])
        started.append(request["video_url"])
        max_active.append(len(active))
        manager.active_download["progress"] = 20 if request["video_url"].endswith("1") else 80
        gate.wait(timeout=2)
        assert manager.active_download["url"] == request["video_url"]
        active.remove(request["video_url"])

    manager._execute_download = execute
    with patch("flask_babel._", side_effect=lambda value: value):
        for index in range(3):
            manager.queue_download("https://test/" + str(index))
    manager.start()
    try:
        gevent.sleep(0.03)
        status = manager.get_downloads_status()
        assert len(status["active_downloads"]) == 2
        assert len(status["pending"]) == 1
        assert len({item["url"] for item in status["active_downloads"]}) == 2
        gate.set()
        gevent.sleep(0.03)
        assert len(started) == 3 and max(max_active) == 2
        assert manager.get_downloads_status()["active_downloads"] == []
    finally:
        gate.set()
        manager._worker.kill()


def test_download_finishes_out_of_order_but_queues_in_request_order(tmp_path):
    queue = MagicMock()
    manager = DownloadManager(
        EventSystem(),
        PreferenceManager(config_file_path=str(tmp_path / "config.ini")),
        MagicMock(),
        queue,
        str(tmp_path),
    )
    with patch("flask_babel._", side_effect=lambda value: value):
        manager.queue_download("https://test/first", True, user="First")
        manager.queue_download("https://test/second", True, user="Second")
    first, second = manager.pending_downloads
    manager._complete_for_queue(second, "/second.mp4")
    queue.enqueue.assert_not_called()
    manager._complete_for_queue(first, "/first.mp4")
    assert [call.args[0] for call in queue.enqueue.call_args_list] == ["/first.mp4", "/second.mp4"]


def test_point_uncached_bilibili_queues_online_and_caches_without_second_enqueue():
    from pikaraoke.routes.search import search_bp
    from tests.conftest import make_route_app

    app = make_route_app(search_bp, [])
    k = MagicMock()
    k.db.get_paths_by_youtube_ids.return_value = {}
    k.queue_manager.enqueue.return_value = [True, "Added"]
    with patch("pikaraoke.routes.search.get_karaoke_instance", return_value=k):
        response = app.test_client().post(
            "/download",
            json={"song_url": URL, "song_title": "Song", "song_added_by": "Singer", "queue": True},
        )
    assert response.status_code == 200 and response.json["online"] is True
    k.queue_manager.enqueue.assert_called_once_with(URL, "Singer", title="Song")
    k.download_manager.queue_download.assert_called_once_with(URL, False, "Singer", "Song")
