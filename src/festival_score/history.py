"""Vorjahres-Lineups: Wikipedia (Eckdaten, Headliner) + setlist.fm (volles Lineup) -> `Festival`.

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
from festival_score import history_setlistfm as slfm
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
    """Headliner zuerst (Reihenfolge der Quelle), dann der Rest alphabetisch; Duplikate per normalize_name."""
    acts: list[Act] = []
    seen: set[str] = set()
    for name in headliners:
        key = normalize_name(name)
        if key not in seen:
            seen.add(key)
            acts.append(Act(name, _slugify(name), len(acts), True))
    for name in sorted(others, key=normalize_name):
        key = normalize_name(name)
        if key not in seen:
            seen.add(key)
            acts.append(Act(name, _slugify(name), len(acts), False))
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

        api_key = slfm.get_api_key()
        venue_ids: list[str] = []
        term = cfg.get("setlistfm_venue")
        if term and not api_key:
            log.warning("%s: SETLISTFM_API_KEY fehlt – nur Wikipedia-Daten", slug)
        elif term:
            try:
                venue_ids = slfm.find_venue_ids(str(term), client, api_key)
                if not venue_ids:
                    log.warning("%s: keine setlist.fm-Venue zu '%s' gefunden", slug, term)
            except Exception:  # noqa: BLE001
                log.exception("%s: setlist.fm-Venue-Suche fehlgeschlagen", slug)

        result = []
        for year in sorted(years):
            fest = _build_year(slug, cfg, year, wiki_years.get(year), venue_ids, api_key, client)
            if fest:
                result.append(fest)
        return result
    finally:
        if own:
            client.close()


def _build_year(
    slug: str,
    cfg: dict[str, Any],
    year: int,
    wy: wiki.WikiYear | None,
    venue_ids: list[str],
    api_key: str | None,
    client: httpx.Client,
) -> Festival | None:
    if wy and wy.cancelled and not wy.visitors:
        return None
    perfs: list[slfm.Performance] = []
    if venue_ids and api_key:
        try:
            perfs = slfm.fetch_performances(venue_ids, year, client, api_key)
        except Exception:  # noqa: BLE001
            log.exception("%s %d: setlist.fm-Abruf fehlgeschlagen", slug, year)

    window = (wy.start_date, wy.end_date) if wy and wy.start_date and wy.end_date else None
    window_from_slfm = False
    if window is None and perfs:
        window = slfm.infer_window(perfs)
        window_from_slfm = window is not None
    names = slfm.artists_in_window(perfs, *window) if window and perfs else []

    if wy is None and not names:
        return None
    if window is None:
        window = (date(year, *FALLBACK_MONTH_DAY), date(year, *FALLBACK_MONTH_DAY))
        log.warning("%s %d: Termin unbekannt, verwende %s", slug, year, window[0])
    elif window_from_slfm:
        log.info("%s %d: Termin aus setlist.fm abgeleitet: %s bis %s", slug, year, *window)
    if window[0] > date.today():
        return None  # noch nicht stattgefunden

    headliners = wy.headliners if wy else []
    wiki_lineup = wy.lineup if wy else []
    # setlist.fm-Schreibweise hat Vorrang; ohne setlist.fm bleibt die Wikipedia-Liste (sonst nur Headliner)
    acts = build_acts(headliners, names + wiki_lineup)
    if not acts and not (wy and (wy.visitors or wy.price_eur or wy.reported_act_count)):
        log.info("%s %d: weder Acts noch Eckdaten, übersprungen", slug, year)
        return None
    source = "setlistfm+wikipedia" if names and wy else "setlistfm" if names else "wikipedia"
    return Festival(
        slug=slug,
        name=cfg.get("name", slug),
        year=year,
        start_date=window[0],
        end_date=window[1],
        url=f"{wiki.ARTICLE_URL}{cfg['wikipedia']}" if cfg.get("wikipedia") else f"{FESTIVALPLANER_URL}{slug}",
        price_eur=wy.price_eur if wy else None,
        capacity=None,
        location=cfg.get("location"),
        acts=acts,
        visitors=wy.visitors if wy else None,
        reported_act_count=wy.reported_act_count if wy else None,
        source=source,
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
