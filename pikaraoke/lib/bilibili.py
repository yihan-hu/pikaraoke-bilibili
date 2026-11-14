"""Bilibili extraction and local credentials, shared by web and download worker."""

import http.cookiejar
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from pikaraoke.lib.get_platform import get_data_directory


def cookie_path() -> Path:
    return Path(get_data_directory()) / "bilibili-cookies.txt"


def cookie_args() -> list[str]:
    return ["--cookies", str(cookie_path())] if cookie_path().exists() else []


def normalize_url(url: str) -> str:
    """Resolve supported public video links; never accept arbitrary extractor URLs."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme not in ("http", "https")
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 80, 443)
    ):
        raise ValueError("请输入 Bilibili 或 YouTube 视频链接")
    if host == "b23.tv":
        import requests

        response = requests.get(url, allow_redirects=False, timeout=10)
        target = response.headers.get("Location", "")
        if urlparse(target).hostname not in ("www.bilibili.com", "bilibili.com"):
            raise ValueError("无效的 B站短链接")
        return normalize_url(target)
    if host in ("www.bilibili.com", "bilibili.com", "m.bilibili.com"):
        match = re.fullmatch(r"/video/(BV[0-9A-Za-z]+|av[0-9]+)/?", parsed.path)
        if not match:
            raise ValueError("仅支持 B站视频链接")
        part = int(parse_qs(parsed.query).get("p", ["1"])[0])
        if part < 1:
            raise ValueError("分P编号无效")
        return f"https://www.bilibili.com/video/{match[1]}?p={part}"
    if host in ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"):
        video_id = (
            parsed.path.strip("/")
            if host == "youtu.be"
            else parse_qs(parsed.query).get("v", [""])[0]
        )
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise ValueError("无效的 YouTube 视频链接")
        return f"https://www.youtube.com/watch?v={video_id}"
    raise ValueError("不支持此网站")


def media_id(url: str) -> str | None:
    parsed = urlparse(url)
    match = re.search(r"/video/(BV[0-9A-Za-z]+)", parsed.path)
    if parsed.hostname == "www.bilibili.com" and match:
        return f"bilibili-{match[1]}-p{parse_qs(parsed.query).get('p', ['1'])[0]}"
    return None


def extract(target: str, flat: bool = False, playlist: bool = False) -> dict:
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--dump-single-json",
        "--skip-download",
        "--yes-playlist" if playlist else "--no-playlist",
    ]
    if flat:
        cmd += ["--flat-playlist"]
    cmd += cookie_args() + [target]
    result = subprocess.run(cmd, capture_output=True, timeout=90)
    if result.returncode:
        raise ValueError("B站请求失败，请检查网络或重新导入 cookies")
    return json.loads(result.stdout)


def details(url: str) -> dict:
    url = normalize_url(url)
    import requests

    jar = http.cookiejar.MozillaCookieJar(str(cookie_path()))
    if cookie_path().exists():
        jar.load(ignore_discard=True, ignore_expires=True)
    video = urlparse(url).path.strip("/").split("/")[1]
    query = {"bvid": video} if video.startswith("BV") else {"aid": video[2:]}
    response = requests.get(
        "https://api.bilibili.com/x/web-interface/view",
        params=query,
        cookies=jar,
        headers={"Referer": "https://www.bilibili.com/", "User-Agent": "Mozilla/5.0"},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        raise ValueError("无法读取视频，请检查链接或 cookies")
    info = payload["data"]
    entries = info.get("pages") or []
    bvid = info["bvid"]
    if not entries:
        raise ValueError("没有可用分P")
    return {
        "title": info.get("title", ""),
        "parts": [
            {
                "part": i,
                "title": entry.get("part", f"P{i}"),
                "duration": entry.get("duration"),
                "url": f"https://www.bilibili.com/video/{bvid}?p={i}",
            }
            for i, entry in enumerate(entries, 1)
        ],
    }


def save_cookies(content: str) -> None:
    """Validate in memory before replacing credentials outside the repository."""
    import tempfile

    path = cookie_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
        jar = http.cookiejar.MozillaCookieJar(temporary)
        try:
            jar.load(ignore_discard=True, ignore_expires=True)
        except http.cookiejar.LoadError as error:
            raise ValueError("Invalid Netscape cookies") from error
        if not any(c.domain.lstrip(".") == "bilibili.com" and c.name == "SESSDATA" for c in jar):
            raise ValueError("需要包含 B站 SESSDATA 的 Netscape cookies 文件")
        for cookie in list(jar):
            if cookie.domain.lstrip(".") != "bilibili.com":
                jar.clear(cookie.domain, cookie.path, cookie.name)
        jar.save(ignore_discard=True, ignore_expires=True)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
