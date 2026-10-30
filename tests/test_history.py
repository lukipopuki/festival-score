from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import httpx
import pytest

from festival_score import history, history_setlistfm as sf, history_wikipedia as hw
from festival_score.history import build_acts, fetch_history, load_history_config
from festival_score.scraper import load_slugs

FIX = Path(__file__).parent / "fixtures" / "wikipedia"


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(hw, "REQUEST_PAUSE_S", 0)
    monkeypatch.setattr(sf, "REQUEST_PAUSE_S", 0)
    monkeypatch.setattr(sf, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("SETLISTFM_API_KEY", raising=False)


def slf_setlist(artist: str, d: str) -> dict:
    return {"eventDate": d, "artist": {"name": artist, "mbid": "m"}, "venue": {"id": "22222222"}}


def make_client(with_setlistfm: bool, calls: list | None = None) -> httpx.Client:
    wikitext = (FIX / "hurricane.wikitext").read_text(encoding="utf-8")

    def handler(req: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(req.url))
        if req.url.host == "de.wikipedia.org":
            return httpx.Response(200, json={"parse": {"wikitext": {"*": wikitext}}})
        assert with_setlistfm
        if req.url.path.endswith("/search/venues"):
            v = [{"id": "22222222", "name": "Eichenring"}]
            return httpx.Response(200, json={"itemsPerPage": 30, "page": 1, "total": 1, "venue": v})
        year = req.url.params["year"]
        p = int(req.url.params["p"])
        if year == "2024":
            pages = {
                1: [slf_setlist("Zartmann", "21-06-2024"), slf_setlist("Ed Sheeran", "21-06-2024")],
                2: [slf_setlist("Bad Nerves", "23-06-2024"), slf_setlist("Rammstein", "28-09-2024")],
            }
            return httpx.Response(200, json={"itemsPerPage": 2, "page": p, "total": 4, "setlist": pages[p]})
        return httpx.Response(404, json={})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_config_covers_all_festivals():
    cfg = load_history_config()
    assert set(load_slugs()) == set(cfg)
    assert all("setlistfm_venue" in c and "wikipedia" in c for c in cfg.values())


def test_build_acts_order_and_dedupe():
    acts = build_acts(["Muse", "K.I.Z"], ["zebra", "Alpha", "muse", "Éclair"])
    assert [a.name for a in acts] == ["Muse", "K.I.Z", "Alpha", "Éclair", "zebra"]
    assert [a.headliner for a in acts] == [True, True, False, False, False]
    assert [a.position for a in acts] == [0, 1, 2, 3, 4]
    assert acts[1].slug == "k-i-z"


def test_merge_with_setlistfm(monkeypatch):
    monkeypatch.setenv("SETLISTFM_API_KEY", "KEY")
    # Wikipedia-Lineup ausblenden, damit nur Headliner + setlist.fm übrig bleiben
    f, = fetch_history("hurricane", [2024], make_client(True))
    assert f.source == "setlistfm+wikipedia"
    assert (f.start_date, f.end_date) == (date(2024, 6, 21), date(2024, 6, 23))
    assert (f.visitors, f.price_eur, f.reported_act_count, f.capacity) == (75000, 199.0, 87, None)
    assert f.url == "https://de.wikipedia.org/wiki/Hurricane_Festival"
    heads = [a.name for a in f.acts if a.headliner]
    assert heads == ["Ed Sheeran", "K.I.Z", "Avril Lavigne", "Bring Me the Horizon", "Deichkind"]
    assert [a.name for a in f.acts[:5]] == heads  # Headliner zuerst, auch die bei setlist.fm fehlenden
    assert "Rammstein" not in {a.name for a in f.acts}  # anderes Konzert an derselben Venue
    assert sum(a.name == "Ed Sheeran" for a in f.acts) == 1
    assert "Zartmann" in {a.name for a in f.acts} and "Bad Nerves" in {a.name for a in f.acts}


def test_year_without_data_is_omitted(monkeypatch):
    monkeypatch.setenv("SETLISTFM_API_KEY", "KEY")
    fests = fetch_history("hurricane", [2020, 2021, 2024, 2019], make_client(True))
    assert [f.year for f in fests] == [2024]  # 2020/2021 ausgefallen, 2019 nicht im Ausschnitt


def test_without_key_wikipedia_only(caplog):
    calls: list[str] = []
    with caplog.at_level(logging.WARNING):
        fests = fetch_history("hurricane", [2024, 2025], make_client(False, calls))
    assert [f.year for f in fests] == [2024, 2025]
    assert all(f.source == "wikipedia" for f in fests)
    assert any("SETLISTFM_API_KEY" in r.message for r in caplog.records)
    assert all("setlist.fm" not in c for c in calls)
    assert [a.name for a in fests[0].acts if a.headliner][:2] == ["Ed Sheeran", "K.I.Z"]
    assert len(fests[0].acts) > 50  # volle Wikipedia-Liste (Klappleiste)


def test_unknown_date_falls_back_to_july_1(monkeypatch, caplog):
    text = "{| class=\"wikitable\"\n! Jahr !! Bands !! Besucher\n|-\n| 2024 || 100 || 85.000\n|}\n"
    wiki_json = {"parse": {"wikitext": {"*": text}}}

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=wiki_json)

    with caplog.at_level(logging.WARNING):
        f, = fetch_history("wacken-open-air", [2024], httpx.Client(transport=httpx.MockTransport(handler)))
    assert f.start_date == f.end_date == date(2024, 7, 1)
    assert f.visitors == 85000 and f.reported_act_count == 100 and f.acts == []
    assert any("Termin unbekannt" in r.message for r in caplog.records)


