"""Find lyrics for noisy karaoke video titles and fall back to public lyrics sources."""

import base64
import json
import logging
import os
import re
import unicodedata
from difflib import SequenceMatcher
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import requests

from pikaraoke.lib.metadata_parser import regex_tidy

LYRICS_AGENT = "PiKaraoke-Bilibili/1.0 (https://github.com/yihan-hu/pikaraoke-bilibili)"
TIMEOUT = 5
_HAN = re.compile(r"[\u3400-\u9fff]")
_CREDITS = re.compile(r"(?:\s+(?:伴奏|原[版调調]|和[声聲]|合[声聲]|无人声|無人聲|消音|卡拉OK))+$", re.I)
_WORD = re.compile(r"[^\W_]+")
_TIMESTAMP = re.compile(r"(?m)^\s*\[\d{1,3}:\d{2}(?:\.\d{1,3})?\]")
_FOLLOWLYRICS_HOSTS = {"zh.followlyrics.com", "www.followlyrics.com", "followlyrics.com"}


def clean_lyrics_query(title: str) -> str:
    title = unicodedata.normalize("NFKC", title)
    return _CREDITS.sub("", regex_tidy(title) or title).strip()[:200]


def _words(text: str | None) -> list[str]:
    if not isinstance(text, str):
        return []
    return _WORD.findall(unicodedata.normalize("NFKC", text).casefold())


def _similarity(name: str, words: list[str]) -> float:
    name_words = _words(name)
    key = "".join(name_words)
    if not key:
        return 0
    spans = ["".join(words[i : i + len(name_words)]) for i in range(len(words))]
    if key in spans:
        return 1
    if len(key) >= 2 and _HAN.search(key) and any(key in word for word in words):
        return 0.85
    if len(key) < 4:
        return 0
    return max(
        (SequenceMatcher(None, key, span, autojunk=False).ratio() for span in spans),
        default=0.0,
    )


def _match_score(record: dict, words: list[str]) -> float:
    title = _similarity(record.get("trackName") or "", words)
    if title < 0.8:
        return 0
    artist = _similarity(record.get("artistName") or "", words)
    return title + (0.35 * artist if artist >= 0.8 else 0)


def rank_lyrics(records: list[dict], query: str) -> list[dict]:
    """Require a plausible title and favor a matching artist before choosing a version."""
    words = _words(query)
    matches = [(score, record) for record in records if (score := _match_score(record, words))]
    matches.sort(key=lambda match: (match[0], bool(match[1].get("syncedLyrics"))), reverse=True)
    return [record for _, record in matches]


def _query_variants(query: str) -> tuple[str, list[dict]]:
    parts = re.split(r"\s+-\s+|\s*[|/]\s*", query, maxsplit=1)
    if len(parts) == 1 and _HAN.search(query):
        parts = query.split()[:2]
    if not parts:
        return "", []
    short = " ".join(parts).strip()
    variants = [{"q": short}]
    if len(parts) == 2:
        variants.extend({"track_name": part} for part in parts if part)
    elif re.fullmatch(r"[\u3400-\u9fff]{5,}", short):
        variants.extend({"track_name": short[:length]} for length in (3, 2))
    return short, variants


