import json
from datetime import date

from festival_score import pipeline, scraper
from festival_score.models import Act, ArtistMetrics, Festival

TODAY = date(2026, 10, 30)


class FakeSpotify:
    def __init__(self, data=None, fail=()):
        self.data = data or {}  # name -> (id, listeners)
        self.fail = set(fail)
        self.fetched: list[str] = []
        self.listener_ids: list[str] = []
        self.saves = 0

    def fetch(self, name):
        self.fetched.append(name)
        if name in self.fail:
            raise RuntimeError("boom")
        if name not in self.data:
            return None
        sid, n = self.data[name]
        return ArtistMetrics(name, "spotify", {"monthly_listeners": n}, source_id=sid)

    def fetch_monthly_listeners(self, sid):
        self.listener_ids.append(sid)
        return 2_000_000

    def save(self):
        self.saves += 1


class FakeLastfm:
    def __init__(self, data=None, fail=()):
        self.data = data or {}
        self.fail = set(fail)
        self.fetched: list[str] = []
        self.saves = 0

    def fetch(self, name):
        self.fetched.append(name)
        if name in self.fail:
            raise RuntimeError("boom")
        if name not in self.data:
            return None
        return ArtistMetrics(name, "lastfm", {"listeners": self.data[name]})

    def save(self):
        self.saves += 1


def fest(slug="hurricane", year=2027, names=("A", "B"), source="festivalplaner", start=None, **kw):
    start = start or date(year, 6, 18)
    return Festival(
        slug=slug, name=slug.title(), year=year, start_date=start,
        end_date=date(start.year, start.month, start.day + 2), url="u", price_eur=100.0,
        location="Ort", source=source,
        acts=[Act(n, n.lower(), i, i == 0) for i, n in enumerate(names)], **kw,
    )


def test_enrich_dedupliziert_und_normalisiert():
    f1 = fest(names=("Muse", "Édith"))
    f2 = fest(slug="southside", names=("muse", "Edith", "Neu"))
    sp, lf = FakeSpotify(), FakeLastfm()
    res = pipeline.enrich([f1, f2], sp, lf, {})
    assert sp.fetched == ["Muse", "Édith", "Neu"]
    assert lf.fetched == ["Muse", "Édith", "Neu"]
    assert set(res) == {"muse", "edith", "neu"}
    assert sp.saves >= 1 and lf.saves >= 1


def test_enrich_overrides():
    f = fest(names=("Muse", "Alle Farben", "Überraschungsact"))
    ov = {
        "Muse": {"spotify_id": "abc"},
        "Alle Farben": {"search_name": "Alle Farben DJ"},
        "Überraschungsact": {"skip": True},
    }
    sp = FakeSpotify({"Alle Farben DJ": ("id2", 500_000)})
    lf = FakeLastfm({"Alle Farben DJ": 100_000, "Muse": 300_000})
    res = pipeline.enrich([f], sp, lf, ov)
    assert sp.listener_ids == ["abc"]
    assert sp.fetched == ["Alle Farben DJ"]
    assert lf.fetched == ["Muse", "Alle Farben DJ"]
    assert res["muse"].spotify_id == "abc" and res["muse"].monthly_listeners == 2_000_000
    assert res["alle farben"].monthly_listeners == 500_000
    assert res["alle farben"].lastfm_listeners == 100_000
    sk = res["uberraschungsact"]
    assert (sk.spotify_id, sk.monthly_listeners, sk.lastfm_listeners) == (None, None, None)


def test_enrich_fehler_einer_quelle():
    f = fest(names=("A", "B"))
    sp = FakeSpotify({"B": ("b", 1_000_000)}, fail={"A"})
    lf = FakeLastfm({"A": 5000, "B": 10}, fail={"B"})
    res = pipeline.enrich([f], sp, lf, {})
    assert res["a"].monthly_listeners is None and res["a"].lastfm_listeners == 5000
    assert res["b"].monthly_listeners == 1_000_000 and res["b"].lastfm_listeners is None


def test_enrich_speichert_alle_50(monkeypatch):
    f = fest(names=tuple(f"Act{i}" for i in range(120)))
    sp, lf = FakeSpotify(), FakeLastfm()
    pipeline.enrich([f], sp, lf, {})
    assert sp.saves == 3  # bei 50, 100 und am Ende


def test_collect_editions_festivalplaner_gewinnt(monkeypatch):
    cur = fest(year=2027)
    monkeypatch.setattr(scraper, "fetch_all", lambda slugs: [cur])

    def history(slug):
        return [fest(year=2027, source="openairguide"), fest(year=2025, source="setlistfm"),
                fest(year=2024, source="wikipedia")]

    res = pipeline.collect_editions(["hurricane"], history)
    eds = res["hurricane"]
    assert [e.year for e in eds] == [2024, 2025, 2027]
    assert next(e for e in eds if e.year == 2027) is cur


def test_collect_editions_history_fehler(monkeypatch):
    monkeypatch.setattr(scraper, "fetch_all", lambda slugs: [fest(slug=s) for s in slugs])

    def history(slug):
        if slug == "a":
            raise RuntimeError("x")
        return [fest(slug=slug, year=2025, source="openairguide")]

    res = pipeline.collect_editions(["a", "b"], history)
    assert len(res["a"]) == 1 and len(res["b"]) == 2
    assert pipeline.collect_editions(["a"], None)["a"][0].year == 2027


