"""Last.fm-Quelle: Hörer- und Abspielzahlen über artist.getInfo."""

from __future__ import annotations

import difflib
import os
import sys
import time

import httpx
from dotenv import load_dotenv

from festival_score import USER_AGENT
from festival_score.cache import MetricsCache, normalize_name
from festival_score.models import ArtistMetrics

API_URL = "https://ws.audioscrobbler.com/2.0/"
MIN_INTERVAL = 0.25  # Sekunden zwischen Requests
RATE_LIMIT_RETRIES = 3
RATE_LIMIT_WAIT = 2.0
MATCH_THRESHOLD = 0.8
ERR_NOT_FOUND = 6
ERR_RATE_LIMIT = 29


class LastfmError(RuntimeError):
    def __init__(self, code: int, message: str):
        super().__init__(f"Last.fm-Fehler {code}: {message}")
        self.code = code


def names_match(query: str, matched: str) -> bool:
    """Plausibilitätsprüfung gegen Fehlzuordnung (Ähnlichkeit >= 0.8 oder nur 'the '-Präfix verschieden)."""
    q, m = normalize_name(query), normalize_name(matched)

    def strip_the(s: str) -> str:
        return s[4:] if s.startswith("the ") else s

    if q == m or strip_the(q) == strip_the(m):
        return True
    return difflib.SequenceMatcher(None, q, m).ratio() >= MATCH_THRESHOLD


def _to_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class LastfmSource:
    name = "lastfm"

    def __init__(
        self,
        client: httpx.Client | None = None,
        cache: MetricsCache | None = None,
        api_key: str | None = None,
        retry_wait: float = RATE_LIMIT_WAIT,
        min_interval: float = MIN_INTERVAL,
    ):
        if api_key is None:
            load_dotenv()
            api_key = os.environ.get("LASTFM_API_KEY")
        if not api_key:
            raise RuntimeError(
                "LASTFM_API_KEY fehlt. Key unter https://www.last.fm/api/account/create erzeugen "
                "und in .env eintragen (siehe .env.example)."
            )
        self.api_key = api_key
        self.client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=15.0)
        self.cache = cache if cache is not None else MetricsCache(self.name)
        self.retry_wait = retry_wait
        self.min_interval = min_interval
        self._last_request = 0.0

    def _request(self, artist_name: str) -> dict:
        params = {
            "method": "artist.getinfo",
            "artist": artist_name,
            "autocorrect": "1",
            "api_key": self.api_key,
            "format": "json",
        }
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            wait = self.min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            resp = self.client.get(API_URL, params=params, headers={"User-Agent": USER_AGENT})
            self._last_request = time.monotonic()
            try:
                data = resp.json()
            except ValueError:
                resp.raise_for_status()
                raise LastfmError(0, "Antwort ist kein JSON")
            if isinstance(data, dict) and "error" in data:
                code = int(data["error"])
                if code == ERR_RATE_LIMIT and attempt < RATE_LIMIT_RETRIES:
                    time.sleep(self.retry_wait)
                    continue
                raise LastfmError(code, str(data.get("message", "")))
            resp.raise_for_status()
            return data
        raise AssertionError("unreachable")

    def _fetch_uncached(self, artist_name: str) -> ArtistMetrics | None:
        try:
            data = self._request(artist_name)
        except LastfmError as e:
            if e.code == ERR_NOT_FOUND:
                return None
            raise
        artist = data.get("artist") or {}
        matched = artist.get("name")
        if not matched or not names_match(artist_name, matched):
            return None
        stats = artist.get("stats") or {}
        # Doku-Beispiel nennt "plays", die JSON-API liefert "playcount"
        playcount = _to_int(stats.get("playcount", stats.get("plays")))
        return ArtistMetrics(
            query_name=artist_name,
            source=self.name,
            values={"listeners": _to_int(stats.get("listeners")), "playcount": playcount},
            source_id=artist.get("mbid") or None,
            matched_name=matched,
        )

    def fetch(self, artist_name: str) -> ArtistMetrics | None:
        hit, cached = self.cache.get(artist_name)
        if hit:
            return cached
        result = self._fetch_uncached(artist_name)
        self.cache.put(artist_name, result)
        return result

    def save(self) -> None:
        self.cache.save()


def main(argv: list[str] | None = None) -> int:
    names = sys.argv[1:] if argv is None else argv
    if not names:
        print('Aufruf: python -m festival_score.sources.lastfm "Muse" "Kontra K"', file=sys.stderr)
        return 2
    try:
        source = LastfmSource()
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1
    for name in names:
        m = source.fetch(name)
        if m is None:
            print(f"{name}: kein Treffer")
        else:
            v = m.values
            print(f"{name} -> {m.matched_name}: listeners={v['listeners']} playcount={v['playcount']} mbid={m.source_id}")
    source.save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
