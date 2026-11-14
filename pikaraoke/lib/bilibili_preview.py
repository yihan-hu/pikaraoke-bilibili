"""Resolve public progressive MP4 previews and serve required upstream headers."""

import time
from urllib.parse import parse_qs, urlparse

import requests

from pikaraoke.lib.bilibili import cookie_path, normalize_url, web_session

_streams: dict = {}
HEADERS = {"Referer": "https://www.bilibili.com/", "User-Agent": "Mozilla/5.0"}


def validate_media_url(url: str) -> None:
    """The local relay may only contact Bilibili's video distribution hosts."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    domains = ("bilivideo.com", "bilivideo.cn", "bilivideo.net", "akamaized.net")
    if (
        parsed.scheme != "https"
        or parsed.port not in (None, 443)
        or parsed.username
        or parsed.password
        or not any(host == domain or host.endswith("." + domain) for domain in domains)
    ):
        raise ValueError("Unsupported preview media host")


def resolve_preview(url: str) -> str:
    """Use Bilibili's HTML5 progressive format, including video and audio."""
    url = normalize_url(url)
    parsed = urlparse(url)
    if parsed.hostname != "www.bilibili.com":
        raise ValueError("Expected Bilibili video")
    path = cookie_path()
    key = (url, path.stat().st_mtime_ns if path.exists() else 0)
    now = time.monotonic()
    if key in _streams and _streams[key][0] > now:
        return _streams[key][1]
    video = parsed.path.strip("/").split("/")[1]
    query = {"bvid": video} if video.startswith("BV") else {"aid": video[2:]}
    part = int(parse_qs(parsed.query).get("p", ["1"])[0])
    with web_session() as session:
        response = session.get(
            "https://api.bilibili.com/x/web-interface/view", params=query, timeout=15
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise ValueError("无法读取视频")
        info = payload["data"]
        pages = info.get("pages", [])
        if not 1 <= part <= len(pages):
            raise ValueError("分P不存在")
        response = session.get(
            "https://api.bilibili.com/x/player/playurl",
            params={
                "bvid": info["bvid"],
                "cid": pages[part - 1]["cid"],
                "qn": 32,
                "fnval": 0,
                "fnver": 0,
                "platform": "html5",
            },
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        streams = payload.get("data", {}).get("durl", [])
        if payload.get("code") != 0 or len(streams) != 1:
            raise ValueError("此视频暂不支持直接预览")
        media_url = streams[0]["url"]
        validate_media_url(media_url)
    if len(_streams) >= 32:
        _streams.pop(next(iter(_streams)))
    _streams[key] = (now + 120, media_url)
    return media_url


def open_preview(url: str, byte_range: str | None):
    """Forward Range so the browser can seek without downloading the whole video."""
    media_url = resolve_preview(url)
    headers = dict(HEADERS)
    if byte_range:
        headers["Range"] = byte_range
    response = requests.get(
        media_url, headers=headers, stream=True, timeout=(10, 30), allow_redirects=False
    )
    if response.status_code not in (200, 206, 416):
        response.close()
        raise ValueError("预览视频暂时不可用")
    return response
