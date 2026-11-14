from unittest.mock import MagicMock, patch
import pytest
from pikaraoke.lib import bilibili_preview as preview


@pytest.fixture(autouse=True)
def clean_cache():
    preview._streams.clear()
    yield
    preview._streams.clear()


@pytest.mark.parametrize(
    "url",
    [
        "http://example.bilivideo.com/a.mp4",
        "https://127.0.0.1/a",
        "https://bilivideo.com.evil.test/a",
        "https://x.bilivideo.com:8443/a",
    ],
)
def test_preview_rejects_untrusted_host(url):
    with pytest.raises(ValueError):
        preview.validate_media_url(url)


def test_preview_resolves_requested_part_and_caches(tmp_path):
    session = MagicMock()
    metadata = MagicMock()
    metadata.json.return_value = {
        "code": 0,
        "data": {"bvid": "BV1xx411c7mD", "pages": [{"cid": 11}, {"cid": 22}]},
    }
    playback = MagicMock()
    playback.json.return_value = {
        "code": 0,
        "data": {"durl": [{"url": "https://video.bilivideo.com/test.mp4"}]},
    }
    session.get.side_effect = [metadata, playback]
    with (
        patch("pikaraoke.lib.bilibili_preview.web_session") as factory,
        patch("pikaraoke.lib.bilibili_preview.cookie_path", return_value=tmp_path / "none"),
    ):
        factory.return_value.__enter__.return_value = session
        url = "https://www.bilibili.com/video/BV1xx411c7mD?p=2"
        assert preview.resolve_preview(url).endswith("test.mp4")
        assert preview.resolve_preview(url).endswith("test.mp4")
    assert session.get.call_count == 2
    assert session.get.call_args.kwargs["params"]["cid"] == 22


def test_relay_preserves_range_and_closes_stream():
    from tests.conftest import make_route_app
    from pikaraoke.routes.search import search_bp

    upstream = MagicMock(status_code=206)
    upstream.headers = {
        "Content-Type": "video/mp4",
        "Content-Length": "4",
        "Content-Range": "bytes 0-3/100",
        "Accept-Ranges": "bytes",
    }
    upstream.iter_content.return_value = iter([b"test"])
    app = make_route_app(search_bp, [])
    with patch("pikaraoke.lib.bilibili_preview.open_preview", return_value=upstream) as open_stream:
        response = app.test_client().get(
            "/bilibili-preview?url=https://www.bilibili.com/video/BV1xx411c7mD?p=1",
            headers={"Range": "bytes=0-3"},
        )
        assert response.status_code == 206
        assert response.data == b"test"
        assert response.headers["Content-Range"] == "bytes 0-3/100"
        assert open_stream.call_args.args[1] == "bytes=0-3"
        upstream.close.assert_called_once()
