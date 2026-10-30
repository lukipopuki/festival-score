"""Schnittstelle, die jede Kennzahlen-Quelle erfüllt."""

from __future__ import annotations

from typing import Protocol

from festival_score.models import ArtistMetrics


class MetricSource(Protocol):
    name: str  # eindeutiger Quellenname, gleichzeitig Cache-Dateiname

    def fetch(self, artist_name: str) -> ArtistMetrics | None:
        """Kennzahlen für einen Künstler holen; None, wenn kein Treffer."""
        ...
