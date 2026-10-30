from __future__ import annotations

from datetime import date

import httpx
import pytest

from festival_score import history_setlistfm as sf
from festival_score.history_setlistfm import (
    Performance,
    SetlistFmError,
    artists_in_window,
    fetch_performances,
    find_venue_ids,
    infer_window,
    parse_setlists,
)


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(sf, "REQUEST_PAUSE_S", 0)
    monkeypatch.setattr(sf, "RETRY_WAIT_S", 0)


def setlist(artist: str, event_date: str, venue_id: str = "abc12345") -> dict:
    """Eintrag im dokumentierten Format von /search/setlists."""
    return {
        "id": "x",
        "versionId": "y",
        "eventDate": event_date,
        "lastUpdated": "2024-06-22T05:18:08.000+0000",
        "artist": {"mbid": "m", "name": artist, "sortName": artist, "disambiguation": "", "url": "u"},
        "venue": {"id": venue_id, "name": "Eichenring", "city": {"name": "Scheeßel"}, "url": "u"},
        "tour": {"name": "t"},
        "set": [],
        "url": "u",
    }


def page(items: list[dict], p: int, per: int, total: int) -> dict:
    return {"type": "setlists", "itemsPerPage": per, "page": p, "total": total, "setlist": items}


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_pagination_headers_and_params():
    calls = []
    data = {
        1: page([setlist("Ed Sheeran", "21-06-2024"), setlist("Zartmann", "21-06-2024")], 1, 2, 5),
        2: page([setlist("Bad Nerves", "22-06-2024"), setlist("Ed Sheeran", "22-06-2024")], 2, 2, 5),
        3: page([setlist("Rammstein", "28-09-2024")], 3, 2, 5),
    }

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        assert req.headers["x-api-key"] == "KEY" and req.headers["accept"] == "application/json"
        assert req.url.path == "/rest/1.0/search/setlists"
        assert req.url.params["venueId"] == "abc12345" and req.url.params["year"] == "2024"
        return httpx.Response(200, json=data[int(req.url.params["p"])])

    perfs = fetch_performances(["abc12345"], 2024, client(handler), "KEY")
    assert len(calls) == 3 and len(perfs) == 5
    assert perfs[0] == Performance(date(2024, 6, 21), "Ed Sheeran")


def test_date_filter_and_unique_artists():
    perfs = parse_setlists(
        [
            setlist("Zartmann", "21-06-2024"),
            setlist("ed sheeran", "22-06-2024"),
            setlist("Ed Sheeran", "23-06-2024"),  # Duplikat (normalize_name)
            setlist("Rammstein", "28-09-2024"),  # anderes Konzert an derselben Venue
            {"eventDate": "kaputt", "artist": {"name": "X"}},
        ]
    )
    assert len(perfs) == 4
    names = artists_in_window(perfs, date(2024, 6, 21), date(2024, 6, 23))
    assert names == ["ed sheeran", "Zartmann"]  # eindeutig, alphabetisch, ohne Rammstein


def test_infer_window_from_setlists():
    perfs = [Performance(date(2024, 6, 21 + i % 3), f"A{i}") for i in range(12)]
    perfs.append(Performance(date(2024, 9, 28), "Solo"))
    assert infer_window(perfs) == (date(2024, 6, 21), date(2024, 6, 23))
    assert infer_window([Performance(date(2024, 9, 28), "Solo")]) is None


def test_venue_search_prefers_name_match_and_accepts_id():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/rest/1.0/search/venues"
        assert req.url.params["name"] == "Eichenring" and req.url.params["country"] == "DE"
        venues = [{"id": "11111111", "name": "Hamburg Arena"}, {"id": "22222222", "name": "Eichenring (Hurricane)"}]
        return httpx.Response(200, json={"type": "venues", "itemsPerPage": 30, "page": 1, "total": 2, "venue": venues})

    assert find_venue_ids("Eichenring", client(handler), "KEY") == ["22222222"]
    assert find_venue_ids("deadbeef", client(lambda r: pytest.fail("kein Request")), "KEY") == ["deadbeef"]


def test_404_means_no_results():
    c = client(lambda r: httpx.Response(404, json={"code": 404, "message": "not found"}))
    assert fetch_performances(["abc12345"], 2019, c, "KEY") == []
    assert find_venue_ids("Nirgendwo", c, "KEY") == []


def test_retry_on_429_then_success():
    n = {"calls": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        n["calls"] += 1
        if n["calls"] < 3:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json=page([setlist("A", "21-06-2024")], 1, 20, 1))

    assert len(fetch_performances(["abc12345"], 2024, client(handler), "KEY")) == 1
    assert n["calls"] == 3


def test_429_gives_up_after_three_retries():
    n = {"calls": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        n["calls"] += 1
        return httpx.Response(429)

    with pytest.raises(SetlistFmError):
        fetch_performances(["abc12345"], 2024, client(handler), "KEY")
    assert n["calls"] == 4  # 1 Versuch + 3 Wiederholungen


def test_api_key_from_env(monkeypatch):
    monkeypatch.setattr(sf, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("SETLISTFM_API_KEY", raising=False)
    assert sf.get_api_key() is None
    monkeypatch.setenv("SETLISTFM_API_KEY", "abc")
    assert sf.get_api_key() == "abc"
