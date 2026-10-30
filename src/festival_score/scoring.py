"""Bewertung: Punkte pro Act und Kennzahlen pro Ausgabe (reine Funktionen, kein I/O)."""

from __future__ import annotations

import math
import statistics

POINTS_ZERO_LISTENERS = 10_000
TOP_N = 5
TOP_DEPTH = 30  # robuster Vergleichswert, unabhängig von Lineup-Größe und Datenlücken
MIN_RATIO_PAIRS = 5


def points(monthly_listeners: int | None) -> float | None:
    """log10(Hörer) - log10(10k), mindestens 0; None ohne Wert."""
    if monthly_listeners is None:
        return None
    if monthly_listeners <= POINTS_ZERO_LISTENERS:
        return 0.0
    return max(0.0, math.log10(monthly_listeners) - math.log10(POINTS_ZERO_LISTENERS))


def estimate_ratio(pairs: list[tuple[int, int]]) -> float | None:
    """Median von spotify/lastfm über Paare mit beiden Werten > 0; None bei < 5 Paaren."""
    ratios = [s / l for s, l in pairs if s and l and s > 0 and l > 0]
    if len(ratios) < MIN_RATIO_PAIRS:
        return None
    return statistics.median(ratios)


def edition_scores(
    points: list[float | None], days: int, price_eur: float | None, top_n: int = TOP_N
) -> dict:
    """total/headliner/depth/per_day/per_euro; Acts ohne Punkte werden ignoriert."""
    values = sorted((p for p in points if p is not None), reverse=True)
    total = sum(values)
    top = values[:top_n]
    rest = values[top_n:]
    return {
        "total": round(total, 2),
        "top30": round(sum(values[:TOP_DEPTH]), 2),
        "headliner": round(sum(top) / len(top), 2) if top else None,
        "depth": round(statistics.median(rest), 2) if rest else None,
        "per_day": round(total / days, 2) if days > 0 else None,
        "per_euro": round(total / price_eur, 4) if price_eur else None,
    }


def strength_ratio(top30: float, previous_top30: float | None) -> float | None:
    """top30 relativ zur Vorjahresausgabe (1.0 = gleich stark); None ohne Referenz."""
    if not previous_top30:
        return None
    return round(top30 / previous_top30, 2)


def completeness(act_count: int, reference_count: int | None) -> float | None:
    """Anteil gegenüber der Referenz-Ausgabe (max 1.0); None ohne Referenz."""
    if not reference_count or reference_count <= 0:
        return None
    return round(min(1.0, act_count / reference_count), 2)
