from datetime import datetime

from app.data.validation import compare_bars, daily_volume_sums, render_report, render_volume_sums
from app.upstox.instruments import IST


def t(h: int, m: int, d: int = 2) -> int:
    return int(datetime(2024, 3, d, h, m, tzinfo=IST).timestamp())


def bar(time: int, o: float = 100, h: float = 101, lo: float = 99, c: float = 100.5, v: float = 0) -> dict:
    return {"time": time, "open": o, "high": h, "low": lo, "close": c, "volume": v}


def test_identical_series_have_no_differences() -> None:
    xs = [bar(t(9, 15)), bar(t(9, 20))]
    r = compare_bars(xs, [dict(x) for x in xs], label="same")
    assert r.ok and r.compared == 2 and r.open_bars_compared == 1 and r.mismatched_bars == 0


def test_differences_are_found_per_field_and_09_15_is_reported_separately() -> None:
    ours = [bar(t(9, 15)), bar(t(9, 20)), bar(t(9, 25)), bar(t(9, 30), v=10)]
    theirs = [bar(t(9, 15), o=100.5), bar(t(9, 20), h=102), bar(t(9, 25)), bar(t(9, 30), v=12)]
    r = compare_bars(ours, theirs, label="x")
    assert not r.ok
    assert r.by_field() == {"open": 1, "high": 1, "volume": 1}
    assert r.mismatched_bars == 3
    assert [m.time for m in r.open_bar_mismatches] == [t(9, 15)]  # only the 09:15 bar
    assert r.open_bar_mismatches[0].diff == -0.5


def test_tiny_price_noise_below_half_a_paisa_is_not_a_mismatch_but_volume_is_exact() -> None:
    assert compare_bars([bar(t(9, 15))], [bar(t(9, 15), c=100.5004)], label="x").ok
    assert not compare_bars([bar(t(9, 15), v=1)], [bar(t(9, 15), v=2)], label="x").ok
    assert compare_bars([bar(t(9, 15), v=1)], [bar(t(9, 15), v=2)], label="x", compare_volume=False).ok


def test_bars_missing_on_either_side_are_listed() -> None:
    r = compare_bars([bar(t(9, 15)), bar(t(9, 20))], [bar(t(9, 20)), bar(t(9, 25))], label="x")
    assert r.only_ours == [t(9, 15)] and r.only_theirs == [t(9, 25)] and r.compared == 1 and not r.ok


def test_report_renders_counts_and_examples() -> None:
    r = compare_bars([bar(t(9, 15))], [bar(t(9, 15), o=101)], label="demo")
    text = render_report([r])
    assert "demo" in text and "open" in text and "09:15" in text and "-1.0000" in text


def test_daily_volume_sums_match_when_the_series_agree() -> None:
    xs = [bar(t(9, 15), v=100), bar(t(9, 20), v=50), bar(t(9, 15, d=4), v=7)]
    rows = daily_volume_sums(xs, [dict(x) for x in xs])
    assert [(r.day.isoformat(), r.ours, r.theirs, r.ok) for r in rows] == [
        ("2024-03-02", 150, 150, True),
        ("2024-03-04", 7, 7, True),
    ]
    assert "days whose volume sum or bar count differs: **0**" in render_volume_sums("x", rows)


def test_daily_volume_sums_flag_a_missing_bar_and_a_wrong_volume() -> None:
    ours = [bar(t(9, 15), v=100), bar(t(9, 20), v=50), bar(t(9, 15, d=4), v=7)]
    theirs = [bar(t(9, 15), v=100), bar(t(9, 15, d=4), v=8)]  # 09:20 missing on day 1, volume off on day 2
    rows = daily_volume_sums(ours, theirs)
    assert [r.ok for r in rows] == [False, False]
    assert (rows[0].bars_ours, rows[0].bars_theirs, rows[0].ours, rows[0].theirs) == (2, 1, 150, 100)
    text = render_volume_sums("x", rows)
    assert "differs: **2**" in text and "2024-03-02" in text and "diff +49" in text
