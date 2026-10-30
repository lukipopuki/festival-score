from __future__ import annotations

import pytest

from festival_score import history_openairguide as oag
from festival_score import history_wikipedia as hw


@pytest.fixture(autouse=True)
def _offline_defaults(monkeypatch, tmp_path):
    """Keine Pausen, Dateicache von openairguide im Temp-Verzeichnis."""
    monkeypatch.setattr(hw, "REQUEST_PAUSE_S", 0)
    monkeypatch.setattr(oag, "REQUEST_PAUSE_S", 0)
    monkeypatch.setattr(oag, "CACHE_DIR", tmp_path / "oag-cache")
