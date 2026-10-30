"""Wacken Open Air.

Die HTML-Seite ist nur ein leerer Vue-Mountpunkt; die Bands kommen aus
`https://www.wacken.com/fileadmin/Json/bandlist-concert.json` (URL steht im Inline-Script der
Seite als `t3vue.ajaxUrls.list`; robots.txt der Domain liefert 404, also keine Einschränkung).
`parse` akzeptiert deshalb beides: HTML (JSON-URL wird gesucht und per `get_html` geholt) oder
direkt den JSON-Text.

Das JSON hat keine Headliner-Markierung -> `headliners` bleibt leer. `detailLink` enthält
fälschlich "2026"; das Jahr kommt aus dem Ticket-Link der Seite bzw. den Bildpfaden (`bands2027`).
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from urllib.parse import urljoin

import httpx

from festival_score.official import OfficialLineup, get_html

log = logging.getLogger(__name__)

_JSON_URL = re.compile(r"""['"]([^'"\s]*bandlist-concert\.json[^'"\s]*)['"]""")


def _year_from_html(html: str) -> int | None:
    m = re.search(r"ticket-uebersicht-(20\d\d)|agb-(20\d\d)", html)
    return int(next(g for g in m.groups() if g)) if m else None


def _year_from_json(data: list[dict]) -> int | None:
    years: Counter[int] = Counter()
    for entry in data:
        for img in entry.get("images") or []:
            m = re.search(r"bands(20\d\d)/", (img.get("originalResource") or {}).get("publicUrl") or "")
            if m:
                years[int(m.group(1))] += 1
    return years.most_common(1)[0][0] if years else None


def parse_json(text: str, url: str = "", year: int | None = None) -> OfficialLineup | None:
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, list):
        return None
    acts: list[str] = []
    seen: set[str] = set()
    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = re.sub(r"\s+", " ", ((entry.get("artist") or {}).get("title") or "")).strip()
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            acts.append(name)
    if not acts:
        return None
    return OfficialLineup(acts=acts, year=year or _year_from_json(data), url=url)


def parse(html: str, url: str) -> OfficialLineup | None:
    if html.lstrip().startswith("["):
        return parse_json(html, url)
    m = _JSON_URL.search(html)
    if m is None:
        return None
    json_url = urljoin(url, m.group(1))
    try:
        text = get_html(json_url)
    except httpx.HTTPError:
        log.warning("Wacken: %s nicht abrufbar", json_url)
        return None
    return parse_json(text, url, year=_year_from_html(html)) if text else None
