from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import httpx
import pytest

from festival_score.history import build_acts, fetch_history, load_history_config
from festival_score.scraper import load_slugs

FIX = Path(__file__).parent / "fixtures"
WIKI = FIX / "wikipedia"
OAG = FIX / "openairguide"

TABLE_BANDS = '{| class="wikitable"\n! Jahr !! Bands !! Besucher\n|-\n| 2024 || 100 || 85.000\n|}\n'
TABLE_NO_BANDS = '{| class="wikitable"\n! Jahr !! Besucher\n|-\n| 2024 || 85.000\n|}\n'


def make_client(oag_pages: dict[str, str] | None = None, wikitext: str | None = None, calls: list | None = None):
    """Wikipedia: Hurricane-Fixture (oder `wikitext`); openairguide: Pfad -> HTML, sonst 404."""
    wikitext = wikitext or (WIKI / "hurricane.wikitext").read_text(encoding="utf-8")

    def handler(req: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(req.url))
        if req.url.host == "de.wikipedia.org":
            return httpx.Response(200, json={"parse": {"wikitext": {"*": wikitext}}})
        assert req.url.host == "www.openairguide.net", req.url
        body = (oag_pages or {}).get(req.url.path.removeprefix("/festivals/deutschland/"))
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, content=body.encode("utf-8"))

    return httpx.Client(transport=httpx.MockTransport(handler))


def oag_html(name: str) -> str:
    return (OAG / f"{name}.html").read_text(encoding="utf-8")


def test_config_covers_all_festivals():
    cfg = load_history_config()
    assert set(load_slugs()) == set(cfg)
    assert all("wikipedia" in c for c in cfg.values())
    assert not any("setlistfm_venue" in c for c in cfg.values())
    assert cfg["highfield-festival"]["openairguide"] == "highfield-festival"
    assert "openairguide" not in cfg["parookaville"]


def test_build_acts_order_and_dedupe():
    acts = build_acts(["Muse", "K.I.Z"], ["zebra", "Alpha", "muse", "Éclair"])
    assert [a.name for a in acts] == ["Muse", "K.I.Z", "zebra", "Alpha", "Éclair"]  # Reihenfolge bleibt
    assert [a.headliner for a in acts] == [True, True, False, False, False]
    assert [a.position for a in acts] == [0, 1, 2, 3, 4]
    assert acts[1].slug == "k-i-z"


def test_merge_openairguide_with_wikipedia():
    # Wikipedia = Hurricane-Fixture (2024: Preis 199, 75.000 Besucher, 87 Bands); openairguide = Wacken 2024
    c = make_client({"hurricane/_history/2024": oag_html("wacken-2024")})
    f, = fetch_history("hurricane", [2024], c)
    assert f.source == "openairguide+wikipedia"
    assert (f.start_date, f.end_date) == (date(2024, 7, 31), date(2024, 8, 3))  # Termin aus openairguide
    assert (f.visitors, f.price_eur, f.reported_act_count) == (75000, 199.0, 87)  # aus Wikipedia
    assert f.url == "https://www.openairguide.net/festivals/deutschland/hurricane/_history/2024"
    heads = [a.name for a in f.acts if a.headliner]
    assert heads == ["Scorpions", "Amon Amarth", "In Extremo", "KoRn"]  # nicht die Wikipedia-Headliner
    assert [a.name for a in f.acts[:4]] == heads
    assert len(f.acts) == 180
    assert [a.name for a in f.acts[4:7]] == ["Inherited", "Etterna", "Voidwomb"]  # Seitenreihenfolge


def test_reported_count_from_openairguide_if_wikipedia_has_none():
    c = make_client({"wacken-open-air/_history/2024": oag_html("wacken-2024")}, wikitext=TABLE_NO_BANDS)
    f, = fetch_history("wacken-open-air", [2024], c)
    assert f.reported_act_count == 180 and f.visitors == 85000 and len(f.acts) == 180


def test_openairguide_only_when_wikipedia_has_no_article():
    calls: list[str] = []
    c = make_client({"lollapalooza-berlin/_history/2025": oag_html("deichbrand-2025")}, calls=calls)
    fests = fetch_history("lollapalooza-berlin", [2024, 2025], c)
    assert [f.year for f in fests] == [2025]
    f = fests[0]
    assert f.source == "openairguide" and f.visitors is None and f.price_eur is None
    assert f.reported_act_count == 118 and len(f.acts) == 118
    assert not any("wikipedia" in x for x in calls)


def test_wikipedia_fallback_when_openairguide_404():
    fests = fetch_history("hurricane", [2024, 2025], make_client({}))
    assert [f.year for f in fests] == [2024, 2025]
    assert all(f.source == "wikipedia" for f in fests)
    assert [a.name for a in fests[0].acts if a.headliner][:2] == ["Ed Sheeran", "K.I.Z"]
    assert len(fests[0].acts) > 50  # volle Wikipedia-Liste (Klappleiste)
    assert fests[0].url.startswith("https://de.wikipedia.org/")


def test_year_without_data_is_omitted():
    fests = fetch_history("hurricane", [2020, 2021, 2024, 2019], make_client({}))
    assert [f.year for f in fests] == [2024]  # 2020/2021 ausgefallen, 2019 nicht im Ausschnitt


def test_unknown_date_falls_back_to_july_1(caplog):
    with caplog.at_level(logging.WARNING):
        f, = fetch_history("wacken-open-air", [2024], make_client({}, wikitext=TABLE_BANDS))
    assert f.start_date == f.end_date == date(2024, 7, 1)
    assert f.visitors == 85000 and f.reported_act_count == 100 and f.acts == []
    assert any("Termin unbekannt" in r.message for r in caplog.records)


def test_unknown_slug():
    with pytest.raises(KeyError):
        fetch_history("gibt-es-nicht", [2024], make_client({}))
