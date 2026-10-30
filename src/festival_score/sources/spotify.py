"""Spotify-Quelle: popularity/followers aus der Web-API, monatliche Hörer von der öffentlichen Künstlerseite.

Hinweis: Seit der Feb-2026-Änderung liefert die Web-API für Development-Mode-Apps
`popularity` und `followers` nicht mehr; dann bleiben die Werte None.
"""

from __future__ import annotations

import argparse
import difflib
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from festival_score import USER_AGENT
from festival_score.cache import MetricsCache, normalize_name
from festival_score.models import ArtistMetrics

TOKEN_URL = "https://accounts.spotify.com/api/token"
SEARCH_URL = "https://api.spotify.com/v1/search"
ARTIST_PAGE_URL = "https://open.spotify.com/artist/{id}"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
MAX_ATTEMPTS = 3
MAX_RETRY_WAIT = 60  # längeres Retry-After = Sperre, nicht kurz abwarten
MIN_SIMILARITY = 0.9


class SpotifyAuthError(RuntimeError):
    """Credentials fehlen oder wurden abgelehnt."""


class SpotifyRateLimited(RuntimeError):
    """Spotify sperrt die App für längere Zeit (Retry-After > MAX_RETRY_WAIT)."""

    def __init__(self, retry_after: float):
        super().__init__(f"Spotify-API gesperrt, Retry-After {retry_after:.0f} s (~{retry_after / 3600:.1f} h)")
        self.retry_after = retry_after


# --- reine Funktionen -------------------------------------------------------


def _loose_name(name: str) -> str:
    """Aggressiv normalisiert: ohne "the "-Präfix, "&" -> "and", ohne Satzzeichen."""
    s = normalize_name(name).replace("&", " and ")
    s = re.sub(r"[^\w\s]", "", s)
    s = " ".join(s.split())
    if s.startswith("the "):
        s = s[4:]
    return s


def _rank_key(item: dict[str, Any]) -> tuple[int, int]:
    followers = item.get("followers")
    total = followers.get("total") if isinstance(followers, dict) else None
    return (item.get("popularity") or 0, total or 0)


