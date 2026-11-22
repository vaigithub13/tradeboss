"""Setup rules on saved-shape 5-minute bars."""

from __future__ import annotations

from datetime import date, timedelta

from app.scanner.bars import day_of
from app.scanner.config import ScannerSettings
from app.scanner.context import context_row, nifty_change_pct
from app.scanner.setups.flag import find_flags
from app.scanner.setups.gap import find_gaps
from app.scanner.setups.orb15 import find_orb
from app.scanner.setups.rsi_extreme import find_rsi_extremes
from app.scanner.setups.vwap_cross import find_vwap_crosses
from app.scanner.setups import run_setups
from app.scanner.universe import load_universes, resolve_names, universe_names
from app.upstox.instruments import current_index
from app.config import settings
from tests.scanner_bars import bar, flat_session

DAY = date(2026, 1, 5)
PRIOR = date(2026, 1, 2)


def _with_prior(today: list[dict]) -> list[dict]:
    return flat_session(PRIOR, 100, 1000) + today


def test_orb_long_on_the_first_close_above_a_valid_range() -> None:
    today = [
        bar(DAY, 9, 15, 100.2, 101.0, 100.0, 100.4, 1000),
        bar(DAY, 9, 20, 100.4, 100.8, 100.1, 100.5, 1000),
        bar(DAY, 9, 30, 100.6, 101.6, 100.5, 101.4, 2000),
    ]
    hits = [h for h in find_orb(_with_prior(today)) if not h.forming]
    assert len(hits) == 1
    assert hits[0].side == "long"
    assert hits[0].close == 101.4
    assert hits[0].range_high == 101.0
    assert hits[0].range_low == 100.0


def test_orb_rejects_a_narrow_range_and_a_quiet_break() -> None:
    narrow = [
        bar(DAY, 9, 15, 100.0, 100.1, 100.0, 100.05, 1000),
        bar(DAY, 9, 20, 100.05, 100.1, 100.0, 100.05, 1000),
        bar(DAY, 9, 30, 100.05, 100.4, 100.0, 100.3, 5000),
    ]
    assert [h for h in find_orb(_with_prior(narrow)) if not h.forming] == []
    quiet = [
        bar(DAY, 9, 15, 100.2, 101.0, 100.0, 100.4, 1000),
        bar(DAY, 9, 20, 100.4, 100.8, 100.1, 100.5, 1000),
        bar(DAY, 9, 30, 100.6, 101.6, 100.5, 101.4, 1000),
    ]
    assert [h for h in find_orb(_with_prior(quiet)) if not h.forming] == []


def test_orb_short_and_no_entry_at_or_after_1430() -> None:
    today = [
        bar(DAY, 9, 15, 100.2, 101.0, 100.0, 100.4, 1000),
        bar(DAY, 9, 20, 100.4, 100.8, 100.1, 100.5, 1000),
        bar(DAY, 9, 30, 100.2, 100.4, 99.4, 99.6, 2000),
    ]
    hits = [h for h in find_orb(_with_prior(today)) if not h.forming]
    assert len(hits) == 1 and hits[0].side == "short"
    bars = [
        bar(DAY, 9, 15, 100.2, 101.0, 100.0, 100.4, 1000),
        bar(DAY, 9, 20, 100.4, 100.8, 100.1, 100.5, 1000),
    ]
    hour, minute = 9, 30
    while (hour, minute) < (14, 30):
        bars.append(bar(DAY, hour, minute, 100.4, 100.7, 100.2, 100.5, 1000))
        minute += 5
        if minute >= 60:
            hour += 1
            minute -= 60
    bars.append(bar(DAY, 14, 30, 100.6, 102.0, 100.5, 101.8, 5000))
    assert [h for h in find_orb(_with_prior(bars)) if not h.forming] == []


def test_flag_breaks_in_the_impulse_direction() -> None:
    bars = quiet_until(DAY, 30)
    # impulse, four tight bars drifting down, then a close through the flag high
    bars.append(bar(DAY, 11, 45, 100.3, 103.0, 100.2, 102.8, 1000))
    for i, minute in enumerate((50, 55, 0, 5)):
        hour = 11 if minute >= 50 else 12
        px = 102.6 - i * 0.05
        bars.append(bar(DAY, hour, minute, px, 102.7, 102.3, px - 0.02, 1000))
    bars.append(bar(DAY, 12, 10, 102.5, 103.2, 102.4, 103.0, 2000))
    hits = find_flags(bars)
    assert any(h.side == "long" and h.close == 103.0 for h in hits)


def test_rsi_long_needs_a_reversal_under_vwap() -> None:
    bars = []
    price = 150.0
    for h, m in __import__("tests.scanner_bars", fromlist=["clock_minutes"]).clock_minutes()[:40]:
        price -= 1.2
        bars.append(bar(DAY, h, m, price + 0.4, price + 0.5, price - 0.2, price, 1000))
    last = bars[-1]
    # close back above the prior close, still far under VWAP
    close = float(bars[-2]["close"]) + 0.6
    bars[-1] = bar(DAY, 12, 30, close - 0.3, close + 0.2, close - 0.4, close, 1000)
    bars[-1]["time"] = last["time"]
    hits = find_rsi_extremes(bars)
    assert hits and hits[0].side == "long"
    assert hits[0].setup == "rsi_extreme"


