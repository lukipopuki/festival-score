from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest

from festival_score import scraper
from festival_score.scraper import FestivalNotFound, fetch_festival, parse_festival

FIX = Path(__file__).parent / "fixtures" / "festivalplaner"


def load(slug: str):
    return parse_festival((FIX / f"{slug}.html").read_text(encoding="utf-8"), slug)


def test_hurricane():
    f = load("hurricane")
    assert (f.name, f.year, f.location) == ("Hurricane", 2027, "Scheeßel")
    assert f.start_date == date(2027, 6, 18) and f.end_date == date(2027, 6, 20)
    assert f.days == 3
    assert f.price_eur == 199.0
    assert f.capacity == 78001
    assert len(f.acts) == 44
    assert sum(a.headliner for a in f.acts) == 8
    assert (f.acts[0].name, f.acts[0].slug, f.acts[0].position) == ("Muse", "muse", 0)
    assert [a.position for a in f.acts] == list(range(44))
    assert len({a.slug for a in f.acts}) == 44
    assert any(a.name == "Dexter & The Moonrocks" for a in f.acts)  # Entity dekodiert


def test_wacken():
    f = load("wacken-open-air")
    assert f.price_eur == 351.0
    assert f.capacity == 85000
    assert f.days == 4
    assert len(f.acts) == 50
    assert sum(a.headliner for a in f.acts) == 3


def test_parookaville_nur_headliner():
    f = load("parookaville")
    assert len(f.acts) == 4 and all(a.headliner for a in f.acts)
    assert f.price_eur == 269.0  # Fixture: "269 € für Wochenendtickets"
    assert f.capacity is None


def test_preisformate():
    assert scraper._parse_number("ab 89,90 Euro") == 89.9
    assert scraper._parse_number("1.299 Euro") == 1299.0
    assert scraper._parse_number("") is None
    assert scraper._parse_number("noch offen") is None


def test_fetch_festival_mock():
    html = (FIX / "hurricane.html").read_text(encoding="utf-8")
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, text=html)

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        f = fetch_festival("hurricane", c)
    assert f.name == "Hurricane"
    assert str(seen[0].url) == "https://www.festivalplaner.de/festivals/hurricane"


def test_fetch_festival_404():
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404))) as c:
        with pytest.raises(FestivalNotFound, match="nope"):
            fetch_festival("nope", c)


def test_fetch_all_ueberspringt_fehler(monkeypatch):
    monkeypatch.setattr(scraper.time, "sleep", lambda s: None)
    html = (FIX / "hurricane.html").read_text(encoding="utf-8")

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("kaputt"):
            return httpx.Response(404)
        return httpx.Response(200, text=html)

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        res = scraper.fetch_all(["kaputt", "hurricane"], c)
    assert [f.slug for f in res] == ["hurricane"]


def test_load_slugs():
    slugs = scraper.load_slugs()
    assert len(slugs) == 9 and "hurricane" in slugs