def pick_best_match(query: str, items: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Wählt aus den Suchtreffern den passenden Künstler oder None.

    1. exakte Namensgleichheit (normalize_name); mehrere -> höchste popularity/followers
    2. sonst "lockere" Gleichheit (the/&/Satzzeichen) oder difflib-Ratio >= 0.9
    """
    items = [i for i in items if i and i.get("name")]
    q = normalize_name(query)
    exact = [i for i in items if normalize_name(i["name"]) == q]
    if exact:
        return max(exact, key=_rank_key)  # max behält bei Gleichstand den ersten (Spotify-Relevanz)

    ql = _loose_name(query)
    scored = []
    for i in items:
        nl = _loose_name(i["name"])
        ratio = 1.0 if nl == ql else difflib.SequenceMatcher(None, ql, nl).ratio()
        if ratio >= MIN_SIMILARITY:
            scored.append((ratio, _rank_key(i), i))
    if not scored:
        return None
    return max(scored, key=lambda t: (t[0], t[1]))[2]


_LISTENERS_RE = re.compile(r"([\d][\d.,]*)\s*([KMB])?\s+monthly\s+listeners", re.IGNORECASE)
_MULTIPLIER = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}


def parse_monthly_listeners(html: str) -> int | None:
    """Liest "Artist · 22.3M monthly listeners." aus og:description (oder Fallback: ganzer HTML-Text)."""
    try:
        text = None
        meta = BeautifulSoup(html, "html.parser").find("meta", attrs={"property": "og:description"})
        if meta is not None:
            text = meta.get("content")
        m = _LISTENERS_RE.search(text or "") or _LISTENERS_RE.search(html)
        if not m:
            return None
        number, suffix = m.group(1), (m.group(2) or "").upper()
        if suffix:
            return int(round(float(number.replace(",", "")) * _MULTIPLIER[suffix]))
        return int(re.sub(r"[.,]", "", number))  # ohne Suffix sind . und , Tausendertrenner
    except (ValueError, TypeError):
        return None


# --- Quelle -----------------------------------------------------------------


class SpotifySource:
    name = "spotify"

    def __init__(
        self,
        client: httpx.Client | None = None,
        cache: MetricsCache | None = None,
        min_page_interval: float = 1.0,
    ):
        self.client = client or httpx.Client(timeout=15, headers={"User-Agent": USER_AGENT})
        self.cache = cache if cache is not None else MetricsCache(self.name)
        self.min_page_interval = min_page_interval
        self._token: str | None = None
        self._token_expires = 0.0
        self._last_page_fetch = 0.0

    # Token
    def _credentials(self) -> tuple[str, str]:
        load_dotenv(PROJECT_ROOT / ".env")
        cid, secret = os.environ.get("SPOTIFY_CLIENT_ID"), os.environ.get("SPOTIFY_CLIENT_SECRET")
        if not cid or not secret:
            raise SpotifyAuthError(
                "SPOTIFY_CLIENT_ID/SPOTIFY_CLIENT_SECRET fehlen. "
                ".env.example nach .env kopieren und ausfüllen (Anleitung dort)."
            )
        return cid, secret

    def _get_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires:
            return self._token
        cid, secret = self._credentials()
        resp = self._request("POST", TOKEN_URL, data={"grant_type": "client_credentials"}, auth=(cid, secret))
        if resp.status_code in (400, 401):
            raise SpotifyAuthError(f"Spotify lehnt die Zugangsdaten ab (HTTP {resp.status_code}); .env prüfen.")
        resp.raise_for_status()
        body = resp.json()
        self._token = body["access_token"]
        self._token_expires = time.monotonic() + max(int(body.get("expires_in", 3600)) - 60, 0)
        return self._token

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """HTTP mit Retry bei 429 (Retry-After respektieren, max. MAX_ATTEMPTS Versuche)."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            resp = self.client.request(method, url, **kwargs)
            if resp.status_code != 429 or attempt == MAX_ATTEMPTS:
                return resp
            try:
                wait = float(resp.headers.get("Retry-After", "1"))
            except ValueError:
                wait = 1.0
            if wait > MAX_RETRY_WAIT:
                raise SpotifyRateLimited(wait)
            time.sleep(max(wait, 0))
        return resp  # pragma: no cover

    # Teil 1: API
    def search(self, artist_name: str) -> dict[str, Any] | None:
        resp = self._request(
            "GET",
            SEARCH_URL,
            params={"q": artist_name, "type": "artist", "limit": 10, "market": "DE"},
            headers={"Authorization": f"Bearer {self._get_token()}"},
        )
        resp.raise_for_status()
        items = resp.json().get("artists", {}).get("items", [])
        return pick_best_match(artist_name, items)

    # Teil 2: Künstlerseite
    def fetch_monthly_listeners(self, spotify_id: str) -> int | None:
        wait = self._last_page_fetch + self.min_page_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            resp = self.client.get(
                ARTIST_PAGE_URL.format(id=spotify_id),
                headers={"Accept-Language": "en"},
                follow_redirects=True,
            )
            self._last_page_fetch = time.monotonic()
            if resp.status_code != 200:
                return None
            return parse_monthly_listeners(resp.text)
        except httpx.HTTPError:
            self._last_page_fetch = time.monotonic()
            return None

    # Teil 3: Cache
    def fetch(self, artist_name: str) -> ArtistMetrics | None:
        hit, cached = self.cache.get(artist_name)
        if hit:
            return cached
        item = self.search(artist_name)
        if item is None:
            self.cache.put(artist_name, None)
            return None
        followers = item.get("followers")
        metrics = ArtistMetrics(
            query_name=artist_name,
            source=self.name,
            values={
                "popularity": item.get("popularity"),
                "followers": followers.get("total") if isinstance(followers, dict) else None,
                "monthly_listeners": self.fetch_monthly_listeners(item["id"]),
            },
            source_id=item["id"],
            matched_name=item["name"],
        )
        self.cache.put(artist_name, metrics)
        return metrics

    def save(self) -> None:
        self.cache.save()


# --- CLI --------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Spotify-Kennzahlen für Künstlernamen abrufen.")
    ap.add_argument("names", nargs="*", help="Künstlernamen")
    ap.add_argument("--listeners-only", metavar="SPOTIFY_ID", nargs="+",
                    help="nur monatliche Hörer der Künstlerseite(n) abrufen (ohne Credentials)")
    args = ap.parse_args(argv)

    if args.listeners_only:
        src = SpotifySource(cache=MetricsCache("spotify-listeners-unused"))
        for sid in args.listeners_only:
            print(f"{sid}: monthly_listeners={src.fetch_monthly_listeners(sid)}")
        return 0
    if not args.names:
        ap.error("Künstlernamen oder --listeners-only angeben")

    src = SpotifySource()
    try:
        for name in args.names:
            m = src.fetch(name)
            if m is None:
                print(f"{name}: kein Treffer")
            else:
                print(f"{name}: {m.matched_name} ({m.source_id}) {m.values}")
    except SpotifyAuthError as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 2
    finally:
        src.save()
    return 0


if __name__ == "__main__":
    sys.exit(main())
