"""Gemeinsames Datenmodell. Alle Module tauschen Daten nur über diese Klassen aus."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any


@dataclass
class Act:
    name: str
    slug: str  # Künstler-Slug bei festivalplaner.de, z. B. "phoebe-bridgers"
    position: int  # 0-basierte Reihenfolge im Lineup (Billing-Proxy)
    headliner: bool


@dataclass
class Festival:
    slug: str  # festivalplaner.de-Slug, z. B. "hurricane"
    name: str
    year: int
    start_date: date
    end_date: date
    url: str
    price_eur: float | None = None
    capacity: int | None = None
    location: str | None = None
    acts: list[Act] = field(default_factory=list)
    visitors: int | None = None  # tatsächliche Besucher (nur vergangene Ausgaben)
    reported_act_count: int | None = None  # offizielle Anzahl Acts laut Quelle, falls bekannt
    source: str = "festivalplaner"  # "festivalplaner", "setlistfm", "wikipedia", ...

    @property
    def days(self) -> int:
        return (self.end_date - self.start_date).days + 1

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["start_date"] = self.start_date.isoformat()
        d["end_date"] = self.end_date.isoformat()
        d["days"] = self.days
        return d


@dataclass
class ArtistMetrics:
    """Kennzahlen eines Künstlers aus genau einer Quelle."""

    query_name: str  # Name, nach dem gesucht wurde (aus dem Lineup)
    source: str  # "spotify", "lastfm", ...
    values: dict[str, float | int | None]  # z. B. {"popularity": 71, "followers": 1200000}
    source_id: str | None = None  # ID bei der Quelle, z. B. Spotify-Artist-ID
    matched_name: str | None = None  # Name, den die Quelle zurückgegeben hat
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["fetched_at"] = self.fetched_at.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ArtistMetrics:
        d = dict(d)
        d["fetched_at"] = datetime.fromisoformat(d["fetched_at"])
        return cls(**d)
