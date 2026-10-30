from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest

from festival_score import history_wikipedia as hw
from festival_score.history_wikipedia import (
    clean_text,
    parse_band_count,
    parse_date_range,
    parse_number,
    parse_price,
    parse_visitors,
    parse_wikitext,
    split_names,
)

FIX = Path(__file__).parent / "fixtures" / "wikipedia"


def years(name: str, **kw):
    return parse_wikitext((FIX / f"{name}.wikitext").read_text(encoding="utf-8"), **kw)


# --- Parser-Bausteine -------------------------------------------------------------------------


def test_numbers():
    assert parse_number("75.000") == 75000
    assert parse_number("231,50") == 231.5
    assert parse_number("199") == 199
    assert parse_visitors("75.000<ref name=\"x\">{{Internetquelle |url=http://a.b |titel=75.001}}</ref>") == 75000
    assert parse_visitors("ca. 35.000") == 35000
    assert parse_visitors("abgesagt") is None
    assert parse_visitors("225.000 erwartet") is None
    assert parse_visitors("—") is None


def test_prices():
    assert parse_price("ab 199 €") == 199
    assert parse_price("159 bis 199 €<ref>x</ref>") == 159
    assert parse_price("249 – 321,50 €") == 249
    assert parse_price("Umtausch des Vorjahrestickets möglich;<br />259 – 279 €") == 259
    assert parse_price("VVK: 231,50 € + Kosten für den Campingplatz") == 231.5
    assert parse_price("59 DM (30,17 €)") == 30.17
    assert parse_price("333{{FN|d}}", plain=True) == 333
    assert parse_price("333", plain=False) is None  # ohne Währung nicht raten
    assert parse_price("") is None


def test_band_count():
    assert parse_band_count("72 Bands<br />3 Open-Air Bühnen") == 72
    assert parse_band_count("73 Bands\n3 Open-Air Bühnen") == 73
    assert parse_band_count("87") == 87
    assert parse_band_count("Bands<br />4 Open-Air-Bühnen") is None


def test_clean_text_links_refs_templates():
    raw = "[[Ziel (Band)|Text]], [[Plain]]<ref name=\"a\">{{Internetquelle |url=x}}</ref><ref name=\"b\" />{{FN|A}} <s>[[Weg]]</s>&nbsp;x"
    assert clean_text(raw) == "Text, Plain x"
    assert clean_text("a<br />b") == "a\nb"
    assert clean_text("[https://x.de Titel] und ''kursiv''") == "Titel und kursiv"


def test_split_names():
    assert split_names("Casper (Rapper), Leoniden (Überraschungsauftritt), Foo & Bar.") == [
        "Casper", "Leoniden", "Foo & Bar"
    ]
    assert split_names("A, B und C", allow_und=True) == ["A", "B", "C"]
    assert split_names("A, Swiss und die Andern") == ["A", "Swiss und die Andern"]


@pytest.mark.parametrize(
    "text,hint,expected",
    [
        ("21. bis 23. Juni", 2024, (date(2024, 6, 21), date(2024, 6, 23))),
        ("5.–7. Juni 2026", None, (date(2026, 6, 5), date(2026, 6, 7))),
        ("21. – 23.06.2024", None, (date(2024, 6, 21), date(2024, 6, 23))),
        ("17.+18. Juli 2020", None, (date(2020, 7, 17), date(2020, 7, 18))),
        ("29. Juli bis 1. August", 2026, (date(2026, 7, 29), date(2026, 8, 1))),
        ("abgesagt<br /><small>geplant für 5.–7. Juni 2020</small>", None, (date(2020, 6, 5), date(2020, 6, 7))),
        ("21. bis 23. Juni", None, None),  # Jahr unbekannt
        ("Fließtext " * 20, 2024, None),
    ],
)
def test_date_range(text, hint, expected):
    assert parse_date_range(text, hint) == expected


# --- Echte Artikel-Ausschnitte --------------------------------------------------------------------


def test_hurricane_table_and_klappleisten():
    y = years("hurricane")
    h24 = y[2024]
    assert (h24.start_date, h24.end_date) == (date(2024, 6, 21), date(2024, 6, 23))
    assert (h24.visitors, h24.price_eur, h24.reported_act_count) == (75000, 199.0, 87)
    assert h24.headliners == ["Ed Sheeran", "K.I.Z", "Avril Lavigne", "Bring Me the Horizon", "Deichkind"]
    assert len(h24.lineup) > 50  # Klappleiste "2024"
    assert "Deichkind" in h24.lineup
    h25 = y[2025]
    assert h25.visitors == 65000 and h25.price_eur == 159.0  # "159 bis 199 €"
    assert h25.reported_act_count is None
    assert "Green Day" in h25.headliners
    h26 = y[2026]
    assert h26.visitors == 78000
    assert "Leoniden" in h26.lineup  # "(Überraschungsauftritt)" entfernt
    assert y[2020].cancelled and y[2021].cancelled


def test_rock_am_ring_full_lineup_no_headliners():
    y = years("rock_am_ring")
    r24 = y[2024]
    assert (r24.start_date, r24.end_date) == (date(2024, 6, 7), date(2024, 6, 9))
    assert (r24.visitors, r24.reported_act_count) == (80000, 72)
    assert r24.price_eur == 231.5
    assert r24.headliners == []
    assert "Querbeat" in r24.lineup and "Pantera" not in r24.lineup
    assert "Body Count ft. Ice-T" in r24.lineup
    assert y[2025].reported_act_count == 101
    assert y[2026].reported_act_count is None  # "Bands" ohne Zahl


