"""Vorjahres-Lineups: openairguide.net (Lineup, Termin, Headliner) + Wikipedia (Preis, Besucher) -> `Festival`.

CLI: `python -m festival_score.history [slug …]`
"""

from __future__ import annotations

import logging
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import yaml

from festival_score import USER_AGENT
from festival_score import history_openairguide as oag
from festival_score import history_wikipedia as wiki
from festival_score.cache import normalize_name
from festival_score.models import Act, Festival
from festival_score.scraper import BASE_URL as FESTIVALPLANER_URL
from festival_score.scraper import load_slugs

HISTORY_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "history.yaml"
FALLBACK_MONTH_DAY = (7, 1)
TIMEOUT_S = 20.0

log = logging.getLogger(__name__)


def load_history_config(path: Path = HISTORY_CONFIG_PATH) -> dict[str, dict[str, Any]]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return {str(k): (v or {}) for k, v in data["festivals"].items()}


def default_years(today: date | None = None) -> list[int]:
    y = (today or date.today()).year
    return [y - 2, y - 1, y]


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", normalize_name(name)).strip("-") or "act"


def build_acts(headliners: list[str], others: list[str]) -> list[Act]:
    """Headliner zuerst, dann der Rest in gegebener Reihenfolge; Duplikate per normalize_name."""
    acts: list[Act] = []
    seen: set[str] = set()
    for names, is_head in ((headliners, True), (others, False)):
        for name in names:
            key = normalize_name(name)
            if key not in seen:
                seen.add(key)
                acts.append(Act(name, _slugify(name), len(acts), is_head))
    return acts


def fetch_history(
    slug: str, years: list[int] | None = None, client: httpx.Client | None = None
) -> list[Festival]:
    """Ausgaben des Festivals in `years` (Standard: letzte 3 Jahre); Jahre ohne Daten fehlen."""
    cfg = load_history_config().get(slug)
    if cfg is None:
        raise KeyError(f"'{slug}' steht nicht in config/history.yaml")
    years = years or default_years()
    own = client is None
    if client is None:
        client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S, follow_redirects=True)
    try:
        wiki_years: dict[int, wiki.WikiYear] = {}
        title = cfg.get("wikipedia")
        if title:
            try:
                wiki_years = wiki.fetch_wikipedia_years(title, client, cfg.get("wikipedia_skip_sections") or ())
            except Exception:  # noqa: BLE001 - Quelle darf den Lauf nicht abbrechen
                log.exception("%s: Wikipedia-Abruf fehlgeschlagen", slug)

        oag_slug = cfg.get("openairguide")
        result = []
        for year in sorted(years):
            ed: oag.OagEdition | None = None
            if oag_slug:
                try:
                    ed = oag.fetch_edition(str(oag_slug), year, client)
                except Exception:  # noqa: BLE001
                    log.exception("%s %d: openairguide-Abruf fehlgeschlagen", slug, year)
            fest = _build_year(slug, cfg, year, wiki_years.get(year), ed)
            if fest:
                result.append(fest)
        return result
    finally:
        if own:
            client.close()


def _build_year(
    slug: str, cfg: dict[str, Any], year: int, wy: wiki.WikiYear | None, ed: oag.OagEdition | None
) -> Festival | None:
    if ed is None and wy and wy.cancelled and not wy.visitors:
        return None
    if ed is None and wy is None:
        return None

    if ed and ed.start_date and ed.end_date:
        window = (ed.start_date, ed.end_date)
    elif wy and wy.start_date and wy.end_date:
        window = (wy.start_date, wy.end_date)
    else:
        window = (date(year, *FALLBACK_MONTH_DAY), date(year, *FALLBACK_MONTH_DAY))
        log.warning("%s %d: Termin unbekannt, verwende %s", slug, year, window[0])
    if window[0] > date.today():
        return None  # noch nicht stattgefunden

    if ed:
        headliners = ed.headliners or (wy.headliners if wy else [])
        others = ed.bands  # Seitenreihenfolge
    else:
        headliners = wy.headliners if wy else []
        others = sorted(wy.lineup if wy else [], key=normalize_name)
    acts = build_acts(headliners, others)
    if not acts and not (wy and (wy.visitors or wy.price_eur or wy.reported_act_count)):
        log.info("%s %d: weder Acts noch Eckdaten, übersprungen", slug, year)
        return None
    reported = (wy.reported_act_count if wy else None) or (ed.reported_count if ed else None)
    source = "openairguide+wikipedia" if ed and wy else "openairguide" if ed else "wikipedia"
    if ed and ed.url:
        url = ed.url
    elif cfg.get("wikipedia"):
        url = f"{wiki.ARTICLE_URL}{cfg['wikipedia']}"
    else:
        url = f"{FESTIVALPLANER_URL}{slug}"
    return Festival(
        slug=slug,
        name=cfg.get("name", slug),
        year=year,
        start_date=window[0],
        end_date=window[1],
        url=url,
        price_eur=wy.price_eur if wy else None,
        capacity=None,
        location=cfg.get("location"),
        acts=acts,
        visitors=wy.visitors if wy else None,
        reported_act_count=reported,
        source=source,
    )


def fetch_upcoming(slug: str, client: httpx.Client | None = None) -> Festival | None:
    """Kommende Ausgabe von openairguide – Ersatz, wenn festivalplaner noch das Vorjahr zeigt."""
    oag_slug = load_history_config().get(slug, {}).get("openairguide")
    if not oag_slug:
        return None
    ed = oag.fetch_current(oag_slug, client)
    if ed is None or ed.start_date is None or ed.start_date <= date.today():
        return None
    cfg = load_history_config()[slug]
    return Festival(
        slug=slug,
        name=cfg.get("name", slug),
        year=ed.year,
        start_date=ed.start_date,
        end_date=ed.end_date or ed.start_date,
        url=ed.url,
        location=cfg.get("location"),
        acts=build_acts(ed.headliners, ed.bands),
        reported_act_count=ed.reported_count,
        source="openairguide",
    )


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    slugs = (argv if argv is not None else sys.argv[1:]) or load_slugs()
    for slug in slugs:
        try:
            fests = fetch_history(slug)
        except Exception:  # noqa: BLE001
            log.exception("%s übersprungen", slug)
            continue
        if not fests:
            print(f"{slug} | keine Daten")
        for f in fests:
            price = f"{f.price_eur:.0f} EUR" if f.price_eur is not None else "-"
            vis = f"{f.visitors:,}".replace(",", ".") if f.visitors else "-"
            heads = ", ".join(a.name for a in f.acts if a.headliner) or "-"
            print(
                f"{f.name} {f.year} | {f.start_date} bis {f.end_date} | Preis {price} | Besucher {vis} | "
                f"gemeldet {f.reported_act_count or '-'} Bands | {len(f.acts)} Acts | Headliner: {heads} | {f.source}"
            )


if __name__ == "__main__":
    main()
