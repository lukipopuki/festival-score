from datetime import date

from festival_score.cache import MetricsCache, normalize_name
from festival_score.models import ArtistMetrics, Festival


def test_festival_days():
    f = Festival("hurricane", "Hurricane", 2027, date(2027, 6, 18), date(2027, 6, 20), "https://x")
    assert f.days == 3
    assert f.to_dict()["days"] == 3


def test_normalize_name():
    assert normalize_name("  Beyoncé   Knowles ") == "beyonce knowles"


def test_cache_roundtrip(tmp_path):
    cache = MetricsCache("test", cache_dir=tmp_path)
    cache.put("Muse", ArtistMetrics("Muse", "test", {"popularity": 80}))
    cache.put("Unbekannt", None)
    cache.save()

    reloaded = MetricsCache("test", cache_dir=tmp_path)
    hit, m = reloaded.get("muse")
    assert hit and m.values["popularity"] == 80
    assert reloaded.get("Unbekannt") == (True, None)
    assert reloaded.get("Fehlt") == (False, None)
