"""Account credentials must survive a new process without another QR scan."""

import http.cookiejar
from unittest.mock import MagicMock, patch

import pytest
import requests

from pikaraoke.lib import bilibili_login as login
from pikaraoke.lib.bilibili import search_videos


@pytest.fixture(autouse=True)
def clear_challenges():
    login._challenges.clear()
    yield
    login._challenges.clear()


def test_qr_confirm_persists_and_new_session_reads_account(tmp_path):
    path = tmp_path / "bilibili-cookies.txt"
    session = MagicMock()
    session.cookies = requests.cookies.RequestsCookieJar()
    session.cookies.set("SESSDATA", "test-account-secret", domain=".bilibili.com", path="/")
    response = MagicMock()
    response.json.return_value = {"code": 0, "data": {"code": 0}}
    session.get.return_value = response
    login._challenges["challenge"] = {
        "key": "remote-key",
        "session": session,
        "expires": float("inf"),
        "next_poll": 0,
    }
    with patch("pikaraoke.lib.bilibili.cookie_path", return_value=path):
        result = login.poll_challenge("challenge")
        assert result["status"] == "success"
        assert "challenge" not in login._challenges
        # Reopen from disk as a new process would, with no in-memory challenge.
        jar = http.cookiejar.MozillaCookieJar(str(path))
        jar.load(ignore_discard=True, ignore_expires=True)
        assert any(c.name == "SESSDATA" and c.value == "test-account-secret" for c in jar)
        assert "test-account-secret" not in str(result)


def test_qr_generation_and_waiting_do_not_persist(tmp_path):
    path = tmp_path / "cookies.txt"
    session = MagicMock()
    response = MagicMock()
    response.json.side_effect = [
        {"code": 0, "data": {"url": "https://passport.bilibili.com/test", "qrcode_key": "remote"}},
        {"code": 0, "data": {"code": 86090}},
    ]
    session.get.return_value = response
    with (
        patch("pikaraoke.lib.bilibili_login.web_session", return_value=session),
        patch("pikaraoke.lib.bilibili.cookie_path", return_value=path),
    ):
        challenge = login.create_challenge()
        assert challenge["image"].startswith("data:image/png;base64,")
        assert login.poll_challenge(challenge["id"])["status"] == "scanned"
        assert not path.exists()


def test_expired_qr_does_not_save():
    session = MagicMock()
    login._challenges["old"] = {"session": session, "expires": -1, "next_poll": 0}
    assert login.poll_challenge("old")["status"] == "expired"
    session.close.assert_called_once()


def test_anonymous_search_cleans_titles_and_needs_no_login():
    response = MagicMock()
    response.json.return_value = {
        "code": 0,
        "data": {
            "result": [
                {
                    "bvid": "BV1xx411c7mD",
                    "title": '<em class="keyword">Song</em> &amp; Artist',
                    "pic": "//example.test/img.jpg",
                    "author": "Artist",
                    "duration": "3:20",
                }
            ]
        },
    }
    with patch("pikaraoke.lib.bilibili.web_session") as session:
        session.return_value.__enter__.return_value.get.return_value = response
        results = search_videos("Song")
    assert results[0]["title"] == "Song & Artist"
    assert results[0]["thumbnail"].startswith("https://")


@pytest.mark.parametrize("endpoint", ["/bilibili-login/qr", "/bilibili-login/poll"])
def test_qr_endpoints_require_admin_and_settings_header(endpoint):
    from tests.conftest import make_route_app
    from pikaraoke.routes.search import search_bp

    app = make_route_app(search_bp, [])
    client = app.test_client()
    with patch("pikaraoke.lib.current_app.is_admin", return_value=False):
        assert (
            client.post(endpoint, headers={"X-Pikaraoke-Settings": "1"}, json={}).status_code == 403
        )
    with patch("pikaraoke.lib.current_app.is_admin", return_value=True):
        assert client.post(endpoint, json={}).status_code == 403
