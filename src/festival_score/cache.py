"""Einfacher JSON-Dateicache pro Quelle, damit APIs nicht bei jedem Lauf neu abgefragt werden."""

from __future__ import annotations

import json
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

from festival_score.models import ArtistMetrics

DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache"


def normalize_name(name: str) -> str:
    """Kleinbuchstaben, ohne Akzente und überflüssige Leerzeichen; Cache-Schlüssel und Namensvergleich."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


class MetricsCache:
    def __init__(self, source: str, cache_dir: Path = DEFAULT_CACHE_DIR, ttl: timedelta = timedelta(days=7)):
        self.path = cache_dir / f"{source}.json"
        self.ttl = ttl
        self._data: dict[str, dict | None] = {}
        if self.path.exists():
            self._data = json.loads(self.path.read_text(encoding="utf-8"))

    def get(self, artist_name: str) -> tuple[bool, ArtistMetrics | None]:
        """(Treffer im Cache?, Wert). Ein gecachtes None bedeutet: Quelle kennt den Künstler nicht."""
        entry = self._data.get(normalize_name(artist_name))
        if entry is None:
            return False, None
        cached_at = datetime.fromisoformat(entry["cached_at"])
        if datetime.now(timezone.utc) - cached_at > self.ttl:
            return False, None
        metrics = entry["metrics"]
        return True, ArtistMetrics.from_dict(metrics) if metrics else None

    def put(self, artist_name: str, metrics: ArtistMetrics | None) -> None:
        self._data[normalize_name(artist_name)] = {
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "metrics": metrics.to_dict() if metrics else None,
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=1), encoding="utf-8")
