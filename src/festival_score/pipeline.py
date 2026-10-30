"""Pipeline: Festivals holen, Acts anreichern, Kennzahlen berechnen, JSON schreiben."""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml
from dotenv import load_dotenv

from festival_score import scoring, scraper
from festival_score.cache import normalize_name
from festival_score.models import Festival
from festival_score.sources.spotify import SpotifyAuthError, SpotifyRateLimited

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OVERRIDES_PATH = PROJECT_ROOT / "config" / "artist_overrides.yaml"
OUTPUT_PATH = PROJECT_ROOT / "data" / "processed" / "festivals.json"
PROGRESS_EVERY = 25
SAVE_EVERY = 50

log = logging.getLogger(__name__)


@dataclass
class ActMetrics:
    """Rohwerte eines Acts aus beiden Quellen (None = unbekannt)."""

    spotify_id: str | None = None
    monthly_listeners: int | None = None
    lastfm_listeners: int | None = None


def load_overrides(path: Path = OVERRIDES_PATH) -> dict[str, dict]:
    """Liest die Overrides. Fehlende oder leere Datei -> {}."""
    if not Path(path).exists():
        return {}
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return {str(k): (v or {}) for k, v in data.items()}


def collect_editions(
    slugs: list[str],
    history_provider: Callable[[str], list[Festival]] | None = None,
) -> dict[str, list[Festival]]:
    """Aktuelle Ausgaben (festivalplaner) plus optional Historie; festivalplaner gewinnt je (slug, year)."""
    result: dict[str, dict[int, Festival]] = {s: {} for s in slugs}
    for f in scraper.fetch_all(slugs):
        result.setdefault(f.slug, {})[f.year] = f
    if history_provider is not None:
        for slug in slugs:
            try:
                history = history_provider(slug)
            except Exception:  # noqa: BLE001 - ein Festival darf den Lauf nicht abbrechen
                log.exception("Historie für %s fehlgeschlagen", slug)
                continue
            for f in history:
                if not f.acts:
                    continue  # vergangene Ausgabe ohne Lineup würde mit Score 0 den Vergleich verzerren
                existing = result.setdefault(slug, {}).get(f.year)
                if existing is None or (
                    existing.source != "festivalplaner" and f.source == "festivalplaner"
                ):
                    result[slug][f.year] = f
    return {s: sorted(eds.values(), key=lambda f: f.year) for s, eds in result.items() if eds}


def _unique_acts(festivals: list[Festival]) -> dict[str, str]:
    """normalisierter Name -> erster Original-Name."""
    names: dict[str, str] = {}
    for f in festivals:
        for a in f.acts:
            names.setdefault(normalize_name(a.name), a.name)
    return names


def enrich(
    festivals: list[Festival], spotify: Any, lastfm: Any, overrides: dict[str, dict]
) -> dict[str, ActMetrics]:
    """Fragt jeden eindeutigen Act einmal bei Spotify und Last.fm ab. Schlüssel: normalize_name."""
    overrides = {normalize_name(k): v for k, v in overrides.items()}
    names = _unique_acts(festivals)
    total = len(names)
    result: dict[str, ActMetrics] = {}
    spotify_blocked = False  # nach einer Sperre nur noch Cache-Werte nutzen
    for i, (key, name) in enumerate(names.items(), 1):
        ov = overrides.get(key) or {}
        m = ActMetrics()
        result[key] = m
        if ov.get("skip"):
            log.info("Act %r übersprungen (skip)", name)
        else:
            query = ov.get("search_name") or name
            try:
                if ov.get("spotify_id"):
                    m.spotify_id = str(ov["spotify_id"])
                    m.monthly_listeners = spotify.fetch_monthly_listeners(m.spotify_id)
                else:
                    if spotify_blocked:
                        _, sm = spotify.cache.get(query)
                    else:
                        sm = spotify.fetch(query)
                    if sm is not None:
                        m.spotify_id = sm.source_id
                        m.monthly_listeners = sm.values.get("monthly_listeners")
            except SpotifyAuthError:
                raise  # fehlende/ungültige Credentials betreffen jeden Act -> Lauf abbrechen
            except SpotifyRateLimited as e:
                spotify_blocked = True
                log.warning("%s – restliche Acts nur aus dem Cache bzw. per Last.fm-Schätzung", e)
            except Exception as e:  # noqa: BLE001
                log.warning("Spotify für %r fehlgeschlagen: %s", name, e)
            try:
                lm = lastfm.fetch(query)
                if lm is not None:
                    m.lastfm_listeners = lm.values.get("listeners")
            except Exception as e:  # noqa: BLE001
                log.warning("Last.fm für %r fehlgeschlagen: %s", name, e)
        if i % PROGRESS_EVERY == 0 or i == total:
            log.info("Acts: %d/%d", i, total)
        if i % SAVE_EVERY == 0:
            spotify.save()
            lastfm.save()
    spotify.save()
    lastfm.save()
    return result


