from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from festival_score.cache import MetricsCache
from festival_score.sources.lastfm import LastfmError, LastfmSource, names_match

FIX = Path(__file__).parent / "fixtures" / "lastfm"


def load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def make(tmp_path, responses: list[tuple[int, dict]]):
    calls: list[httpx.Request] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        status, body = queue.pop(0) if len(queue) > 1 else queue[0]
        return httpx.Response(status, json=body)

    src = LastfmSource(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        cache=MetricsCache("lastfm", cache_dir=tmp_path),
        api_key="testkey",
        retry_wait=0,
        min_interval=0,
    )
    return src, calls


def test_success_converts_strings(tmp_path):
    src, calls = make(tmp_path, [(200, load("artist_getinfo_muse.json"))])
    m = src.fetch("Muse")
    assert m is not None
    assert m.values == {"listeners": 4120345, "playcount": 612345678}
    assert m.source_id == "9c9f1380-2516-4fc9-a3e6-f9f61941d090"
    assert m.matched_name == "Muse"
    assert m.source == "lastfm"
    q = calls[0].url.params
    assert q["method"] == "artist.getinfo" and q["autocorrect"] == "1" and q["api_key"] == "testkey"


def test_empty_mbid_is_none(tmp_path):
    src, _ = make(tmp_path, [(200, load("artist_getinfo_no_mbid.json"))])
    m = src.fetch("Kontra K")
    assert m is not None and m.source_id is None


def test_not_found_returns_none(tmp_path):
    src, _ = make(tmp_path, [(404, load("error_6.json"))])
    assert src.fetch("Gibtsnicht") is None


def test_rate_limit_retries(tmp_path):
    src, calls = make(
        tmp_path,
        [(429, load("error_29.json")), (200, load("error_29.json")), (200, load("artist_getinfo_muse.json"))],
    )
    assert src.fetch("Muse") is not None
    assert len(calls) == 3


def test_rate_limit_gives_up(tmp_path):
    src, calls = make(tmp_path, [(200, load("error_29.json"))])
    with pytest.raises(LastfmError) as e:
        src.fetch("Muse")
    assert e.value.code == 29 and len(calls) == 4


def test_other_error_raises(tmp_path):
    src, _ = make(tmp_path, [(403, load("error_10.json"))])
    with pytest.raises(LastfmError, match="10"):
        src.fetch("Muse")


def test_mismatch_discarded(tmp_path):
    src, _ = make(tmp_path, [(200, load("artist_getinfo_muse.json"))])
    assert src.fetch("Rammstein") is None


@pytest.mark.parametrize(
    "q,m,ok",
    [
        ("Muse", "Muse", True),
        ("muse", "MUSE", True),
        ("The Killers", "Killers", True),
        ("Killers", "The Killers", True),
        ("Beyoncé", "Beyonce", True),
        ("Kontra K", "Kontra K", True),
        ("Rammstein", "Muse", False),
        ("Bonez MC", "Bonez", False),
        ("Die Ärzte", "Die Toten Hosen", False),
    ],
)
def test_names_match(q, m, ok):
    assert names_match(q, m) is ok


def test_cache_hit_prevents_http(tmp_path):
    src, calls = make(tmp_path, [(200, load("artist_getinfo_muse.json"))])
    first = src.fetch("Muse")
    second = src.fetch("muse")
    assert len(calls) == 1
    assert second is not None and second.values == first.values


def test_none_is_cached(tmp_path):
    src, calls = make(tmp_path, [(404, load("error_6.json"))])
    assert src.fetch("Gibtsnicht") is None
    assert src.fetch("Gibtsnicht") is None
    assert len(calls) == 1
    src.save()
    assert MetricsCache("lastfm", cache_dir=tmp_path).get("Gibtsnicht") == (True, None)


def test_missing_key(monkeypatch, tmp_path):
    monkeypatch.delenv("LASTFM_API_KEY", raising=False)
    monkeypatch.setattr("festival_score.sources.lastfm.load_dotenv", lambda: None)
    with pytest.raises(RuntimeError, match=".env.example"):
        LastfmSource(cache=MetricsCache("lastfm", cache_dir=tmp_path))
