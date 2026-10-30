"""setlist.fm: volles Lineup vergangener Festivals aus den Setlists an der Festival-Venue.

API: https://api.setlist.fm/docs/1.0/index.html – Header `x-api-key` und `Accept: application/json`.
Antworten sind paginiert (`page`, `itemsPerPage`, `total`, Liste unter `venue` bzw. `setlist`);
`eventDate` hat das Format `dd-MM-yyyy`. Kein Treffer wird von setlist.fm als 404 beantwortet.
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import httpx
from dotenv import load_dotenv

from festival_score import USER_AGENT
from festival_score.cache import normalize_name

BASE_URL = "https://api.setlist.fm/rest/1.0"
REQUEST_PAUSE_S = 0.5  # max. 2 Requests/s
RETRY_WAIT_S = 2.0
MAX_RETRIES = 3
MAX_PAGES = 50
TIMEOUT_S = 20.0
MAX_FESTIVAL_DAYS = 4  # Fenster für die Termin-Ableitung aus den Setlists
MIN_SETLISTS_FOR_INFERENCE = 5

log = logging.getLogger(__name__)
_last_request = 0.0


class SetlistFmError(Exception):
    """setlist.fm-Abfrage fehlgeschlagen."""


@dataclass(frozen=True)
class Performance:
    event_date: date
    artist: str


def get_api_key() -> str | None:
    """API-Key aus Umgebung bzw. `.env`; None, wenn nicht gesetzt."""
    load_dotenv()
    return os.environ.get("SETLISTFM_API_KEY") or None


def _get(client: httpx.Client, path: str, api_key: str, params: dict) -> dict | None:
    """GET mit Rate-Limit und Retry bei 429. None bei 404 (keine Treffer)."""
    global _last_request
    for attempt in range(MAX_RETRIES + 1):
        wait = REQUEST_PAUSE_S - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        try:
            resp = client.get(
                f"{BASE_URL}{path}",
                params=params,
                headers={"x-api-key": api_key, "Accept": "application/json", "User-Agent": USER_AGENT},
            )
        except httpx.HTTPError as e:
            raise SetlistFmError(f"{path}: Netzwerkfehler: {e}") from e
        finally:
            _last_request = time.monotonic()
        if resp.status_code == 429:
            if attempt == MAX_RETRIES:
                raise SetlistFmError(f"{path}: Rate-Limit (429) nach {MAX_RETRIES} Wiederholungen")
            retry_after = resp.headers.get("Retry-After", "")
            time.sleep(float(retry_after) if retry_after.isdigit() else RETRY_WAIT_S * (attempt + 1))
            continue
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise SetlistFmError(f"{path}: HTTP {resp.status_code}")
        return resp.json()
    raise SetlistFmError(f"{path}: unerreichbar")  # pragma: no cover


def _pages(client: httpx.Client, path: str, api_key: str, params: dict, key: str) -> list[dict]:
    items: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        data = _get(client, path, api_key, {**params, "p": page})
        if not data:
            break
        items += data.get(key, [])
        if page * int(data.get("itemsPerPage") or 1) >= int(data.get("total") or 0):
            break
    return items


def find_venue_ids(term: str, client: httpx.Client, api_key: str, country: str = "DE") -> list[str]:
    """venueIds zu einem Suchbegriff. Ist `term` selbst eine ID (hex, ohne Leerzeichen), wird sie genutzt.

    Bevorzugt Venues, deren Name den Suchbegriff enthält; sonst der erste Treffer.
    """
    term = str(term).strip()
    if re.fullmatch(r"[0-9a-f]{6,10}", term):
        return [term]
    venues = _pages_first(client, "/search/venues", api_key, {"name": term, "country": country})
    if not venues:
        return []
    want = normalize_name(term)
    exact = [v["id"] for v in venues if want in normalize_name(v.get("name", ""))]
    return exact or [venues[0]["id"]]


def _pages_first(client: httpx.Client, path: str, api_key: str, params: dict) -> list[dict]:
    data = _get(client, path, api_key, {**params, "p": 1})
    return (data or {}).get("venue", [])


def parse_setlists(setlists: list[dict]) -> list[Performance]:
    out = []
    for s in setlists:
        try:
            d = datetime.strptime(s["eventDate"], "%d-%m-%Y").date()
            name = s["artist"]["name"].strip()
        except (KeyError, ValueError, AttributeError):
            continue
        if name:
            out.append(Performance(d, name))
    return out


def fetch_performances(
    venue_ids: list[str], year: int, client: httpx.Client, api_key: str
) -> list[Performance]:
    """Alle Setlists der Venues in einem Jahr (paginiert)."""
    result: list[Performance] = []
    for vid in venue_ids:
        raw = _pages(client, "/search/setlists", api_key, {"venueId": vid, "year": year}, "setlist")
        result += parse_setlists(raw)
    return result


def infer_window(perfs: list[Performance]) -> tuple[date, date] | None:
    """Festivalzeitraum aus den Setlists: das Fenster (<= 4 Tage) mit den meisten Auftritten."""
    counts = Counter(p.event_date for p in perfs)
    best: tuple[int, date] | None = None
    for d in counts:
        total = sum(counts.get(d + timedelta(days=i), 0) for i in range(MAX_FESTIVAL_DAYS))
        if best is None or (total, -d.toordinal()) > (best[0], -best[1].toordinal()):
            best = (total, d)
    if best is None or best[0] < MIN_SETLISTS_FOR_INFERENCE:
        return None
    days = [best[1] + timedelta(days=i) for i in range(MAX_FESTIVAL_DAYS) if counts.get(best[1] + timedelta(days=i))]
    return min(days), max(days)


def artists_in_window(perfs: list[Performance], start: date, end: date) -> list[str]:
    """Eindeutige Künstler (nach normalize_name) mit Auftritt im Zeitraum, alphabetisch."""
    seen: dict[str, str] = {}
    for p in perfs:
        if start <= p.event_date <= end:
            seen.setdefault(normalize_name(p.artist), p.artist)
    return sorted(seen.values(), key=normalize_name)