def _edition_dict(
    f: Festival, metrics: dict[str, ActMetrics], ratio: float | None, today: date, reference: int | None
) -> dict:
    acts = []
    pts: list[float | None] = []
    for a in sorted(f.acts, key=lambda a: a.position):
        m = metrics.get(normalize_name(a.name)) or ActMetrics()
        listeners, estimated = m.monthly_listeners, False
        if listeners is None and m.lastfm_listeners and ratio:
            listeners, estimated = round(m.lastfm_listeners * ratio), True
        p = scoring.points(listeners)
        pts.append(p)
        acts.append(
            {
                "name": a.name,
                "position": a.position,
                "headliner": a.headliner,
                "spotify_id": m.spotify_id,
                "monthly_listeners": listeners,
                "lastfm_listeners": m.lastfm_listeners,
                "listeners_estimated": estimated,
                "points": round(p, 2) if p is not None else None,
            }
        )
    past = f.end_date < today
    return {
        "year": f.year,
        "status": "past" if past else "upcoming",
        "source": f.source,
        "start_date": f.start_date.isoformat(),
        "end_date": f.end_date.isoformat(),
        "days": f.days,
        "price_eur": f.price_eur,
        "capacity": f.capacity,
        "visitors": f.visitors,
        "act_count": len(f.acts),
        "reported_act_count": f.reported_act_count,
        # Vollständigkeit ist nur für noch nicht stattgefundene Ausgaben aussagekräftig
        "completeness": None if past else scoring.completeness(len(f.acts), reference),
        "scores": scoring.edition_scores(pts, f.days, f.price_eur, scoring.TOP_N),
        "acts": acts,
    }


def build_output(
    editions: dict[str, list[Festival]],
    metrics: dict[str, ActMetrics],
    today: date | None = None,
    now: datetime | None = None,
) -> dict:
    """Baut das JSON laut docs/data-format.md."""
    today = today or date.today()
    now = now or datetime.now(timezone.utc)
    pairs = [
        (m.monthly_listeners, m.lastfm_listeners)
        for m in metrics.values()
        if m.monthly_listeners and m.lastfm_listeners
    ]
    ratio = scoring.estimate_ratio(pairs)
    festivals = []
    for slug, eds in editions.items():
        if not eds:
            continue
        newest_first = sorted(eds, key=lambda f: f.year, reverse=True)
        past = [f for f in newest_first if f.end_date < today]
        reference = (past[0].reported_act_count or len(past[0].acts)) if past else None
        festivals.append(
            {
                "slug": slug,
                "name": newest_first[0].name,
                "location": next((f.location for f in newest_first if f.location), None),
                "editions": [_edition_dict(f, metrics, ratio, today, reference) for f in newest_first],
            }
        )
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "scoring": {"points_zero_listeners": scoring.POINTS_ZERO_LISTENERS, "top_n": scoring.TOP_N},
        "festivals": festivals,
    }


def _fmt(v: float | None, width: int, spec: str) -> str:
    return f"{'-':>{width}}" if v is None else f"{v:>{width}{spec}}"


def format_table(output: dict) -> str:
    lines = [
        f"{'Festival':<22}{'Jahr':>5}{'Acts':>6}{'Vollst.':>8}{'total':>8}"
        f"{'headl.':>8}{'/Tag':>8}{'/Euro':>8}{'ohne':>6}"
    ]
    for fest in output["festivals"]:
        for e in fest["editions"]:
            s = e["scores"]
            missing = sum(a["points"] is None for a in e["acts"])
            lines.append(
                f"{fest['name'][:21]:<22}{e['year']:>5}{e['act_count']:>6}"
                f"{_fmt(e['completeness'], 8, '.2f')}{_fmt(s['total'], 8, '.1f')}"
                f"{_fmt(s['headliner'], 8, '.2f')}{_fmt(s['per_day'], 8, '.1f')}"
                f"{_fmt(s['per_euro'], 8, '.3f')}{missing:>6}"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Festival-Lineups bewerten und festivals.json erzeugen.")
    ap.add_argument("--no-history", action="store_true", help="keine historischen Ausgaben laden")
    ap.add_argument("--only", nargs="+", metavar="SLUG", help="nur diese Festivals")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    load_dotenv(PROJECT_ROOT / ".env")

    from festival_score.sources.lastfm import LastfmSource
    from festival_score.sources.spotify import SpotifySource

    history_provider = None
    if not args.no_history:
        try:
            from festival_score.history import fetch_history

            history_provider = fetch_history
        except ImportError:
            log.info("festival_score.history nicht vorhanden - laufe ohne Historie")

    slugs = args.only or scraper.load_slugs()
    editions = collect_editions(slugs, history_provider)
    all_festivals = [f for eds in editions.values() for f in eds]
    if not all_festivals:
        log.error("Keine Festivals geladen")
        return 1

    metrics = enrich(all_festivals, SpotifySource(), LastfmSource(), load_overrides())
    output = build_output(editions, metrics)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(format_table(output))
    print(f"\nGeschrieben: {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