def search_lyrics_records(query: str) -> list[dict]:
    """Try a compact query and title-only searches without treating album tags as required."""
    _, variants = _query_variants(query)
    records = {}
    error = None
    words = _words(query)
    for params in variants:
        try:
            response = requests.get(
                "https://lrclib.net/api/search",
                params=params,
                headers={"User-Agent": LYRICS_AGENT},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
        except OSError as exc:
            error = exc
            break
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Unexpected LRCLIB search response")
        for item in payload[:20]:
            if isinstance(item, dict) and item.get("id") and (
                item.get("plainLyrics") or item.get("syncedLyrics")
            ):
                records.setdefault(item["id"], item)
        matches = rank_lyrics(list(records.values()), query)
        if matches and matches[0].get("syncedLyrics") and _match_score(matches[0], words) >= 1.25:
            break
    if not records and error:
        raise error
    return list(records.values())


class _LyricPage(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links = []
        self.json_ld = []
        self._script = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        if tag == "script" and attrs.get("type") == "application/ld+json":
            self._script = []

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script is not None:
            self.json_ld.append("".join(self._script))
            self._script = None


def _google_links(query: str) -> list[str]:
    keyword = "歌词" if _HAN.search(query) else "lyrics"
    query = f"{query} {keyword} site:followlyrics.com"
    key = os.environ.get("MAOKARAOKE_SERPER_API_KEY")
    if key:
        response = requests.post(
            "https://google.serper.dev/search",
            json={"q": query},
            headers={"X-API-KEY": key},
            timeout=TIMEOUT,
            allow_redirects=False,
        )
        response.raise_for_status()
        return [item["link"] for item in response.json().get("organic", []) if item.get("link")]

    response = requests.get(
        "https://www.google.com/search",
        params={"q": query},
        headers={"User-Agent": LYRICS_AGENT},
        timeout=TIMEOUT,
        allow_redirects=False,
    )
    response.raise_for_status()
    if response.is_redirect:
        return []
    page = _LyricPage()
    page.feed(response.text)
    links = []
    for href in page.links:
        parsed = urlsplit(href)
        if parsed.path == "/url" and not parsed.netloc:
            query_args = parse_qs(parsed.query)
            href = (query_args.get("q") or query_args.get("url") or [""])[0]
        links.append(href)
    return links


def _followlyrics(query: str, original_query: str) -> dict | None:
    pages = {}
    for link in _google_links(query):
        url = urlsplit(link)
        if url.hostname not in _FOLLOWLYRICS_HOSTS:
            continue
        path = re.fullmatch(r"(/lyrics/\d+/[\w%-]+)(?:/amp)?/?", url.path)
        if path:
            pages.setdefault(f"https://{url.hostname}{path[1]}/amp", None)
    for url in list(pages)[:3]:
        try:
            response = requests.get(url, timeout=TIMEOUT, allow_redirects=False)
            response.raise_for_status()
            page = _LyricPage()
            page.feed(response.text)
            for block in page.json_ld:
                data = json.loads(block)
                if not isinstance(data, dict) or not isinstance(data.get("lyrics"), dict):
                    continue
                lyrics = data["lyrics"].get("text")
                composer = data.get("composer") or {}
                record = {
                    "id": "web",
                    "trackName": data.get("name"),
                    "artistName": composer.get("name") if isinstance(composer, dict) else "",
                    "source": "Google / FollowLyrics",
                }
                if isinstance(lyrics, str) and lyrics.strip() and rank_lyrics([record], original_query):
                    field = "syncedLyrics" if _TIMESTAMP.search(lyrics) else "plainLyrics"
                    record[field] = lyrics[:50000]
                    return record
        except (OSError, ValueError) as error:
            logging.debug("Could not read lyrics page %s: %s", url, error)
    return None


def _kugou(query: str, original_query: str) -> dict | None:
    root = "https://lyrics.kugou.com"
    response = requests.get(
        f"{root}/search",
        params={"ver": 1, "man": "yes", "client": "pc", "keyword": query},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    candidates = payload.get("candidates") or []
    records = [
        {
            "trackName": item.get("song"),
            "artistName": item.get("singer"),
            "duration": int(item.get("duration") or 0),
            "candidate": item,
        }
        for item in candidates
        if item.get("id") and item.get("accesskey")
    ]
    records.sort(key=lambda item: item["duration"], reverse=True)
    matches = rank_lyrics(records, original_query)
    if not matches:
        return None
    record = matches[0]
    candidate = record.pop("candidate")
    response = requests.get(
        f"{root}/download",
        params={
            "ver": 1,
            "client": "pc",
            "fmt": "lrc",
            "charset": "utf8",
            "id": candidate["id"],
            "accesskey": candidate["accesskey"],
        },
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    content = response.json().get("content")
    if not content:
        return None
    lyrics = base64.b64decode(content, validate=True).decode("utf-8-sig")
    if not lyrics.strip():
        return None
    field = "syncedLyrics" if _TIMESTAMP.search(lyrics) else "plainLyrics"
    return {
        **record,
        "id": "web",
        "source": "酷狗音乐",
        "duration": record["duration"] / 1000,
        field: lyrics[:50000],
    }


def fetch_web_lyrics(query: str) -> dict | None:
    short, _ = _query_variants(query)
    if not short:
        return None
    for provider in (_followlyrics, _kugou):
        try:
            record = provider(short, query)
            if record:
                return record
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            logging.warning("Lyrics fallback %s failed: %s", provider.__name__, error)
    return None
