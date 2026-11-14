"""The seek control jumps the current song and tells every player where to go."""

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from pikaraoke.routes.controller import controller_bp


@pytest.fixture
def karaoke():
    k = MagicMock()
    k.playback_controller.now_playing_url = "/stream/song.mp4"
    k.playback_controller.now_playing_duration = 120
    k.playback_controller.now_playing_position = 10.0
    return k


def post(karaoke, path):
    app = Flask(__name__)
    app.register_blueprint(controller_bp)
    with (
        patch("pikaraoke.routes.controller.get_karaoke_instance", return_value=karaoke),
        patch("pikaraoke.routes.controller.broadcast_event") as broadcast,
    ):
        response = app.test_client().post(path)
    return response, broadcast


def test_seek_moves_position_and_broadcasts(karaoke):
    response, broadcast = post(karaoke, "/seek/42.5")
    assert response.status_code == 200
    assert response.get_json()["position"] == 42.5
    assert karaoke.playback_controller.now_playing_position == 42.5
    broadcast.assert_called_once_with("seek", 42.5)


@pytest.mark.parametrize(("raw", "expected"), [("-5", 0.0), ("500", 119.5)])
def test_seek_clamps_to_the_song(karaoke, raw, expected):
    response, broadcast = post(karaoke, f"/seek/{raw}")
    assert response.get_json()["position"] == expected
    broadcast.assert_called_once_with("seek", expected)


@pytest.mark.parametrize("raw", ["abc", "nan", "inf"])
def test_seek_rejects_non_numbers(karaoke, raw):
    response, broadcast = post(karaoke, f"/seek/{raw}")
    assert response.status_code == 400
    broadcast.assert_not_called()


def test_seek_needs_a_playing_song(karaoke):
    karaoke.playback_controller.now_playing_url = None
    response, broadcast = post(karaoke, "/seek/30")
    assert response.status_code == 409
    broadcast.assert_not_called()
