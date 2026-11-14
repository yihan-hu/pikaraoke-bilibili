import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from pikaraoke.lib.bilibili import details, media_id, normalize_url, save_cookies
from pikaraoke.lib.library_scanner import build_song_record
from pikaraoke.lib.youtube_dl import build_ytdl_download_command


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.test/video/BVabc",
        "http://127.0.0.1/",
        "file:///etc/passwd",
        "https://youtube.com.evil.test/watch?v=aaaaaaaaaaa",
        "https://bilibili.com/video/BVabc?p=0",
    ],
)
def test_reject_unsupported(url):
    with pytest.raises(ValueError):
        normalize_url(url)


def test_part_identity_and_scan(tmp_path):
    url = normalize_url("https://bilibili.com/video/BV1xx411c7mD?p=2&tracking=x")
    key = media_id(url)
    assert key == "bilibili-BV1xx411c7mD-p2"
    record = build_song_record(str(tmp_path / ("Song---" + key + ".mp4")))
    assert record["youtube_id"] == key
    cmd = build_ytdl_download_command(url, str(tmp_path))
    assert "--no-playlist" in cmd
    assert any(key in a for a in cmd)


def test_parts_are_separate():
    from unittest.mock import MagicMock

    response = MagicMock()
    response.json.return_value = {
        "code": 0,
        "data": {
            "bvid": "BV1xx411c7mD",
            "title": "Album",
            "pages": [{"part": "One"}, {"part": "Two"}],
        },
    }
    with patch("requests.get", return_value=response):
        result = details("https://www.bilibili.com/video/BV1xx411c7mD?p=2")
    assert [p["part"] for p in result["parts"]] == [1, 2]
    assert result["parts"][1]["title"] == "Two"
    assert result["parts"][1]["url"].endswith("?p=2")


def test_cookie_validation_is_atomic(tmp_path):
    target = tmp_path / "cookies.txt"
    with patch("pikaraoke.lib.bilibili.cookie_path", return_value=target):
        with pytest.raises(ValueError):
            save_cookies("bad")
        assert not target.exists()
        save_cookies(
            "# Netscape HTTP Cookie File\n.bilibili.com\tTRUE\t/\tTRUE\t2147483647\tSESSDATA\ttest\n"
        )
        before = target.read_bytes()
        with pytest.raises(ValueError):
            save_cookies("bad")
        assert target.read_bytes() == before


def test_default_search_renders_bilibili_and_hides_credentials():
    from unittest.mock import MagicMock

    from pikaraoke.routes.search import search_bp
    from tests.conftest import make_route_app

    app = make_route_app(
        search_bp,
        [
            ("/", "home.home"),
            ("/queue", "queue.queue"),
            ("/browse", "files.browse"),
            ("/info", "info.info"),
        ],
    )
    with (
        patch("pikaraoke.routes.search.get_karaoke_instance", return_value=MagicMock()),
        patch("pikaraoke.routes.search.get_site_name", return_value="PiKaraoke"),
    ):
        response = app.test_client().get("/search")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'value="bilibili" selected' in body
    assert 'id="cookies-file"' not in body


def test_cookie_endpoint_rejects_guest():
    from pikaraoke.routes.search import search_bp
    from tests.conftest import make_route_app

    app = make_route_app(search_bp, [])
    with patch("pikaraoke.lib.current_app.is_admin", return_value=False):
        assert app.test_client().get("/bilibili-cookies").status_code == 403
