"""Tests der offiziellen Parser: Rock am Ring/Park, Wacken, Lollapalooza, Parookaville (ohne Netzwerk)."""

from __future__ import annotations

from pathlib import Path

import pytest

from festival_score.official import lollapalooza, parookaville, rockamring, wacken

FIX = Path(__file__).parent / "fixtures" / "official"


def read(*parts: str) -> str:
    return (FIX.joinpath(*parts)).read_text(encoding="utf-8")


# --- Rock am Ring / Rock im Park -------------------------------------------------


@pytest.mark.parametrize("fixture,url", [
    ("rock-am-ring.html", "https://www.rock-am-ring.com/line-up"),
    ("rock-im-park.html", "https://www.rock-im-park.com/line-up"),
])
def test_rockamring(fixture, url):
    lu = rockamring.parse(read("rockamring", fixture), url)
    assert lu is not None
    assert lu.year == 2027
    assert len(lu.acts) == 50
    assert len(set(lu.acts)) == len(lu.acts)
    # Headliner nur als Logo (Name aus img-title), Leerzeichen bereinigt
    assert len(lu.headliners) == 9
    assert "blink-182" in lu.headliners and "Bring Me The Horizon" in lu.headliners
    assert "Beartooth" in lu.acts and "Beartooth" not in lu.headliners
    assert "The Devil Wears Prada" in lu.acts  # dritte, unbeschriftete Gruppe
    assert "SPEED" in lu.acts  # Schreibweise unverändert
    assert lu.url == url


def test_rockamring_reihenfolge():
    lu = rockamring.parse(read("rockamring", "rock-am-ring.html"), "u")
    assert lu.acts[:4] == ["Falling In Reverse", "blink-182", "Billy Talent", "Beartooth"]


def test_rockamring_fremde_seite():
    assert rockamring.parse("<html><body><p>Hallo</p></body></html>", "u") is None
    assert rockamring.parse("", "u") is None


# --- Wacken -----------------------------------------------------------------------


def test_wacken_json_direkt():
    lu = wacken.parse(read("wacken", "bandlist-concert.json"), "https://www.wacken.com/de/line-up/bands/")
    assert lu is not None
    assert lu.year == 2027  # aus Bildpfaden bands2027
    assert lu.acts[:3] == ["Avatar", "Beast In Black", "Belphegor"]
    assert len(lu.acts) == 9  # Duplikat entfernt
    assert lu.acts.count("Avatar") == 1
    assert lu.headliners == []


def test_wacken_html_laedt_json_nach(monkeypatch):
    calls = []

    def fake_get_html(url, client=None):
        calls.append(url)
        return read("wacken", "bandlist-concert.json")

    monkeypatch.setattr(wacken, "get_html", fake_get_html)
    lu = wacken.parse(read("wacken", "line-up.html"), "https://www.wacken.com/de/line-up/bands/")
    assert calls == ["https://www.wacken.com/fileadmin/Json/bandlist-concert.json"]
    assert lu is not None and lu.year == 2027 and "Avatar" in lu.acts


def test_wacken_fremd_oder_leer(monkeypatch):
    monkeypatch.setattr(wacken, "get_html", lambda url, client=None: None)
    assert wacken.parse("<html><body>nix</body></html>", "u") is None
    assert wacken.parse(read("wacken", "line-up.html"), "https://www.wacken.com/de/line-up/bands/") is None
    assert wacken.parse("[]", "u") is None
    assert wacken.parse("[kaputt", "u") is None


# --- Lollapalooza -------------------------------------------------------------------


def test_lollapalooza():
    lu = lollapalooza.parse(read("lollapalooza", "lineup.html"), "https://www.lollapaloozade.com/lineup")
    assert lu is not None
    assert lu.year == 2027
    assert lu.acts == ["Robbie Williams", "The Neighbourhood", "sombr"]
    assert lu.headliners == ["Robbie Williams", "The Neighbourhood", "sombr"]


def test_lollapalooza_fremd():
    assert lollapalooza.parse("<html><body><h1>404</h1></body></html>", "u") is None


# --- Parookaville ---------------------------------------------------------------------


def test_parookaville_json_direkt():
    lu = parookaville.parse(read("parookaville", "search-artist.json"), "https://www.parookaville.com/de/line-up")
    assert lu is not None
    assert lu.year == 2027
    assert lu.acts == ["BUNT.", "Don Diablo", "Lost Frequencies", "Steve Aoki"]
    assert lu.headliners == []


def test_parookaville_html_laedt_json_nach(monkeypatch):
    calls = []

    def fake_get_html(url, client=None):
        calls.append(url)
        return read("parookaville", "search-artist.json")

    monkeypatch.setattr(parookaville, "get_html", fake_get_html)
    lu = parookaville.parse(read("parookaville", "line-up.html"), "https://www.parookaville.com/de/line-up")
    assert calls == [
        "https://www.parookaville.com/search/artist?region=DE&language=de&page=1&limit=120&preFilter%5B%5D=festival-17"
    ]
    assert lu is not None and "Steve Aoki" in lu.acts


def test_parookaville_fremd():
    assert parookaville.parse("<html><body>x</body></html>", "u") is None
    assert parookaville.parse('{"items": []}', "u") is None
