"""Scraper für festivalplaner.de: Termine, Preise und Lineups als `Festival`-Objekte."""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import yaml
from bs4 import BeautifulSoup

from festival_score import USER_AGENT
from festival_score.models import Act, Festival

BASE_URL = "https://www.festivalplaner.de/festivals/"
CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "festivals.yaml"
REQUEST_PAUSE_S = 1.0
TIMEOUT_S = 20.0

log = logging.getLogger(__name__)


class ScraperError(Exception):
    """Festivaldaten konnten nicht geholt oder geparst werden."""


class FestivalNotFound(ScraperError):
    """Die Festivalseite existiert nicht (HTTP 404)."""


def load_slugs(path: Path = CONFIG_PATH) -> list[str]:
    """Liest die Festival-Slugs aus der YAML-Config."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return [str(s) for s in data["festivals"]]


def _find_music_event(soup: BeautifulSoup) -> dict[str, Any]:
    """Sucht im ld+json den MusicEvent-Block (auch in Listen oder @graph)."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text())
        except json.JSONDecodeError:
            continue
        candidates = data if isinstance(data, list) else data.get("@graph", [data])
        for item in candidates:
            if isinstance(item, dict) and item.get("@type") == "MusicEvent":
                return item
    raise ScraperError("Kein MusicEvent-ld+json gefunden")


def _parse_number(text: str) -> float | None:
    """Erste Zahl im Text, deutsches Format ("1.299,50", "89,90", "199")."""
    m = re.search(r"\d[\d.]*(?:,\d+)?", text)
    if not m:
        return None
    s = m.group(0).rstrip(".")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")  # Tausenderpunkt
    return float(s)


def _parse_facts(soup: BeautifulSoup) -> dict[str, str]:
    """dt/dd-Paare der Facts-Box (nur diese, nicht die Location-Box)."""
    box = soup.find("div", class_="facts-box")
    facts: dict[str, str] = {}
    if box:
        for dt in box.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            if dd:
                facts[dt.get_text(strip=True)] = dd.get_text(" ", strip=True)
    return facts


def _parse_acts(soup: BeautifulSoup) -> list[Act]:
    container = soup.find("div", class_="lineup-pills")
    acts: list[Act] = []
    seen: set[str] = set()
    if not container:
        return acts
    for a in container.find_all("a", class_="lineup-pill"):
        href = a.get("href", "")
        slug = href.rstrip("/").rsplit("/", 1)[-1]
        name = a.get_text(strip=True)  # BeautifulSoup dekodiert Entities
        if not slug or not name or slug in seen:
            continue
        seen.add(slug)
        acts.append(
            Act(
                name=name,
                slug=slug,
                position=len(acts),
                headliner="lineup-pill--headliner" in a.get("class", []),
            )
        )
    return acts


def parse_festival(html: str, slug: str) -> Festival:
    """Parst eine Festivalseite (reine Funktion, kein Netzwerk)."""
    soup = BeautifulSoup(html, "html.parser")
    event = _find_music_event(soup)

    try:
        start = date.fromisoformat(event["startDate"][:10])
        end = date.fromisoformat(event.get("endDate", event["startDate"])[:10])
    except (KeyError, ValueError) as e:
        raise ScraperError(f"{slug}: Datum nicht lesbar") from e

    name = re.sub(r"\s+\d{4}$", "", event.get("name", slug).strip())
    address = (event.get("location") or {}).get("address") or {}

    price = None
    capacity = None
    for key, value in _parse_facts(soup).items():
        if key.startswith("Preis"):
            price = _parse_number(value)
        elif key.startswith("K"):
            digits = re.sub(r"\D", "", value)
            capacity = int(digits) if digits else None

    return Festival(
        slug=slug,
        name=name,
        year=start.year,
        start_date=start,
        end_date=end,
        url=event.get("url") or f"{BASE_URL}{slug}",
        price_eur=price,
        capacity=capacity,
        location=address.get("addressLocality"),
        acts=_parse_acts(soup),
    )


def _new_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S, follow_redirects=True
    )


def fetch_festival(slug: str, client: httpx.Client | None = None) -> Festival:
    """Holt und parst ein Festival. 404 -> `FestivalNotFound`."""
    own = client is None
    if client is None:
        client = _new_client()
    try:
        try:
            resp = client.get(BASE_URL + slug)
        except httpx.HTTPError as e:
            raise ScraperError(f"{slug}: Netzwerkfehler: {e}") from e
        if resp.status_code == 404:
            raise FestivalNotFound(f"Festival '{slug}' nicht gefunden (404): {BASE_URL}{slug}")
        if resp.status_code >= 400:
            raise ScraperError(f"{slug}: HTTP {resp.status_code}")
        return parse_festival(resp.text, slug)
    finally:
        if own:
            client.close()


def fetch_all(slugs: list[str], client: httpx.Client | None = None) -> list[Festival]:
    """Holt alle Festivals mit 1 s Pause; Fehler werden geloggt und übersprungen."""
    own = client is None
    if client is None:
        client = _new_client()
    result: list[Festival] = []
    try:
        for i, slug in enumerate(slugs):
            if i:
                time.sleep(REQUEST_PAUSE_S)
            try:
                result.append(fetch_festival(slug, client))
            except Exception:  # noqa: BLE001 - einzelnes Festival darf den Lauf nicht abbrechen
                log.exception("Festival %s übersprungen", slug)
    finally:
        if own:
            client.close()
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for f in fetch_all(load_slugs()):
        price = f"{f.price_eur:.2f} EUR" if f.price_eur is not None else "kein Preis"
        heads = sum(a.headliner for a in f.acts)
        print(
            f"{f.name} {f.year} | {f.start_date} bis {f.end_date} | {f.days} Tage | "
            f"{price} | {len(f.acts)} Acts | {heads} Headliner"
        )


if __name__ == "__main__":
    main()
