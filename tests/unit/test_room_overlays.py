from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pikaraoke.lib.room_overlays import RoomOverlays, parse_lrc
from pikaraoke.routes.room import room_bp
from tests.conftest import make_route_app


def test_lrc_multiple_timestamps_and_plain_fallback():
    lines = parse_lrc("[offset:100]\n[00:01.20][00:03.400]Alpha\n[00:02.00]Beta")
    assert [round(item["time"], 2) for item in lines] == [1.3, 2.1, 3.5]
    room = RoomOverlays()
    room.select_lyrics({"trackName": "Test", "syncedLyrics": "[00:01]Alpha\n[00:02]Beta"}, "media")
    assert room.lyrics["mode"] == "synced"
    assert room.lyrics["plain"] == ["Alpha", "Beta"]
    room.lyrics["mode"] = "plain"
    assert room.state()["lyrics"]["plain"] == ["Alpha", "Beta"]


def test_plain_lyrics_keep_every_line():
    room = RoomOverlays()
    lines = ["Line " + str(index) for index in range(300)]
    room.select_lyrics({"plainLyrics": "\n".join(lines)}, "media")
    assert room.lyrics["plain"] == lines and room.lyrics["mode"] == "plain"


def test_danmaku_keeps_name_and_text_separate_and_limits_rate():
    room = RoomOverlays()
    item = room.danmaku("Hello", "Singer", "client")
    assert item["text"] == "Hello" and item["name"] == "Singer"
    import pytest

    with pytest.raises(ValueError):
        room.danmaku("Again", "Singer", "client")
    room.danmaku_enabled = False
    with pytest.raises(ValueError):
        room.danmaku("No", "Other", "other")


def test_select_then_switch_full_lyrics():
    app = make_route_app(room_bp, [])
    controller = SimpleNamespace(now_playing="Test", get_now_playing=lambda: {"media_key": "track"})
    k = SimpleNamespace(playback_controller=controller, socketio=MagicMock())
    headers = {"X-Pikaraoke-Settings": "1"}
    with (
        patch("pikaraoke.routes.room.get_karaoke_instance", return_value=k),
        patch("pikaraoke.routes.room.is_admin", return_value=True),
    ):
        response = app.test_client().post(
            "/room/lyrics", json={"text": "[00:01]Alpha\n[00:02]Beta"}, headers=headers
        )
        assert response.status_code == 200 and response.json["lyrics"]["mode"] == "synced"
        response = app.test_client().post("/room/lyrics", json={"mode": "plain"}, headers=headers)
        assert response.json["lyrics"]["mode"] == "plain"
        assert response.json["lyrics"]["plain"] == ["Alpha", "Beta"]
        response = app.test_client().post(
            "/room/danmaku", json={"text": "<b>text</b>", "name": "Singer"}
        )
        assert response.status_code == 200
        assert k.socketio.emit.call_args.args[1]["text"] == "<b>text</b>"


def test_non_admin_cannot_change_lyrics_or_clear_danmaku():
    app = make_route_app(room_bp, [])
    with patch("pikaraoke.routes.room.is_admin", return_value=False):
        assert app.test_client().post("/room/lyrics", json={"visible": True}).status_code == 403
        assert (
            app.test_client().post("/room/danmaku/settings", json={"clear": True}).status_code
            == 403
        )
