"""Tests für die Spotify-Quelle, komplett ohne Netzwerk."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from festival_score.cache import MetricsCache
from festival_score.sources import spotify
from festival_score.sources.spotify import (
    SpotifyAuthError,
    SpotifySource,
    parse_monthly_listeners,
    pick_best_match,
)

FIX = Path(__file__).parent / "fixtures" / "spotify"
MUSE_ID = "12Chz98pHFMPJEknJQMWvI"


def load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def items(name: str) -> list[dict]:
    return load(name)["artists"]["items"]


def make_source(tmp_path, search_fixture: str = "search_muse.json", calls: list | None = None, page_status=200):
    calls = calls if calls is not None else []
    page = (FIX / "artist_page_muse.html").read_text(encoding="utf-8")

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if req.url.host == "accounts.spotify.com":
            assert req.headers["authorization"].startswith("Basic ")
            assert b"grant_type=client_credentials" in req.content
            return httpx.Response(200, json=load("token.json"))
        if req.url.host == "api.spotify.com":
            assert req.headers["authorization"] == "Bearer test-token-abc"
            assert req.url.params["type"] == "artist" and req.url.params["market"] == "DE"
            return httpx.Response(200, json=load(search_fixture))
        if req.url.host == "open.spotify.com":
            assert req.headers["accept-language"] == "en"
            return httpx.Response(page_status, text=page)
        raise AssertionError(req.url)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cache = MetricsCache("spotify", cache_dir=tmp_path)
    return SpotifySource(client=client, cache=cache, min_page_interval=0), calls


@pytest.fixture(autouse=True)
def creds(monkeypatch):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "secret")
    monkeypatch.setattr(spotify, "load_dotenv", lambda *a, **k: None)


# --- pick_best_match ---


def test_exact_match():
    assert pick_best_match("Muse", items("search_muse.json"))["id"] == MUSE_ID


def test_ambiguous_picks_most_popular_exact():
    assert pick_best_match("Kontra K", items("search_ambiguous.json"))["id"] == "BBBBBBBBBBBBBBBBBBBBBB"


def test_no_match():
    assert pick_best_match("Muse", items("search_nomatch.json")) is None
    assert pick_best_match("Muse", []) is None


def test_the_prefix_and_ampersand():
    assert pick_best_match("Killers", items("search_the.json"))["name"] == "The Killers"
    assert pick_best_match("The Killers", items("search_the.json"))["name"] == "The Killers"
    both = [{"id": "x", "name": "Simon and Garfunkel", "popularity": 1}]
    assert pick_best_match("Simon & Garfunkel", both)["id"] == "x"


def test_missing_popularity_does_not_crash():
    assert pick_best_match("Muse", items("search_no_popularity.json"))["id"] == MUSE_ID


# --- parse_monthly_listeners ---


def test_parse_fixture():
    html = (FIX / "artist_page_muse.html").read_text(encoding="utf-8")
    n = parse_monthly_listeners(html)
    assert n is not None and n > 1_000_000


@pytest.mark.parametrize(
    "text,expected",
    [
        ("22.3M", 22_300_000),
        ("512.4K", 512_400),
        ("8,123", 8123),
        ("812", 812),
        ("1.2B", 1_200_000_000),
    ],
)
def test_parse_number_formats(text, expected):
    html = f'<head><meta property="og:description" content="Artist · {text} monthly listeners."/></head>'
    assert parse_monthly_listeners(html) == expected


def test_parse_failure_returns_none():
    assert parse_monthly_listeners("<html></html>") is None
    assert parse_monthly_listeners("") is None
    html = '<meta property="og:description" content="Künstler · 22,3 Mio. monatliche Hörer"/>'
    assert parse_monthly_listeners(html) is None


# --- Quelle ---


def test_fetch_full(tmp_path):
    src, calls = make_source(tmp_path)
    m = src.fetch("Muse")
    assert m.source == "spotify" and m.source_id == MUSE_ID and m.matched_name == "Muse"
    assert m.values["popularity"] == 78 and m.values["followers"] == 18_500_000
    assert m.values["monthly_listeners"] > 1_000_000
    assert "genres" not in m.values
    assert [c.url.host for c in calls] == ["accounts.spotify.com", "api.spotify.com", "open.spotify.com"]


def test_missing_api_fields_give_none(tmp_path):
    src, _ = make_source(tmp_path, "search_no_popularity.json")
    m = src.fetch("Muse")
    assert m.values["popularity"] is None and m.values["followers"] is None
    assert m.values["monthly_listeners"] is not None


def test_page_failure_gives_none_listeners(tmp_path):
    src, _ = make_source(tmp_path, page_status=500)
    assert src.fetch("Muse").values["monthly_listeners"] is None


def test_no_match_returns_none_and_is_cached(tmp_path):
    src, calls = make_source(tmp_path, "search_nomatch.json")
    assert src.fetch("Muse") is None
    n = len(calls)
    assert src.fetch("Muse") is None
    assert len(calls) == n


def test_token_cached_between_searches(tmp_path):
    src, calls = make_source(tmp_path)
    src.fetch("Muse")
    src.fetch("Muse Other")
    assert sum(c.url.host == "accounts.spotify.com" for c in calls) == 1


def test_cache_hit_prevents_http(tmp_path):
    src, calls = make_source(tmp_path)
    src.fetch("Muse")
    src.save()
    # frische Quelle mit gespeichertem Cache und Transport, der nie aufgerufen werden darf
    def boom(req):
        raise AssertionError("HTTP trotz Cache-Treffer")

    src2 = SpotifySource(
        client=httpx.Client(transport=httpx.MockTransport(boom)),
        cache=MetricsCache("spotify", cache_dir=tmp_path),
    )
    m = src2.fetch("muse")
    assert m.source_id == MUSE_ID


def test_retry_after_on_429(tmp_path, monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(spotify.time, "sleep", sleeps.append)
    state = {"n": 0}

    def handler(req):
        if req.url.host == "accounts.spotify.com":
            return httpx.Response(200, json=load("token.json"))
        state["n"] += 1
        if state["n"] < 3:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json=load("search_muse.json"))

    src = SpotifySource(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        cache=MetricsCache("spotify", cache_dir=tmp_path),
    )
    assert src.search("Muse")["id"] == MUSE_ID
    assert sleeps == [2.0, 2.0]


def test_429_gives_up_after_three_attempts(tmp_path, monkeypatch):
    monkeypatch.setattr(spotify.time, "sleep", lambda s: None)

    def handler(req):
        if req.url.host == "accounts.spotify.com":
            return httpx.Response(200, json=load("token.json"))
        return httpx.Response(429, headers={"Retry-After": "0"})

    src = SpotifySource(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        cache=MetricsCache("spotify", cache_dir=tmp_path),
    )
    with pytest.raises(httpx.HTTPStatusError):
        src.search("Muse")


def test_missing_credentials_message(tmp_path, monkeypatch):
    monkeypatch.delenv("SPOTIFY_CLIENT_ID")
    src, _ = make_source(tmp_path)
    with pytest.raises(SpotifyAuthError, match=r"\.env\.example"):
        src.fetch("Muse")