def metrics(**kw):
    return pipeline.ActMetrics(**kw)


def test_build_output_struktur_und_schaetzung():
    names = [f"Act{i}" for i in range(6)] + ["Nur Lastfm", "Nix"]
    upcoming = fest(year=2027, names=names)
    past = fest(year=2025, names=[f"X{i}" for i in range(16)], reported_act_count=20)
    m = {}
    for i in range(6):
        m[f"act{i}"] = metrics(spotify_id=f"id{i}", monthly_listeners=2_000_000, lastfm_listeners=1_000_000)
    m["nur lastfm"] = metrics(lastfm_listeners=500_000)
    m["nix"] = metrics()
    out = pipeline.build_output({"hurricane": [past, upcoming]}, m, today=TODAY)
    json.dumps(out)  # serialisierbar
    assert set(out) == {"generated_at", "scoring", "festivals"}
    assert out["scoring"] == {"points_zero_listeners": 10000, "top_n": 5, "top_depth": 30}
    f = out["festivals"][0]
    assert (f["slug"], f["name"], f["location"]) == ("hurricane", "Hurricane", "Ort")
    assert [e["year"] for e in f["editions"]] == [2027, 2025]
    e = f["editions"][0]
    assert e["status"] == "upcoming" and f["editions"][1]["status"] == "past"
    assert e["days"] == 3 and e["act_count"] == 8
    assert e["completeness"] == 0.4  # 8 / reported 20
    assert set(e) == {"year", "status", "source", "start_date", "end_date", "days", "price_eur",
                      "capacity", "visitors", "act_count", "reported_act_count", "completeness",
                      "scores", "acts"}
    assert set(e["scores"]) == {"total", "top30", "headliner", "depth", "per_day", "per_euro"}
    acts = {a["name"]: a for a in e["acts"]}
    assert set(acts["Act0"]) == {"name", "position", "headliner", "spotify_id", "monthly_listeners",
                                 "lastfm_listeners", "listeners_estimated", "points"}
    est = acts["Nur Lastfm"]
    assert est["listeners_estimated"] is True and est["monthly_listeners"] == 1_000_000
    assert est["points"] == 2.0
    assert acts["Nix"]["points"] is None and acts["Act0"]["listeners_estimated"] is False
    assert e["scores"]["total"] == 15.81


def test_build_output_ohne_schaetzung_bei_zu_wenig_paaren():
    upcoming = fest(names=["A", "B"])
    m = {"a": metrics(monthly_listeners=1_000_000, lastfm_listeners=500_000),
         "b": metrics(lastfm_listeners=100_000)}
    out = pipeline.build_output({"hurricane": [upcoming]}, m, today=TODAY)
    acts = out["festivals"][0]["editions"][0]["acts"]
    assert acts[1]["monthly_listeners"] is None and acts[1]["points"] is None
    assert out["festivals"][0]["editions"][0]["completeness"] is None


def test_completeness_nutzt_act_count_der_letzten_vergangenen():
    past = fest(year=2025, names=[f"X{i}" for i in range(10)])
    cur = fest(year=2027, names=["A", "B", "C", "D", "E"])
    out = pipeline.build_output({"hurricane": [past, cur]}, {}, today=TODAY)
    eds = out["festivals"][0]["editions"]
    assert eds[0]["completeness"] == 0.5
    assert eds[1]["completeness"] is None  # vergangene Ausgaben haben keine Vollständigkeit


def test_enrich_nach_sperre_nur_noch_cache():
    from festival_score.sources.spotify import SpotifyRateLimited

    class BlockedSpotify(FakeSpotify):
        class cache:
            @staticmethod
            def get(name):
                return (True, ArtistMetrics(name, "spotify", {"monthly_listeners": 5}, source_id="c")) if name == "B" else (False, None)

        def fetch(self, name):
            self.fetched.append(name)
            raise SpotifyRateLimited(80000)

    sp = BlockedSpotify()
    res = pipeline.enrich([fest(names=["A", "B", "C"])], sp, FakeLastfm(), {})
    assert sp.fetched == ["A"]  # nach der Sperre keine API-Anfragen mehr
    assert res["b"].monthly_listeners == 5
    assert res["c"].monthly_listeners is None


def test_collect_editions_kommende_ausgabe_als_ersatz(monkeypatch):
    alt = fest("deichbrand", year=2025, start=date(2025, 7, 17))
    neu_fp = fest("hurricane", year=2099, start=date(2099, 6, 18))
    monkeypatch.setattr(scraper, "fetch_all", lambda slugs: [alt, neu_fp])
    gefragt = []

    def upcoming(slug):
        gefragt.append(slug)
        return fest(slug, year=2099, start=date(2099, 7, 15), source="openairguide")

    eds = pipeline.collect_editions(["deichbrand", "hurricane"], upcoming_provider=upcoming)
    assert gefragt == ["deichbrand"]  # hurricane hat schon eine kommende Ausgabe
    assert [f.year for f in eds["deichbrand"]] == [2025, 2099]