def test_rock_im_park_rowspan_labels():
    y = years("rock_im_park")
    assert y[2020].cancelled and y[2021].cancelled
    r24 = y[2024]
    assert r24.headliners == ["Green Day", "Måneskin", "Die Ärzte"]
    assert (r24.visitors, r24.price_eur) == (80000, 249.0)
    assert "Babymetal" in r24.lineup and "Green Day" in r24.lineup
    r25 = y[2025]  # drei Zeilen: Headliner, Co-Headliner, Außerdem
    assert r25.headliners[:3] == ["Slipknot", "Bring Me the Horizon", "Korn"]
    assert "Sleep Token" in r25.headliners and "Beatsteaks" not in r25.headliners
    assert r25.visitors == 88500


def test_southside_headliner_formats():
    y = years("southside")
    assert y[2022].visitors == 65000
    # "Headliner: A, B, … und C" + Folgezeile
    assert y[2024].headliners[0] == "Avril Lavigne" and "The Offspring" in y[2024].headliners
    assert len(y[2024].headliners) == 9
    assert y[2025].headliners == ["Green Day", "AnnenMayKantereit", "The Prodigy"]
    # 2023: erster Satz ohne Label
    assert y[2023].headliners[0] == "Die Ärzte" and "01099" not in y[2023].headliners
    assert "01099" in y[2023].lineup
    assert y[2026].visitors == 59000


def test_wacken_year_table_skips_winter_nights():
    text = (FIX / "wacken.wikitext").read_text(encoding="utf-8")
    polluted = parse_wikitext(text)[2024]
    assert polluted.start_date == date(2024, 3, 1)  # ohne Skip: Termin der Winter Nights
    y = parse_wikitext(text, skip_sections=["Wacken Winter Nights"])
    w24 = y[2024]
    assert w24.start_date is None  # Wikipedia nennt keine Termine
    assert (w24.visitors, w24.price_eur, w24.reported_act_count) == (85000, 333.0, 202)
    assert y[2022].visitors == 100000 and y[2022].reported_act_count == 195
    assert y[2026].price_eur == 351.0 and y[2026].visitors is None
    assert y[2020].visitors is None


def test_parookaville_two_tables():
    y = years("parookaville")
    p24 = y[2024]
    assert (p24.start_date, p24.end_date) == (date(2024, 7, 19), date(2024, 7, 21))
    assert p24.visitors == 225000
    assert p24.headliners[:3] == ["Above & Beyond", "Armin van Buuren", "Dimitri Vegas & Like Mike"]
    assert y[2021].cancelled
    assert y[2026].visitors is None  # "225.000 erwartet"
    assert "Dimitri Vegas" in y[2025].headliners  # "(Vinyl Only)" entfernt


def test_deichbrand_year_sections():
    y = years("deichbrand")
    d24 = y[2024]
    assert (d24.start_date, d24.end_date) == (date(2024, 7, 18), date(2024, 7, 21))
    assert len(d24.lineup) == 36 and "Kings of Leon" in d24.lineup
    assert d24.headliners == []
    assert "Macklemore" in y[2025].lineup


def test_highfield_malformed_cells():
    y = years("highfield")
    h22 = y[2022]
    assert h22.headliners == ["Deichkind", "Broilers", "Kraftklub"]
    assert h22.visitors == 35000  # Zelle über zwei Zeilen: "35.000\n(ausverkauft)"
    h23 = y[2023]
    assert h23.visitors == 35000 and h23.headliners[0] == "K.I.Z"
    h24 = y[2024]  # defektes Attribut `align="center" # rowspan="2"`
    assert (h24.start_date, h24.end_date) == (date(2024, 8, 16), date(2024, 8, 18))
    assert h24.headliners[0] == "Peter Fox" and "Macklemore" in h24.headliners
    assert "Donots" in h24.lineup and h24.visitors == 30000


# --- Abruf -----------------------------------------------------------------------------------


def test_fetch_wikitext_uses_mediawiki_api(monkeypatch):
    monkeypatch.setattr(hw, "REQUEST_PAUSE_S", 0)
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["params"] = dict(req.url.params)
        seen["ua"] = req.headers["user-agent"]
        return httpx.Response(200, json={"parse": {"wikitext": {"*": "== x =="}}})

    c = httpx.Client(transport=httpx.MockTransport(handler))
    assert hw.fetch_wikitext("Hurricane_Festival", c) == "== x =="
    assert seen["params"]["action"] == "parse" and seen["params"]["page"] == "Hurricane_Festival"
    assert seen["params"]["prop"] == "wikitext" and seen["ua"].startswith("festival-score/")


def test_fetch_wikitext_errors(monkeypatch):
    monkeypatch.setattr(hw, "REQUEST_PAUSE_S", 0)
    c = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"error": {"info": "weg"}})))
    with pytest.raises(hw.WikipediaError):
        hw.fetch_wikitext("X", c)
    c = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    with pytest.raises(hw.WikipediaError):
        hw.fetch_wikitext("X", c)
