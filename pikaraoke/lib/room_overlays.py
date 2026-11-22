"""Lyrics selection and lightweight room-wide screen overlays."""

import logging
import re
import time
import uuid
from collections.abc import Callable

import requests
from gevent import spawn

from pikaraoke.lib.metadata_parser import regex_tidy

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


def _search_records(query: str) -> list[dict]:
    response = requests.get(
        "https://lrclib.net/api/search",
        params={"q": query.replace(" - ", " ")},
        headers={"User-Agent": LYRICS_AGENT},
        timeout=15,
    )
    response.raise_for_status()
    return [
        item
        for item in response.json()[:20]
        if item.get("plainLyrics") or item.get("syncedLyrics")
    ]


def _search_summaries(records: list[dict]) -> list[dict]:
    return [
        {
            "id": item["id"],
            "title": item.get("trackName", ""),
            "artist": item.get("artistName", ""),
            "duration": item.get("duration"),
            "synced": bool(item.get("syncedLyrics")),
        }
        for item in records
    ]


def lyrics_search(query: str) -> list[dict]:
    return _search_summaries(_search_records(query))


def lyrics_fetch(record_id: int) -> dict:
    response = requests.get(
        f"https://lrclib.net/api/get/{record_id}", headers={"User-Agent": LYRICS_AGENT}, timeout=15
    )
    response.raise_for_status()
    return response.json()


class RoomOverlays:
    def __init__(self):
        self.lyrics = self._empty_lyrics("bottom")
        self.auto_search = True
        self.track_key: str | None = None
        self._lookup = self._empty_lookup(None, "")
        self.danmaku_enabled = True
        self._last_sent = {}

    @staticmethod
    def _empty_lyrics(position: str) -> dict:
        return {
            "visible": False,
            "mode": "plain",
            "lines": [],
            "plain": [],
            "track_key": None,
            "id": None,
            "title": "",
            "position": position,
            "offset": 0,
        }

    @staticmethod
    def _empty_lookup(track_key: str | None, query: str) -> dict:
        return {"track_key": track_key, "query": query, "status": "idle", "results": []}

    def state(self) -> dict:
        return {
            "lyrics": self.lyrics.copy(),
            "lyrics_auto_search": self.auto_search,
            "lyrics_lookup": (
                self._lookup.copy() if self.track_key else self._empty_lookup(None, "")
            ),
            "danmaku_enabled": self.danmaku_enabled,
        }

    def set_playing_track(
        self, track_key: str | None, title: str | None, publish: Callable[[], None]
    ) -> None:
        """Look up each new song and preserve the selection when transposing it."""
        if not title:
            track_key = None
        if track_key == self.track_key:
            return
        self.track_key = track_key
        if track_key and title and track_key != self._lookup["track_key"]:
            query = (regex_tidy(title) or title)[:200]
            self._lookup = self._empty_lookup(track_key, query)
            self.lyrics = self._empty_lyrics(self.lyrics["position"])
        if track_key:
            self._start_lyrics_search(publish)
        publish()

    def set_auto_search(self, enabled: bool, publish: Callable[[], None]) -> None:
        """Start or cancel the automatic lookup for the current track."""
        self.auto_search = enabled
        if enabled and self.track_key:
            self._start_lyrics_search(publish)
        elif not enabled and self._lookup["status"] == "searching":
            self._lookup = self._empty_lookup(self._lookup["track_key"], self._lookup["query"])

    def _start_lyrics_search(self, publish: Callable[[], None]) -> None:
        lookup = self._lookup
        if not self.auto_search or lookup["status"] not in ("idle", "error"):
            return
        lookup["status"] = "searching"
        spawn(self._resolve_lyrics, lookup, publish)

    def _resolve_lyrics(self, lookup: dict, publish: Callable[[], None]) -> None:
        try:
            records = _search_records(lookup["query"])
            # A newer song or a disabled search invalidates this request.
            if lookup is not self._lookup:
                return
            records.sort(key=lambda record: not record.get("syncedLyrics"))
            lookup["results"] = _search_summaries(records)
            if records and self.lyrics["track_key"] != lookup["track_key"]:
                self.select_lyrics(records[0], lookup["track_key"])
            lookup["status"] = "ready"
        except (OSError, ValueError, KeyError) as error:
            logging.warning("Automatic lyrics search failed for %r: %s", lookup["query"], error)
            lookup["status"] = "error"
        if lookup is self._lookup and self.track_key:
            publish()

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
            "id": record.get("id"),
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
