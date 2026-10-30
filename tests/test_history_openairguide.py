from __future__ import annotations

import os
import time
from datetime import date
from pathlib import Path

import httpx
import pytest

from festival_score import history_openairguide as oag
from festival_score.history_openairguide import fetch_current, fetch_edition, parse_date_range, parse_edition

FIX = Path(__file__).parent / "fixtures" / "openairguide"


def html(name: str) -> str:
    return oag.decode_html((FIX / f"{name}.html").read_bytes())


def client_for(pages: dict[str, str], calls: list[str] | None = None) -> httpx.Client:
    def handler(req: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(req.url))
        assert req.headers["user-agent"].startswith("festival-score/")
        body = pages.get(req.url.path.removeprefix("/festivals/deutschland/"))
        if body is None:
            return httpx.Response(404, text="nicht gefunden")
        return httpx.Response(200, content=body.encode("utf-8"), headers={"content-type": "text/html; charset=utf-8"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_parse_deichbrand_2025():
    ed = parse_edition(html("deichbrand-2025"))
    assert ed is not None and ed.year == 2025
    assert (ed.start_date, ed.end_date) == (date(2025, 7, 17), date(2025, 7, 20))
    assert ed.headliners == ["Kontra K", "Macklemore", "K.I.Z", "Deichkind", "Finch"]
    assert ed.reported_count == 118 and len(ed.bands) == 118 and len(set(ed.bands)) == 118
    # Seitenreihenfolge: Donnerstag 15:00 zuerst, nicht alphabetisch
    assert ed.bands[:3] == ["Headjet", "Marie Bothmer", "Vargo"]
    assert {"Kontra K", "Macklemore"} <= set(ed.bands)


def test_parse_wacken_2024_umlauts_and_dates():
    ed = parse_edition(html("wacken-2024"))
    assert ed is not None
    assert (ed.start_date, ed.end_date) == (date(2024, 7, 31), date(2024, 8, 3))
    assert ed.headliners == ["Scorpions", "Amon Amarth", "In Extremo", "KoRn"]
    assert len(ed.bands) == ed.reported_count == 180
    assert any(c in "".join(ed.bands) for c in "äöüÄÖÜ")  # Umlaute korrekt dekodiert
    assert "�" not in "".join(ed.bands)


def test_decode_fallback_cp1252():
    assert oag.decode_html("Tür & Tor".encode("cp1252")) == "Tür & Tor"
    assert oag.decode_html("Tür".encode("utf-8")) == "Tür"


def test_parse_current_page():
    ed = parse_edition(html("deichbrand-current"))
    assert ed is not None and ed.year == 2027
    assert (ed.start_date, ed.end_date) == (date(2027, 7, 15), date(2027, 7, 18))
    assert ed.reported_count == 31
    assert ed.bands[:3] == ["AnnenMayKantereit", "Cro", "Finch"] and len(ed.bands) == 5


def test_empty_or_cancelled_edition_is_none():
    page = "<html><head><title>Deichbrand 2020</title></head><body><div id='bandliste'></div></body></html>"
    assert parse_edition(page) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("vom 17. bis 20. Juli 2025", (date(2025, 7, 17), date(2025, 7, 20))),
        ("31. Juli bis 03. August 2024", (date(2024, 7, 31), date(2024, 8, 3))),
        ("Donnerstag, 15. bis Sonntag, 18. Juli 2027", (date(2027, 7, 15), date(2027, 7, 18))),
        ("17. und 18. Juli 2027", (date(2027, 7, 17), date(2027, 7, 18))),
        ("am 5. Juli 2025", (date(2025, 7, 5), date(2025, 7, 5))),
        ("nix", None),
    ],
)
def test_parse_date_range(text, expected):
    assert parse_date_range(text) == expected


def test_fetch_edition_and_cache():
    calls: list[str] = []
    c = client_for({"deichbrand/_history/2025": html("deichbrand-2025")}, calls)
    ed = fetch_edition("deichbrand", 2025, c)
    assert ed and len(ed.bands) == 118
    assert ed.url == "https://www.openairguide.net/festivals/deutschland/deichbrand/_history/2025"
    assert (oag.CACHE_DIR / "deichbrand-2025.html").exists()
    ed2 = fetch_edition("deichbrand", 2025, c)
    assert ed2 and ed2.bands == ed.bands
    assert len(calls) == 1  # zweiter Aufruf aus dem Cache


def test_404_returns_none():
    assert fetch_edition("parookaville", 2025, client_for({})) is None
    assert fetch_current("parookaville", client_for({})) is None


def test_wrong_year_page_is_none():
    c = client_for({"deichbrand/_history/2023": html("deichbrand-2025")})
    assert fetch_edition("deichbrand", 2023, c) is None


def test_fetch_current_uses_confirmed_page_and_daily_cache():
    calls: list[str] = []
    pages = {"deichbrand": html("deichbrand-current"), "deichbrand/_confirmed": html("deichbrand-confirmed")}
    c = client_for(pages, calls)
    ed = fetch_current("deichbrand", c)
    assert ed and ed.year == 2027 and ed.reported_count == 31
    assert len(ed.bands) == 30 and ed.bands[:3] == ["AnnenMayKantereit", "Cro", "Finch"]
    assert ed.headliners[:3] == ["AnnenMayKantereit", "Cro", "Finch"]
    assert len(calls) == 2
    fetch_current("deichbrand", c)
    assert len(calls) == 2  # frisch genug
    old = time.time() - 2 * 24 * 3600
    for f in oag.CACHE_DIR.glob("*.html"):
        os.utime(f, (old, old))
    fetch_current("deichbrand", c)
    assert len(calls) == 4  # nach einem Tag neu geholt


def test_pause_between_requests(monkeypatch):
    monkeypatch.setattr(oag, "REQUEST_PAUSE_S", 0.2)
    page = html("deichbrand-2025")
    c = client_for({"deichbrand/_history/2025": page, "deichbrand/_history/2024": page})
    fetch_edition("deichbrand", 2025, c)
    t = time.monotonic()
    fetch_edition("deichbrand", 2024, c)
    assert time.monotonic() - t >= 0.15
