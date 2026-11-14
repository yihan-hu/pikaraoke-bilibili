from unittest.mock import MagicMock, patch
from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.preference_manager import PreferenceManager
from pikaraoke.lib.queue_manager import QueueManager
from pikaraoke.routes.queue import queue_bp
from tests.conftest import make_route_app


def test_shuffle_keeps_songs_and_metadata_without_skip(tmp_path):
    events = EventSystem()
    emitted = []
    events.on("skip_requested", lambda: emitted.append("skip"))
    events.on("queue_update", lambda: emitted.append("queue"))
    manager = QueueManager(PreferenceManager(config_file_path=str(tmp_path / "config.ini")), events)
    songs = [{"file": str(i), "user": "Singer" + str(i), "semitones": i} for i in range(3)]
    manager.queue = songs[:]
    with patch(
        "pikaraoke.lib.queue_manager.random.shuffle", side_effect=lambda rows: rows.reverse()
    ):
        assert manager.shuffle()
    assert manager.queue == list(reversed(songs))
    assert all(any(item is original for original in songs) for item in manager.queue)
    assert emitted == ["queue"]


def test_shuffle_two_changes_order_even_when_random_returns_same(tmp_path):
    manager = QueueManager(
        PreferenceManager(config_file_path=str(tmp_path / "config.ini")), EventSystem()
    )
    assert not manager.shuffle()
    manager.queue = [{"file": "one"}]
    assert not manager.shuffle()
    manager.queue.append({"file": "two"})
    with patch("pikaraoke.lib.queue_manager.random.shuffle", return_value=None):
        assert manager.shuffle()
    assert [item["file"] for item in manager.queue] == ["two", "one"]


def test_shuffle_route_requires_admin():
    app = make_route_app(queue_bp, [])
    k = MagicMock()
    with (
        patch("pikaraoke.routes.queue.get_karaoke_instance", return_value=k),
        patch("pikaraoke.routes.queue.is_admin", return_value=False),
    ):
        assert app.test_client().post("/queue/shuffle").status_code == 403
    k.queue_manager.shuffle.assert_not_called()
    k.queue_manager.shuffle.return_value = True
    with (
        patch("pikaraoke.routes.queue.get_karaoke_instance", return_value=k),
        patch("pikaraoke.routes.queue.is_admin", return_value=True),
    ):
        response = app.test_client().post("/queue/shuffle")
        import json

        assert json.loads(response.data)["success"] is True


def test_point_song_reports_queue_rejection():
    from pikaraoke.routes.search import search_bp

    app = make_route_app(search_bp, [])
    k = MagicMock()
    k.db.get_paths_by_youtube_ids.return_value = {"bilibili-BV1xx411c7mD-p1": "/songs/test.mp4"}
    k.queue_manager.enqueue.return_value = [False, "Already queued"]
    with patch("pikaraoke.routes.search.get_karaoke_instance", return_value=k):
        response = app.test_client().post(
            "/download",
            json={
                "song_url": "https://www.bilibili.com/video/BV1xx411c7mD",
                "song_title": "Test",
                "song_added_by": "Test",
                "queue": True,
            },
        )
    assert response.status_code == 409
    assert response.json["error"] == "Already queued"
    k.download_manager.queue_download.assert_not_called()