def test_date_inferred_from_setlistfm_when_wikipedia_has_none(monkeypatch):
    monkeypatch.setenv("SETLISTFM_API_KEY", "KEY")
    text = "{| class=\"wikitable\"\n! Jahr !! Bands !! Besucher\n|-\n| 2024 || 100 || 85.000\n|}\n"

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "de.wikipedia.org":
            return httpx.Response(200, json={"parse": {"wikitext": {"*": text}}})
        if req.url.path.endswith("/search/venues"):
            return httpx.Response(200, json={"venue": [{"id": "22222222", "name": "Wacken Open Air"}], "total": 1})
        items = [slf_setlist(f"Band {i}", f"{1 + i % 3:02d}-08-2024") for i in range(9)]
        items.append(slf_setlist("Anderes Konzert", "15-03-2024"))
        return httpx.Response(200, json={"itemsPerPage": 50, "page": 1, "total": 10, "setlist": items})

    f, = fetch_history("wacken-open-air", [2024], httpx.Client(transport=httpx.MockTransport(handler)))
    assert (f.start_date, f.end_date) == (date(2024, 8, 1), date(2024, 8, 3))
    assert len(f.acts) == 9 and f.source == "setlistfm+wikipedia"


def test_festival_without_wikipedia_uses_setlistfm_only(monkeypatch):
    monkeypatch.setenv("SETLISTFM_API_KEY", "KEY")

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.host == "api.setlist.fm"
        if req.url.path.endswith("/search/venues"):
            return httpx.Response(200, json={"venue": [{"id": "22222222", "name": "Olympiastadion Berlin"}], "total": 1})
        if req.url.params["year"] != "2024":
            return httpx.Response(404)
        items = [slf_setlist(f"Band {i}", f"{9 + i % 2:02d}-09-2024") for i in range(8)]
        return httpx.Response(200, json={"itemsPerPage": 50, "page": 1, "total": 8, "setlist": items})

    fests = fetch_history("lollapalooza-berlin", [2023, 2024], httpx.Client(transport=httpx.MockTransport(handler)))
    assert [f.year for f in fests] == [2024]
    f = fests[0]
    assert f.source == "setlistfm" and f.visitors is None and f.price_eur is None
    assert not any(a.headliner for a in f.acts) and len(f.acts) == 8
    assert f.url.endswith("lollapalooza-berlin")


def test_unknown_slug():
    with pytest.raises(KeyError):
        fetch_history("gibt-es-nicht", [2024], make_client(False))
