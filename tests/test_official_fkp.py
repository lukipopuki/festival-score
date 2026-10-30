"""Tests für den FKP-Parser (ohne Netzwerk, echte HTML-Fixtures)."""

from __future__ import annotations

from pathlib import Path

import pytest

from festival_score.official.fkp import parse, smart_case

FIX = Path(__file__).parent / "fixtures" / "official" / "fkp"


def load(name: str):
    return parse((FIX / f"{name}.html").read_text(encoding="utf-8"), f"https://{name}.de/line-up/")


def test_hurricane():
    lu = load("hurricane")
    assert lu is not None
    assert len(lu.acts) == 48  # 46 "Konzert" + 2 DJ-Acts mit Bühnen-Kategorie
    assert lu.year == 2027
    assert lu.headliners == []
    assert lu.acts[:3] == ["Phoebe Bridgers", "Placebo", "Dropkick Murphys"]
    for name in ("MGK", "Bleech 9:3", "RAUM27", "Feine Sahne Fischfilet", "Felix Jaehn", "Alt-J"):
        assert name in lu.acts
    assert not any(a.endswith("Konzert") or a.isupper() and len(a) > 5 and " " in a for a in lu.acts)
    assert len(set(a.casefold() for a in lu.acts)) == len(lu.acts)


def test_southside():
    lu = load("southside")
    assert lu is not None
    assert len(lu.acts) == 49
    assert lu.year == 2027
    assert lu.acts[0] == "Muse"
    assert "Chapo102" in lu.acts and "Dexter and the Moonrocks" in lu.acts


def test_deichbrand():
    lu = load("deichbrand")
    assert lu is not None
    assert len(lu.acts) == 31
    assert lu.year == 2027
    assert lu.acts[:3] == ["Annenmaykantereit", "Cro", "Finch"]
    assert "Steve Aoki" in lu.acts  # Link mit Query-Href
    assert "Breakdown of Sanity" in lu.acts  # Originalschreibweise aus alt
    assert "Anaïs" in lu.acts and "PA69" in lu.acts
    assert not any("­" in a for a in lu.acts)
    # keine Nav-/Footer-Einträge
    assert not {"Infos", "Tickets", "Impressum", "Merch"} & set(lu.acts)


def test_duplikate_und_leer():
    html = (
        '<a class="m0132_lineupv2__show" href="/line-up/act/x/"><h4 class="m0132_lineupv2__artist">X</h4></a>'
        '<a class="m0132_lineupv2__show" href="/line-up/act/x/"><h4 class="m0132_lineupv2__artist">X</h4></a>'
    )
    assert parse(html, "u").acts == ["X"]
    assert parse("<html><body><a href='/line-up/'>Line-Up</a></body></html>", "u") is None


def test_fallback_konzert_links():
    html = '<h1>LINE-UP 2028</h1><a href="/line-up/act/a/">FOO BAR Konzert</a><a href="/infos/">Infos</a>'
    lu = parse(html, "u")
    assert lu.acts == ["Foo Bar"] and lu.year == 2028


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("MGK", "MGK"),
        ("AC/DC", "AC/DC"),
        ("K.I.Z", "K.I.Z"),
        ("BLEECH 9:3", "Bleech 9:3"), ("RAUM27", "RAUM27"),
        ("ALT-J", "Alt-J"),
        ("THE PARADOX", "The Paradox"),
        ("DEXTER AND THE MOONROCKS", "Dexter and the Moonrocks"),
        ("ANAÏS", "Anaïs"),
        ("Already Mixed", "Already Mixed"),
    ],
)
def test_smart_case(raw, expected):
    assert smart_case(raw) == expected
