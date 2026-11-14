from unittest.mock import MagicMock, patch
from flask import Flask, request
from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.preference_manager import PreferenceManager
from pikaraoke.lib.playback_controller import PlaybackController, PlaybackResult
from pikaraoke.lib.download_manager import DownloadManager

URL = "https://www.bilibili.com/video/BV1xx411c7mD?p=1"


def test_online_loading_keeps_song_without_waiting_for_network(tmp_path):
    events = EventSystem()
    pc = PlaybackController(
        PreferenceManager(config_file_path=str(tmp_path / "config.ini")),
        events,
        lambda value, **kw: value,
    )
    with patch(
        "pikaraoke.lib.bilibili.web_session",
        side_effect=AssertionError("No network before reserving the turn"),
    ):
        result = pc.play_online(URL, "Singer", "Song")
    assert result.success and pc.is_playing and pc.is_buffering
    assert pc.get_now_playing()["now_playing"] == "Song"
    assert pc.get_now_playing()["is_buffering"] is True


def test_completed_cache_continues_same_song_at_last_position(tmp_path):
    events = EventSystem()
    ended = []
    events.on("song_ended", lambda *args: ended.append(args))
    pc = PlaybackController(
        PreferenceManager(config_file_path=str(tmp_path / "config.ini")),
        events,
        lambda value, **kw: value,
    )
    pc.play_online(URL, "Singer", "Song")
    source = pc.now_playing_url
    pc.now_playing_position = 73.25
    cached = MagicMock()
    cached.play_file.return_value = PlaybackResult(
        success=True, stream_url="/stream/local.m3u8", duration=200
    )
    with patch("pikaraoke.lib.playback_controller.StreamManager", return_value=cached):
        pc._resume_cached(source, "/songs/cached.mp4")
    assert pc.now_playing == "Song" and pc.now_playing_user == "Singer"
    assert pc.now_playing_position == 73.25 and pc.resume_position == 73.25
    assert pc.now_playing_url == "/stream/local.m3u8" and pc.is_playing
    assert not ended


def test_stale_player_cannot_end_current_song():
    from pikaraoke.routes import socket_events

    handlers = {}
    sio = MagicMock()

    def register(name):
        def decorator(function):
            handlers[name] = function
            return function

        return decorator

    sio.on.side_effect = register
    socket_events.setup_socket_events(sio)
    k = MagicMock()
    k.playback_controller.now_playing_url = "/current"
    app = Flask(__name__)
    with (
        app.test_request_context("/"),
        patch.object(socket_events, "master_splash_id", "master"),
        patch.object(socket_events, "get_karaoke_instance", return_value=k),
    ):
        request.sid = "slave"
        handlers["end_song"]({"stream_url": "/current", "reason": "complete"})
        request.sid = "master"
        handlers["end_song"]({"stream_url": "/old", "reason": "complete"})
        k.playback_controller.end_song.assert_not_called()
        handlers["end_song"]({"stream_url": "/current", "reason": "complete"})
        k.playback_controller.end_song.assert_called_once_with("complete")


def test_cancel_waiting_download_does_not_remove_online_song(tmp_path):
    queue = MagicMock()
    dm = DownloadManager(
        EventSystem(),
        PreferenceManager(config_file_path=str(tmp_path / "config.ini")),
        MagicMock(),
        queue,
        str(tmp_path),
    )
    with patch("flask_babel._", side_effect=lambda value: value):
        dm.queue_download(URL, False, "Singer", "Song")
    job = dm.pending_downloads[0]
    assert dm.cancel_download(job["id"])
    assert not dm.pending_downloads
    queue.queue_edit.assert_not_called()
    assert job["id"] in dm._cancelled
