"""Spread recorder. Fixture depth frames only: no socket, no authorize, no HTTP.

Statistics use one snapshot per contract per second (the last update in that second).
Fill costs are points away from the mid, walked through the five levels.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from app.backtest.expiry import load_default_calendar
from app.live.frames import FeedItem, encode_feed
from app.live.model import IST
from app.live.spreads.book import fill_costs
from app.live.spreads.decode import Level, depth_quotes, encode_depth
from app.live.spreads.keys import compose_keys
from app.live.spreads.report import fast_minutes, median_p90, orb_window, report_day, second_snapshots
from app.live.spreads.rows import quote_row
from app.live.spreads.select import Contract, StrikeSelector, nearest_weekly, select_contracts
from app.live.spreads.writer import SpreadWriter, read_spreads
from app.live.service import LiveConfig, LiveService
from app.data.store import CandleStore
from app.upstox.instruments import NIFTY_INDEX_KEY, VIX_KEY

MON = date(2026, 10, 5)
EXP = date(2026, 10, 6)  # Tuesday weekly
CAL = load_default_calendar()


def T(h: int, m: int, s: int = 0, day: date = MON) -> int:
    return int(datetime(day.year, day.month, day.day, h, m, s, tzinfo=IST).timestamp() * 1000)


def sec(h: int, m: int, s: int = 0, day: date = MON) -> int:
    return T(h, m, s, day) // 1000


def chain(expiry: date, strikes: list[float]) -> list[Contract]:
    out = []
    for strike in strikes:
        for kind in ("CE", "PE"):
            out.append(Contract(f"NSE_FO|{kind}{int(strike)}", float(strike), kind, expiry))
    return out


def book(levels: list[Level]) -> dict:
    return {"NSE_FO|CE22350": levels}


# ---------------------------------------------------------------- S1 depth
def test_s1_best_quote_is_level_1_and_all_five_levels_are_kept() -> None:
    levels = [
        Level(100.0, 10, 100.5, 11),
        Level(99.5, 20, 101.0, 21),
        Level(99.0, 30, 101.5, 31),
        Level(98.5, 40, 102.0, 41),
        Level(98.0, 50, 102.5, 51),
    ]
    raw = encode_depth(T(10, 0), {"NSE_FO|CE": levels})
    quotes = depth_quotes(raw)
    assert len(quotes) == 1
    q = quotes[0]
    assert q.ts_ms == T(10, 0)
    assert q.levels == tuple(levels)
    assert (q.levels[0].bid_p, q.levels[0].bid_q, q.levels[0].ask_p, q.levels[0].ask_q) == (100.0, 10, 100.5, 11)


def test_s1_one_sided_and_empty_and_index_frames() -> None:
    one = encode_depth(T(10, 0), {"NSE_FO|CE": [Level(0.0, 0, 10.0, 5)]})
    quotes = depth_quotes(one)
    assert len(quotes) == 1 and quotes[0].levels[0].bid_p == 0 and quotes[0].levels[0].ask_p == 10
    empty = encode_depth(T(10, 0), {"NSE_FO|CE": [Level(0.0, 0, 0.0, 0)]})
    assert depth_quotes(empty) == []
    index = encode_feed([FeedItem(NIFTY_INDEX_KEY, 22340.0, T(10, 0), 0, None, None, None, False)], T(10, 0))
    assert depth_quotes(index) == []


# ---------------------------------------------------------------- S2 strikes
def test_s2_atm_plus_minus_two_calls_and_puts() -> None:
    strikes = [float(s) for s in range(22000, 23001, 50)]
    listed = chain(EXP, strikes)
    chosen, missing = select_contracts(22340.0, 50, EXP, listed)
    got = sorted((c.kind, c.strike) for c in chosen)
    want = sorted((k, float(s)) for s in (22250, 22300, 22350, 22400, 22450) for k in ("CE", "PE"))
    assert got == want
    assert missing == []


def test_s2_a_missing_strike_is_skipped_and_named() -> None:
    strikes = [float(s) for s in (22250, 22300, 22350, 22400, 22450)]
    listed = [c for c in chain(EXP, strikes) if not (c.kind == "CE" and c.strike == 22400)]
    chosen, missing = select_contracts(22340.0, 50, EXP, listed)
    assert len(chosen) == 9
    assert missing == [(22400.0, "CE")]


def test_s2_expiry_day_uses_that_days_weekly() -> None:
    expiry = nearest_weekly(CAL, date(2026, 10, 1))
    assert nearest_weekly(CAL, expiry) == expiry
    later = nearest_weekly(CAL, date(expiry.year, expiry.month, expiry.day))
    assert later == expiry


# ---------------------------------------------------------------- S3 ATM moves
def _wide() -> list[Contract]:
    return chain(EXP, [float(s) for s in range(22000, 23201, 50)])


def test_s3_new_atm_holds_two_seconds_then_the_old_set_overlaps_under_20() -> None:
    sel = StrikeSelector()
    listed = _wide()
    first = sel.on_spot(22340.0, 50, EXP, listed, T(10, 0))
    assert len(first) == 10
    assert sel.on_spot(22600.0, 50, EXP, listed, T(10, 0) + 1000) == first
    assert sel.on_spot(22600.0, 50, EXP, listed, T(10, 0) + 2999) == first
    both = sel.on_spot(22600.0, 50, EXP, listed, T(10, 0) + 3000)
    assert len(both) == 20
    new = [k for k in both if k not in first]
    assert len(new) == 10
    for key in new[:-1]:
        sel.on_depth(key, T(10, 0) + 3000)
        assert len(sel.keys()) == 20
    sel.on_depth(new[-1], T(10, 0) + 3000)
    assert set(sel.keys()) == set(new)


def test_s3_old_keys_leave_after_30_seconds_without_a_new_depth() -> None:
    sel = StrikeSelector()
    listed = _wide()
    sel.on_spot(22340.0, 50, EXP, listed, T(10, 0))
    sel.on_spot(22600.0, 50, EXP, listed, T(10, 0) + 1000)
    both = sel.on_spot(22600.0, 50, EXP, listed, T(10, 0) + 3000)
    assert len(both) == 20
    assert len(sel.on_spot(22600.0, 50, EXP, listed, T(10, 0) + 3000 + 29_999)) == 20
    left = sel.on_spot(22600.0, 50, EXP, listed, T(10, 0) + 3000 + 30_000)
    assert len(left) == 10
    assert set(left).isdisjoint(set(k for k in both if "22250" in k or "22300" in k or "22350" in k or "22400" in k or "22450" in k))


def test_s3_a_third_center_drops_the_oldest_generation() -> None:
    sel = StrikeSelector()
    listed = _wide()
    sel.on_spot(22340.0, 50, EXP, listed, T(10, 0))
    sel.on_spot(22600.0, 50, EXP, listed, T(10, 0) + 1000)
    sel.on_spot(22600.0, 50, EXP, listed, T(10, 0) + 3000)
    assert sel.on_spot(22850.0, 50, EXP, listed, T(10, 0) + 4000)
    keys = sel.on_spot(22850.0, 50, EXP, listed, T(10, 0) + 6000)
    assert len(keys) == 20
    assert all("22250" not in k and "22350" not in k and "22450" not in k for k in keys)


# ---------------------------------------------------------------- S4 one connection
def test_s4_flag_off_keeps_the_chart_cap_and_flag_on_appends_options() -> None:
    chart = [NIFTY_INDEX_KEY, VIX_KEY] + [f"NSE_EQ|{i}" for i in range(20)]
    options = [f"NSE_FO|{i}" for i in range(25)]
    off = compose_keys(chart, options, enabled=False)
    assert off == chart[:12]
    assert off[0] == NIFTY_INDEX_KEY and off[1] == VIX_KEY
    on = compose_keys(chart, options, enabled=True)
    assert on[:12] == chart[:12]
    assert on[12:] == options[:20]
    assert NIFTY_INDEX_KEY in on and VIX_KEY in on


def test_s4_a_disabled_service_does_not_create_a_spreads_directory(tmp_path: Path) -> None:
    cfg = LiveConfig(
        candles_dir=tmp_path / "candles",
        recordings_dir=tmp_path / "rec",
        state_dir=tmp_path / "state",
        instruments_dir=tmp_path / "inst",
        spread_recorder_enabled=False,
        spreads_dir=tmp_path / "spreads",
    )
    svc = LiveService(cfg, CandleStore(tmp_path / "candles"), client_factory=lambda: None, authorize=lambda: "wss://example.invalid")
    assert svc.subscription_keys() == [NIFTY_INDEX_KEY, VIX_KEY]
    assert not (tmp_path / "spreads").exists()


# ---------------------------------------------------------------- S5 parquet
def _row(ts: int, spread: float = 0.5) -> dict:
    return quote_row(
        key="NSE_FO|CE22350", ts_ms=ts, levels=[Level(100.0, 250, 100.0 + spread, 250)],
        expiry=EXP, strike=22350.0, kind="CE", nifty_ltp=22340.0, lot=100,
    )


def test_s5_writes_the_day_file_parts_and_respects_the_gate(tmp_path: Path) -> None:
    writer = SpreadWriter(tmp_path, enabled=True)
    writer.append(_row(T(8, 0)), market_open=True)
    writer.append(_row(T(10, 0)), market_open=False)
    assert list(tmp_path.glob("*.parquet")) == []
    writer.append(_row(T(10, 0)), market_open=True)
    writer.close()
    assert (tmp_path / f"{MON.isoformat()}.parquet").is_file()

    again = SpreadWriter(tmp_path, enabled=True)
    again.append(_row(T(10, 1), spread=0.8), market_open=True)
    again.close()
    assert (tmp_path / f"{MON.isoformat()}.part-1.parquet").is_file()
    frame = read_spreads(tmp_path, MON)
    assert len(frame) == 2
    assert list(frame["ts_ms"]) == [T(10, 0), T(10, 1)]
    assert frame.iloc[0]["spread"] == pytest.approx(0.5)

    off = SpreadWriter(tmp_path / "off", enabled=False)
    off.append(_row(T(10, 0)), market_open=True)
    off.close()
    assert not (tmp_path / "off").exists()


# ---------------------------------------------------------------- S6 percentiles
def test_s6_median_and_p90_and_a_crossed_book() -> None:
    assert median_p90([float(n) for n in range(1, 11)]) == (5.5, 9.0)
    assert median_p90([]) == (None, None)
    rows = [_snap(T(10, 0) + i * 1000, spread=float(n)) for i, n in enumerate(range(1, 11))]
    rows.append(_snap(T(10, 0) + 20_000, spread=-1.0))
    cell = report_day(rows, [], day=MON, step=50, calendar=CAL)["time"]["10:00"]["spread"]
    assert cell["median"] == 5.5
    assert cell["p90"] == 9.0
    assert cell["n"] == 10
    assert cell["thin"] is True


# ---------------------------------------------------------------- one second, one vote
def test_a_busy_second_and_a_quiet_second_each_count_once() -> None:
    t0 = T(10, 0)
    rows = [_snap(t0 + i * 10, spread=1.0, buy_1=1.0) for i in range(49)]
    rows.append(_snap(t0 + 500, spread=10.0, buy_1=10.0))
    rows.append(_snap(t0 + 1000, spread=0.0, buy_1=0.0))
    snaps = second_snapshots(rows)
    assert [(s["spread"], s["buy_1"]) for s in snaps] == [(10.0, 10.0), (0.0, 0.0)]
    cell = report_day(rows, [], day=MON, step=50, calendar=CAL)["time"]["10:00"]
    assert cell["spread"]["n"] == 2
    assert cell["spread"]["median"] == 5.0
    assert cell["buy_1"]["n"] == 2
    assert cell["buy_1"]["median"] == 5.0


# ---------------------------------------------------------------- fill cost
def test_fill_enough_at_level_1() -> None:
    levels = [Level(100.0, 250, 102.0, 250)] + [Level(0.0, 0, 0.0, 0)] * 4
    cost = fill_costs(levels, 100)
    assert cost.level1_covers_1_lot is True
    assert cost.buy_1 == pytest.approx(1.0)
    assert cost.sell_1 == pytest.approx(1.0)
    assert cost.buy_2 == pytest.approx(1.0)
    assert cost.sell_2 == pytest.approx(1.0)


def test_fill_walks_three_levels() -> None:
    levels = [
        Level(8.0, 30, 10.0, 30),
        Level(6.0, 40, 12.0, 40),
        Level(4.0, 50, 14.0, 50),
        Level(2.0, 100, 20.0, 100),
        Level(1.0, 100, 30.0, 100),
    ]
    cost = fill_costs(levels, 100)
    assert cost.level1_covers_1_lot is False
    assert cost.buy_1 == pytest.approx(3.0)
    assert cost.sell_1 == pytest.approx(3.0)
    assert cost.buy_2 == pytest.approx(6.4)
    assert cost.sell_2 == pytest.approx(4.8)


def test_fill_not_enough_in_five_levels() -> None:
    levels = [Level(100.0, 10, 102.0, 10) for _ in range(5)]
    cost = fill_costs(levels, 100)
    assert cost.level1_covers_1_lot is False
    assert cost.buy_1 is None and cost.sell_1 is None
    assert cost.buy_2 is None and cost.sell_2 is None


def test_quote_row_uses_the_nifty_lot_table() -> None:
    levels = [Level(100.0, 65, 102.0, 65)]
    row = quote_row(
        key="NSE_FO|CE", ts_ms=T(10, 0), levels=levels, expiry=EXP, strike=24000.0,
        kind="CE", nifty_ltp=24000.0, cycle="weekly",
    )
    assert row is not None
    assert row["lot_size"] == 65
    assert row["level1_covers_1_lot"] is True
    assert row["buy_1"] == pytest.approx(1.0)
    short = quote_row(
        key="NSE_FO|CE", ts_ms=T(10, 0), levels=[Level(100.0, 10, 102.0, 10) for _ in range(5)],
        expiry=EXP, strike=24000.0, kind="CE", nifty_ltp=24000.0, cycle="weekly",
    )
    assert short is not None
    assert short["lot_size"] == 65
    assert short["buy_1"] is None and short["sell_1"] is None


# ---------------------------------------------------------------- S7 fast minutes
def test_s7_fast_minutes_are_the_top_tenth_including_ties() -> None:
    bars = [{"time": i, "high": float(i), "low": 0.0} for i in range(1, 11)]
    assert fast_minutes(bars) == {10}
    tied = [{"time": 1, "high": 5.0, "low": 0.0}, {"time": 2, "high": 5.0, "low": 0.0}]
    tied += [{"time": i, "high": 1.0, "low": 0.0} for i in range(3, 11)]
    assert fast_minutes(tied) == {1, 2}
    assert fast_minutes([]) == set()


# ---------------------------------------------------------------- S8 ORB window
def _range_bars() -> list[dict]:
    return [{"time": sec(9, m), "high": 100.0, "low": 90.0} for m in range(15, 30)]


def test_s8_five_minutes_after_the_first_break() -> None:
    bars = _range_bars()
    bars.append({"time": sec(9, 30), "high": 100.0, "low": 90.0})
    bars.append({"time": sec(9, 31), "high": 101.0, "low": 95.0})
    window = orb_window(bars)
    assert window == (T(9, 31), T(9, 36))
    start, end = window
    assert start <= T(9, 35, 59) < end
    assert not (start <= T(9, 36) < end)

    quiet = _range_bars() + [{"time": sec(9, m), "high": 100.0, "low": 90.0} for m in range(30, 40)]
    assert orb_window(quiet) is None
    assert orb_window([]) is None

    both = _range_bars() + [{"time": sec(9, 32), "high": 101.0, "low": 89.0}]
    assert orb_window(both) == (T(9, 32), T(9, 37))


# ---------------------------------------------------------------- S9 buckets, and fill costs in every slice
def _snap(ts: int, *, spread: float, buy_1: float | None = 0.4, strike: float = 22350.0, kind: str = "CE",
          nifty: float = 22340.0, expiry: date = EXP) -> dict:
    return {
        "ts_ms": ts, "instrument_key": f"NSE_FO|{kind}{int(strike)}", "expiry": expiry.isoformat(),
        "strike": strike, "kind": kind, "nifty_ltp": nifty, "spread": spread,
        "buy_1": buy_1, "sell_1": buy_1, "buy_2": buy_1, "sell_2": buy_1,
        "level1_covers_1_lot": buy_1 is not None,
    }


def test_s9_every_two_sided_snapshot_is_in_one_bin_and_fill_costs_sit_beside_the_spread() -> None:
    rows = [
        _snap(T(9, 20), spread=1.0, strike=22350.0, kind="CE"),
        _snap(T(10, 0), spread=2.0, strike=22400.0, kind="CE"),
        _snap(T(9, 31), spread=3.0, strike=22250.0, kind="CE", buy_1=None),
    ]
    bars = _range_bars()
    bars.append({"time": sec(9, 31), "high": 110.0, "low": 80.0})
    bars += [{"time": sec(h, m), "high": 100.0, "low": 99.0} for h in range(10, 15) for m in (0, 15, 30, 45)]
    rep = report_day(rows, bars, day=MON, step=50, calendar=CAL)
    assert sum(c["spread"]["n"] for c in rep["time"].values()) == 3
    assert sum(c["spread"]["n"] for c in rep["dte"].values()) == 3
    assert sum(c["spread"]["n"] for c in rep["moneyness"].values()) == 3
    assert rep["time"]["09:15"]["spread"]["n"] == 1
    assert rep["time"]["09:30"]["spread"]["n"] == 1
    assert rep["time"]["10:00"]["spread"]["n"] == 1
    for slice_ in (rep["time"]["09:15"], rep["dte"][next(iter(rep["dte"]))], rep["moneyness"]["ATM"], rep["fast"], rep["orb"]):
        assert "buy_1" in slice_ and "sell_1" in slice_ and "buy_2" in slice_ and "sell_2" in slice_
        assert "level1_covers_1_lot" in slice_
    assert rep["time"]["09:30"]["buy_1"]["uncovered"] == 1
    assert rep["orb"]["spread"]["n"] == 1
    assert rep["fast"]["spread"]["n"] >= 1
    assert rep["moneyness"]["ATM"]["spread"]["n"] == 1
    assert rep["moneyness"]["OTM1"]["spread"]["n"] == 1
    assert rep["moneyness"]["ITM2"]["spread"]["n"] == 1
