"""Parookaville.

Die HTML-Seite enthält nur `<artist-wrapper initial-search-path="/search/artist?…festival-17">`;
die Acts liefert diese JSON-Suche (robots.txt: alles erlaubt). `parse` akzeptiert HTML (Such-URL
wird gelesen und per `get_html` geholt, `links.next` wird verfolgt) oder direkt das JSON.

`properties.artist.artist.name` hat die Originalschreibweise (`nameUppercase` ist nur ein
Darstellungs-Flag); `subname` ("in the round") wird ignoriert. Headliner-Markierung gibt es
nicht (`section.name` ist leer) -> `headliners` bleibt leer. Jahr aus `festival.name`.
"""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from festival_score.official import OfficialLineup, get_html

log = logging.getLogger(__name__)

MAX_PAGES = 5


def _load(text: str) -> dict | None:
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("items"), list) else None


def _collect(pages: list[dict], url: str) -> OfficialLineup | None:
    acts: list[str] = []
    seen: set[str] = set()
    year: int | None = None
    for page in pages:
        for item in page["items"]:
            props = item.get("properties") or {}
            info = props.get("artist") or {}
            name = ((info.get("artist") or {}).get("name") or item.get("label") or "").strip()
            name = re.sub(r"\s+", " ", name)
            if name and name.casefold() not in seen:
                seen.add(name.casefold())
                acts.append(name)
            if year is None:
                m = re.search(r"\b(20\d\d)\b", (info.get("festival") or {}).get("name") or "")
                year = int(m.group(1)) if m else None
    if not acts:
        return None
    return OfficialLineup(acts=acts, year=year, url=url)


def parse(html: str, url: str) -> OfficialLineup | None:
    first = _load(html) if html.lstrip().startswith("{") else None
    if first is None:
        tag = BeautifulSoup(html, "html.parser").find("artist-wrapper")
        path = tag.get("initial-search-path") if tag else None
        if not path:
            return None
        next_url: str | None = urljoin(url, path)
        pages: list[dict] = []
        while next_url and len(pages) < MAX_PAGES:
            try:
                text = get_html(next_url)
            except httpx.HTTPError:
                log.warning("Parookaville: %s nicht abrufbar", next_url)
                break
            page = _load(text) if text else None
            if page is None:
                break
            pages.append(page)
            nxt = (page.get("links") or {}).get("next")
            next_url = urljoin(url, nxt) if nxt else None
    else:
        pages = [first]
    return _collect(pages, url) if pages else None
