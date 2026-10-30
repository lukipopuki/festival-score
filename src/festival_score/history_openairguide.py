"""openairguide.net: Lineups (Headliner + volle Bandliste) und Termine vergangener/kommender Ausgaben.

Seitenstruktur (geprüft am HTML, nicht am Fließtext):
- Vergangene Ausgabe `…/<slug>/_history/<jahr>`: Headliner in `.headliner-swipe .teaser-festivals`,
  Bandliste in `#bandliste` (Timetable nach Tagen, je Band ein `.card-title .teaser-festivals`),
  Anzahl in `#anzahl_treffer_bandliste` ("118 Bands aufgetreten") bzw. `#alle_resultate`.
- Aktuelle Ausgabe `…/<slug>`: ld+json `Event` mit Datum, Anzahl in `#alle_resultate`, nur die ersten
  Bands; die volle Liste steht auf `…/<slug>/_confirmed`.
Die Seite ist UTF-8; ein cp1252-Fallback fängt Abweichungen ab.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from festival_score import USER_AGENT

BASE_URL = "https://www.openairguide.net/festivals/deutschland/"
CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache" / "openairguide"
CURRENT_MAX_AGE_S = 24 * 3600  # aktuelle Seite: 1 Tag; vergangene Jahre unbegrenzt
REQUEST_PAUSE_S = 1.5
TIMEOUT_S = 20.0

log = logging.getLogger(__name__)
_last_request = 0.0

MONTHS = {
    "januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4, "mai": 5, "juni": 6, "juli": 7,
    "august": 8, "september": 9, "oktober": 10, "november": 11, "dezember": 12,
}
_DAY = r"(\d{1,2})\.?"
_MONTH = r"([A-Za-zÄÖÜäöü]+)"
_YEAR = r"(\d{4})"
_RANGE_RE = re.compile(
    rf"{_DAY}\s*(?:{_MONTH}\s*(?:{_YEAR}\s*)?)?(?:bis|und|-|–)\s*(?:[A-Za-zäöü]+,\s*)?{_DAY}\s*{_MONTH}\s*{_YEAR}"
)
_SINGLE_RE = re.compile(rf"{_DAY}\s*{_MONTH}\s*{_YEAR}")


class OagError(Exception):
    """Seite konnte nicht geholt werden."""


@dataclass
class OagEdition:
    year: int
    start_date: date | None
    end_date: date | None
    headliners: list[str] = field(default_factory=list)
    bands: list[str] = field(default_factory=list)  # Seitenreihenfolge (Tag, Uhrzeit)
    reported_count: int | None = None
    url: str = ""


# --- Parsen ------------------------------------------------------------------------------------


def decode_html(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _month(name: str) -> int | None:
    return MONTHS.get(name.lower())


def parse_date_range(text: str) -> tuple[date, date] | None:
    """Erster Zeitraum im Text: "17. bis 20. Juli 2025", "31. Juli bis 03. August 2024", "5. Juli 2025"."""
    text = _clean(text)
    m = _RANGE_RE.search(text)
    if m:
        d1, m1, y1, d2, m2, y2 = m.groups()
        mo2 = _month(m2)
        mo1 = _month(m1) if m1 else mo2
        if mo1 and mo2:
            try:
                return date(int(y1 or y2), mo1, int(d1)), date(int(y2), mo2, int(d2))
            except ValueError:
                pass
    m = _SINGLE_RE.search(text)
    if m and _month(m.group(2)):
        try:
            d = date(int(m.group(3)), _month(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
        return d, d
    return None


def _find_dates(soup: BeautifulSoup, text: str, days: list[date]) -> tuple[date | None, date | None]:
    # 1. ld+json Event (aktuelle Seite)
    for sc in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(sc.string or sc.get_text())
        except json.JSONDecodeError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("startDate"):
                try:
                    start = date.fromisoformat(str(item["startDate"])[:10])
                    end = date.fromisoformat(str(item.get("endDate") or item["startDate"])[:10])
                    return start, end
                except ValueError:
                    pass
    # 2. Satz "Das Festival fand vom … statt"
    m = re.search(r"(?:fand|findet)\s+(?:vom|am)\s+(.{5,80}?)\s+(?:statt|in)\b", text)
    if m:
        rng = parse_date_range(m.group(1))
        if rng:
            return rng
    # 3. Tagesüberschriften des Timetables
    if days:
        return min(days), max(days)
    return None, None


def parse_edition(html: str) -> OagEdition | None:
    """Parst eine vergangene Ausgabe oder die aktuelle Seite; ohne Bands/Headliner -> None."""
    soup = BeautifulSoup(html, "html.parser")
    text = _clean(soup.get_text(" "))

    headliners: list[str] = []
    for el in soup.select(".headliner-swipe .teaser-festivals"):
        name = _clean(el.get_text())
        if name and name not in headliners:
            headliners.append(name)

    bands: list[str] = []
    seen: set[str] = set()
    container = soup.select_one("#bandliste") or soup.select_one("#bandliste_overview_confirmed")
    if container:
        for el in container.select(".card-title .teaser-festivals"):
            name = _clean(el.get_text())
            link = el.find_parent("a")
            key = link["href"] if link is not None and link.get("href") else name
            if name and key not in seen:
                seen.add(key)
                bands.append(name)
    # Headliner ohne Timetable-Eintrag trotzdem in die Bandliste
    have = {b.casefold() for b in bands}
    for h in headliners:
        if h.casefold() not in have:
            bands.append(h)

    reported: int | None = None
    el = soup.select_one("#anzahl_treffer_bandliste")
    m = re.match(r"(\d+)\s+Band", _clean(el.get_text())) if el else None
    if m:
        reported = int(m.group(1))
    else:
        m = re.search(r"id=['\"]alle_resultate['\"]\s+value=['\"](\d+)['\"]", html)
        reported = int(m.group(1)) if m else None

    if not bands:
        return None

    days: list[date] = []
    for h in container.select(".ro-date h3.h3-festival") if container else []:
        r = parse_date_range(h.get_text())
        if r:
            days.append(r[0])
    start, end = _find_dates(soup, text, days)
    year = start.year if start else None
    if year is None:
        t = soup.title.get_text() if soup.title else ""
        m = re.search(r"\b(20\d\d)\b", t)
        year = int(m.group(1)) if m else None
    if year is None:
        return None
    return OagEdition(year, start, end, headliners, bands, reported)


# --- Abruf (mit Dateicache, höflicher Pause) -----------------------------------------------------


def _cache_path(slug: str, key: str, cache_dir: Path | None) -> Path:
    return (cache_dir or CACHE_DIR) / f"{slug}-{key}.html"


def _get(url: str, path: Path, max_age_s: float | None, client: httpx.Client | None) -> str | None:
    """HTML holen: Cache (falls frisch) oder Request; 404 -> None, andere Fehler -> OagError."""
    global _last_request
    if path.exists() and (max_age_s is None or time.time() - path.stat().st_mtime < max_age_s):
        return decode_html(path.read_bytes())
    own = client is None
    if client is None:
        client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S, follow_redirects=True)
    try:
        wait = REQUEST_PAUSE_S - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        try:
            resp = client.get(url, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
        except httpx.HTTPError as e:
            raise OagError(f"{url}: Netzwerkfehler: {e}") from e
        finally:
            _last_request = time.monotonic()
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise OagError(f"{url}: HTTP {resp.status_code}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(resp.content)
        return decode_html(resp.content)
    finally:
        if own:
            client.close()


def edition_url(oag_slug: str, year: int | None = None) -> str:
    return f"{BASE_URL}{oag_slug}/_history/{year}" if year else f"{BASE_URL}{oag_slug}"


def fetch_edition(
    oag_slug: str, year: int, client: httpx.Client | None = None, cache_dir: Path | None = None
) -> OagEdition | None:
    """Vergangene Ausgabe; 404, leere oder falsche Jahresseite -> None."""
    url = edition_url(oag_slug, year)
    html = _get(url, _cache_path(oag_slug, str(year), cache_dir), None, client)
    ed = parse_edition(html) if html else None
    if ed is None or ed.year != year:
        return None
    ed.url = url
    return ed


def fetch_current(
    oag_slug: str, client: httpx.Client | None = None, cache_dir: Path | None = None
) -> OagEdition | None:
    """Aktuelle/kommende Ausgabe. Hauptseite (Termin, Anzahl, erste Bands) + `_confirmed` (volle Liste)."""
    url = edition_url(oag_slug)
    html = _get(url, _cache_path(oag_slug, "current", cache_dir), CURRENT_MAX_AGE_S, client)
    ed = parse_edition(html) if html else None
    if ed is None:
        return None
    ed.url = url
    # Die Hauptseite zeigt nur die ersten Bands; diese gelten als Headliner
    if not ed.headliners:
        ed.headliners = list(ed.bands)
    if ed.reported_count is None or len(ed.bands) < ed.reported_count:
        try:
            html2 = _get(
                f"{url}/_confirmed", _cache_path(oag_slug, "current-confirmed", cache_dir), CURRENT_MAX_AGE_S, client
            )
        except OagError:
            log.exception("%s: _confirmed-Seite nicht abrufbar", oag_slug)
            html2 = None
        full = parse_edition(html2) if html2 else None
        if full and len(full.bands) > len(ed.bands):
            heads = {x.casefold() for x in ed.headliners}
            ed.bands = ed.headliners + [b for b in full.bands if b.casefold() not in heads]
    return ed