def test_vwap_reclaim_waits_for_the_hold_bar() -> None:
    bars = flat_session(DAY, 100, 100)[:25]
    # prior close under vwap, cross up on volume, next bar holds
    bars[20] = bar(DAY, * _hm(bars[20]["time"]), 100, 100, 99.0, 99.0, 100)
    bars[21] = bar(DAY, * _hm(bars[21]["time"]), 99.2, 101.0, 99.1, 100.8, 400)
    bars[22] = bar(DAY, * _hm(bars[22]["time"]), 100.8, 101.2, 100.6, 101.0, 100)
    hits = find_vwap_crosses(bars)
    assert any(h.side == "long" and h.time == bars[22]["time"] for h in hits)


def test_gap_go_and_gap_fill() -> None:
    go = [
        bar(DAY, 9, 15, 102.0, 102.4, 101.8, 102.2, 1000),
        bar(DAY, 9, 20, 102.2, 102.5, 102.0, 102.3, 1000),
        bar(DAY, 9, 30, 102.3, 103.0, 102.2, 102.8, 1000),
    ]
    hits = find_gaps(flat_session(PRIOR) + go, prev_close=100)
    assert any(h.setup == "gap_go" and h.side == "long" for h in hits)
    fill = [
        bar(DAY, 9, 15, 102.0, 102.4, 101.8, 102.2, 1000),
        bar(DAY, 9, 20, 102.2, 102.3, 101.9, 102.0, 1000),
        bar(DAY, 9, 30, 102.0, 102.1, 101.4, 101.6, 1000),
    ]
    filled = find_gaps(flat_session(PRIOR) + fill, prev_close=100)
    assert any(h.setup == "gap_fill" and h.side == "short" for h in filled)
    assert not any(h.setup == "gap_go" for h in filled)


def test_a_disabled_setup_is_skipped() -> None:
    today = [
        bar(DAY, 9, 15, 100.2, 101.0, 100.0, 100.4, 1000),
        bar(DAY, 9, 20, 100.4, 100.8, 100.1, 100.5, 1000),
        bar(DAY, 9, 30, 100.6, 101.6, 100.5, 101.4, 2000),
    ]
    settings = ScannerSettings(setups={"orb15": False})
    hits = run_setups(_with_prior(today), enabled=settings.setups, prev_close=100)
    assert not any(h.setup == "orb15" for h in hits)


def test_context_columns() -> None:
    prior = [flat_session(PRIOR - timedelta(days=i), 100, 1000) for i in range(1, 6)]
    today = flat_session(DAY, 102, 2000)
    today[-1] = {**today[-1], "close": 103}
    row = context_row(symbol="RELIANCE", sector="Energy", today=today, prior_sessions=prior, prev_close=100, nifty_pct=1.0)
    assert row["change_pct"] == 3
    assert row["vs_nifty"] == 2
    assert row["sector"] == "Energy"
    assert row["rvol"] is not None and row["rvol"] > 1
    nifty = flat_session(DAY, 100, 1)
    nifty[-1] = {**nifty[-1], "close": 101}
    assert nifty_change_pct(nifty, DAY, 100) == 1


def test_universe_file_and_live_snapshot() -> None:
    spec = load_universes()
    names = universe_names(spec)
    assert len(names["nifty50"]) == 50
    assert len(names["banknifty"]) == 14
    assert spec["as_of"] == "2026-11-21"
    resolved, unresolved = resolve_names(names["nifty50"], {"RELIANCE": [("NSE_EQ|X", "NSE_EQ_X")], "NOPE": []})
    # the helper is exact: a name missing from the table is unresolved, a unique key resolves
    assert any(s.symbol == "RELIANCE" for s in resolved)
    assert "ADANIENT" in unresolved
    index = current_index(settings.instruments_dir)
    if index is None:
        return
    from app.scanner.universe import resolve_universe

    nifty, missing = resolve_universe(index, "nifty50", spec=spec)
    banks, bank_missing = resolve_universe(index, "banknifty", spec=spec)
    assert missing == []
    assert bank_missing == []
    assert len(nifty) == 50
    assert len(banks) == 14


def quiet_until(day: date, n: int) -> list[dict]:
    from tests.scanner_bars import clock_minutes

    return [bar(day, h, m, 100, 100.1, 99.9, 100, 1000) for h, m in clock_minutes()[:n]]


def _hm(ts: int) -> tuple[int, int]:
    got = day_of(ts)  # noqa: F841 — keeps the import used when debugging a bar
    from datetime import datetime

    dt = datetime.fromtimestamp(ts, __import__("tests.scanner_bars", fromlist=["IST"]).IST)
    return dt.hour, dt.minute
