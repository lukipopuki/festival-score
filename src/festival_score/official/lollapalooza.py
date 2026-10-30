"""Lollapalooza Berlin (Webflow, serverseitig gerendert).

Volle Liste: `.line-up_list [artist-item] .line-up_artist-name-text`. Darüber stehen drei
Hero-Listen (`.hero_artist-collection-list-wrapper`, Tier 1-3); die erste (größte Schrift) gilt
als Headliner-Liste. Leere Tiers enthalten nur `.w-dyn-empty`.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from festival_score.official import OfficialLineup


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def parse(html: str, url: str) -> OfficialLineup | None:
    soup = BeautifulSoup(html, "html.parser")
    acts: list[str] = []
    seen: set[str] = set()
    for item in soup.select(".line-up_list [artist-item]"):
        el = item.select_one(".line-up_artist-name-text")
        link = item.find("a", attrs={"aria-label": True})
        name = _clean(el.get_text(" ") if el else (link["aria-label"] if link else ""))
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            acts.append(name)
    if not acts:
        return None

    headliners: list[str] = []
    tier1 = soup.select_one(".hero_artist-collection-list-wrapper")
    if tier1 is not None:
        for el in tier1.select(".w-dyn-item .artist-title"):
            name = _clean(el.get_text(" "))
            if name and name not in headliners:
                headliners.append(name)

    title = soup.title.get_text() if soup.title else ""
    m = re.search(r"\b(20\d\d)\b", title)
    return OfficialLineup(acts=acts, headliners=headliners, year=int(m.group(1)) if m else None, url=url)
