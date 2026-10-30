"""Rock am Ring / Rock im Park (gleiches Nuxt-System, serverseitig gerendertes HTML).

Struktur: `.lineup-days > article.lineup-day` (je Tag), darin `span.headliner`, `span.second`
und eine dritte Gruppe ohne Label; jeder Act ist ein `a[href^="/line-up/"]`. Headliner stehen
nur als Logo-Bild (Name im `title` des `img`), alle anderen als Text.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from festival_score.official import OfficialLineup


def _name(a: Tag) -> str:
    text = a.get_text(" ", strip=True)
    if not text:
        img = a.find("img")
        if img is not None:
            text = img.get("title") or re.sub(r"^Logo\s+", "", img.get("alt") or "")
    return re.sub(r"\s+", " ", text).strip()


def parse(html: str, url: str) -> OfficialLineup | None:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(".lineup-days")
    if root is None:
        return None
    acts: list[str] = []
    headliners: list[str] = []
    seen: set[str] = set()
    for a in root.select('a[href^="/line-up/"]'):
        name = _name(a)
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        acts.append(name)
        if a.find_parent(class_="headliner") is not None:
            headliners.append(name)
    if not acts:
        return None
    # Jahr aus dem <title> ("… | 4 - 6 Juni 2027 | …")
    title = soup.title.get_text() if soup.title else ""
    m = re.search(r"\b(20\d\d)\b", title)
    return OfficialLineup(acts=acts, headliners=headliners, year=int(m.group(1)) if m else None, url=url)
