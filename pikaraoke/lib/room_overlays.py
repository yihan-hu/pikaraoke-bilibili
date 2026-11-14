"""Lyrics selection and lightweight room-wide screen overlays."""

import re
import time
import uuid

import requests

LYRICS_AGENT = "PiKaraoke-Bilibili/1.0 (https://github.com/yihan-hu/pikaraoke-bilibili)"


def parse_lrc(text: str) -> list[dict]:
    """Keep all timed lines, including multiple timestamps on a line."""
    offset = re.search(r"\[offset:([+-]?\d+)\]", text)
    shift = int(offset[1]) / 1000 if offset else 0
    lines = []
    pattern = r"\[(\d{1,3}):(\d{2})(?:\.(\d{1,3}))?\]"
    for line in text.splitlines():
        content = re.sub(pattern, "", line).strip()
        for match in re.finditer(pattern, line):
            if int(match[2]) >= 60 or not content:
                continue
            seconds = int(match[1]) * 60 + int(match[2]) + float("0." + (match[3] or "0"))
            lines.append({"time": max(0, seconds + shift), "text": content})
    return sorted(lines, key=lambda line: line["time"])


def lyrics_search(query: str) -> list[dict]:
    response = requests.get(
        "https://lrclib.net/api/search",
        params={"q": query},
        headers={"User-Agent": LYRICS_AGENT},
        timeout=15,
    )
    response.raise_for_status()
    return [
        {
            "id": item["id"],
            "title": item.get("trackName", ""),
            "artist": item.get("artistName", ""),
            "duration": item.get("duration"),
            "synced": bool(item.get("syncedLyrics")),
        }
        for item in response.json()[:20]
        if item.get("plainLyrics") or item.get("syncedLyrics")
    ]


def lyrics_fetch(record_id: int) -> dict:
    response = requests.get(
        f"https://lrclib.net/api/get/{record_id}", headers={"User-Agent": LYRICS_AGENT}, timeout=15
    )
    response.raise_for_status()
    return response.json()


class RoomOverlays:
    def __init__(self):
        self.lyrics = {
            "visible": False,
            "mode": "plain",
            "lines": [],
            "plain": [],
            "track_key": None,
            "title": "",
            "position": "bottom",
            "offset": 0,
        }
        self.danmaku_enabled = True
        self._last_sent = {}

    def state(self) -> dict:
        return {"lyrics": self.lyrics, "danmaku_enabled": self.danmaku_enabled}

    def select_lyrics(self, record: dict, track_key: str) -> None:
        synced = parse_lrc((record.get("syncedLyrics") or "")[:50000])
        plain = (record.get("plainLyrics") or "").splitlines()
        if not any(line.strip() for line in plain):
            plain = [line["text"] for line in synced]
        if not plain:
            raise ValueError("没有可显示的歌词")
        self.lyrics = {
            "visible": True,
            "mode": "synced" if synced else "plain",
            "lines": synced,
            "plain": plain,
            "track_key": track_key,
            "title": record.get("trackName", "歌词"),
            "position": self.lyrics["position"],
            "offset": 0,
        }

    def danmaku(self, text: str, name: str, client: str) -> dict:
        if not self.danmaku_enabled:
            raise ValueError("弹幕已关闭")
        text, name = text.strip(), name.strip() or "游客"
        if not text or len(text) > 80 or len(name) > 24:
            raise ValueError("弹幕限80字，名字限24字")
        now = time.monotonic()
        if now - self._last_sent.get(client, -100) < 2:
            raise ValueError("请间隔2秒再发送")
        self._last_sent = {key: value for key, value in self._last_sent.items() if now - value < 60}
        self._last_sent[client] = now
        return {"id": uuid.uuid4().hex, "text": text, "name": name}
