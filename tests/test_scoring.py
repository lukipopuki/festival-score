from festival_score import scoring


def test_points_grenzen():
    assert scoring.points(None) is None
    assert scoring.points(0) == 0
    assert scoring.points(5_000) == 0
    assert scoring.points(10_000) == 0
    assert scoring.points(1_000_000) == 2
    assert round(scoring.points(100_000), 6) == 1
    assert round(scoring.points(10_000_000), 6) == 3


def test_estimate_ratio():
    pairs = [(200, 100)] * 5
    assert scoring.estimate_ratio(pairs) == 2
    assert scoring.estimate_ratio(pairs[:4]) is None
    # Paare mit 0 zählen nicht
    assert scoring.estimate_ratio(pairs[:4] + [(0, 10)]) is None
    assert scoring.estimate_ratio(pairs + [(1000, 10)]) == 2  # Median


def test_edition_scores():
    pts = [3.0, 2.0, 1.0, 1.0, 1.0, 0.5, 0.3, None]
    s = scoring.edition_scores(pts, days=2, price_eur=100)
    assert s["total"] == 8.8
    assert s["headliner"] == 1.6
    assert s["depth"] == 0.4
    assert s["per_day"] == 4.4
    assert s["per_euro"] == 0.088


def test_edition_scores_leer_und_ohne_preis():
    s = scoring.edition_scores([], days=3, price_eur=None)
    assert s["total"] == 0
    assert s["headliner"] is None and s["depth"] is None
    assert s["per_euro"] is None
    s = scoring.edition_scores([None, None], days=1, price_eur=50)
    assert s["total"] == 0 and s["headliner"] is None


def test_edition_scores_wenige_acts_keine_depth():
    s = scoring.edition_scores([2.0, 1.0], days=1, price_eur=10)
    assert s["headliner"] == 1.5
    assert s["depth"] is None


def test_completeness():
    assert scoring.completeness(44, 86) == 0.51
    assert scoring.completeness(100, 50) == 1.0
    assert scoring.completeness(10, None) is None
    assert scoring.completeness(10, 0) is None


def test_top30_begrenzt_auf_30_beste():
    from festival_score.scoring import edition_scores

    pts = [2.0] * 40 + [None, 0.5]
    s = edition_scores(pts, days=3, price_eur=100)
    assert s["top30"] == 60.0
    assert s["total"] == 80.5
    assert edition_scores([], 3, None)["top30"] == 0


def test_strength_ratio():
    from festival_score.scoring import strength_ratio

    assert strength_ratio(82.1, 81.2) == 1.01
    assert strength_ratio(50, None) is None
    assert strength_ratio(50, 0) is None
