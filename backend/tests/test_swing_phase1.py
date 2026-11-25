"""Swing Phase 1: universe tags, daily windows, adjustment, point-in-time filters."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from app.data.history import split_windows
from app.data.importer import write_parquet
from app.swing.daily import (
    DECADE_DAYS,
    adjustment_verdict,
    bars_to_frame,
    session_stamp,
    sync_daily,
    trim_open_tail,
    write_daily_adjusted,
)
from app.swing.filters import TURNOVER_MIN, apply_filters
from app.swing.quality import coverage, year_end_passes
from app.swing.universe import build_universe, tag_segment
from app.upstox.instruments import Instrument, InstrumentIndex
from scripts.swing_phase1 import refuse_if_live

IST = timezone(timedelta(hours=5, minutes=30))


def _csv(rows: list[tuple[str, str]]) -> str:
    lines = ["Company Name,Industry,Symbol,Series,ISIN Code"]
    for symbol, isin in rows:
        lines.append(f"{symbol} Ltd,Sector,{symbol},EQ,{isin}")
    return "\n".join(lines) + "\n"


def _index() -> InstrumentIndex:
    equities = [
        ("AAA", "INE000000001"),
        ("BBB", "INE000000002"),
        ("CCC", "INE000000003"),
        ("DDD", "INE000000004"),
    ]
    instruments = [
        Instrument(f"NSE_EQ|{isin}", symbol, symbol, "equity", "NSE_EQ", "EQ") for symbol, isin in equities
    ]
    instruments.append(Instrument("NSE_INDEX|Nifty 50", "Nifty 50", "Nifty 50", "index", "NSE_INDEX", "INDEX"))
    return InstrumentIndex(instruments, date(2026, 11, 25))


def test_segment_tags_follow_todays_index_and_count_names_upstox_lacks():
    texts = {
        "nifty500": _csv([("AAA", "INE000000001"), ("BBB", "INE000000002"), ("CCC", "INE000000003"), ("DDD", "INE000000004"), ("EEE", "INE000000005")]),
        "nifty50": _csv([("AAA", "INE000000001")]),
        "next50": _csv([("DDD", "INE000000004")]),
        "midcap150": _csv([("BBB", "INE000000002")]),
        "smallcap250": _csv([("CCC", "INE000000003")]),
    }
    universe = build_universe(texts, _index(), as_of=date(2026, 11, 25))
    segments = {row["symbol"]: row["segment"] for row in universe["stocks"]}
    assert segments == {"AAA": "large", "BBB": "mid", "CCC": "small", "DDD": "large"}
    assert tag_segment("DDD", {"nifty500": {"DDD"}, "next50": {"DDD"}}) == "large"
    assert [row["symbol"] for row in universe["not_found"]] == ["EEE"]
    be = Instrument("NSE_EQ|INE000000005", "EEE", "Eee", "equity", "NSE_EQ", "BE")
    again = build_universe(texts, _index(), as_of=date(2026, 11, 25), extra_equities=[be])
    assert again["not_found"] == []
    assert again["counts"]["resolved"] == 5
    assert universe["counts"]["departed_since_2015_added"] == 0
    assert universe["counts"]["not_found"] == 1
    assert universe["counts"]["indices_missing"] > 0
    assert any(row["instrument_key"] == "NSE_INDEX|Nifty 50" for row in universe["indices"])


def test_daily_windows_stay_inside_one_decade_and_a_second_run_does_not_refetch(tmp_path):
    windows = split_windows((date(2015, 1, 1), date(2026, 11, 25)), DECADE_DAYS)
    assert windows[0][0] == date(2015, 1, 1)
    assert windows[-1][1] == date(2026, 11, 25)
    assert all((end - start).days < 3650 for start, end in windows)

    class Client:
        def __init__(self) -> None:
            self.calls = 0

        def historical_candles(self, key, start, end, *, unit="minutes", interval=1):
            assert unit == "days" and interval == 1
            self.calls += 1
            return [[f"{start.isoformat()}T00:00:00+05:30", 10, 12, 9, 11, 1000, 0]]

    inst = Instrument("NSE_EQ|INE000000001", "AAA", "Aaa", "equity", "NSE_EQ", "EQ")
    client = Client()
    when = datetime(2024, 2, 1, 18, 0, tzinfo=IST)
    first = sync_daily(client, tmp_path, inst, from_date=date(2024, 1, 2), to_date=date(2024, 1, 5), now=when)
    assert first["windows"] == 1
    assert first["bars"] == 1
    frame = bars_to_frame([["2024-01-02T00:00:00+05:30", 10, 12, 9, 11, 1000, 0]])
    stamp = datetime.fromtimestamp(int(frame.iloc[0]["time"]), IST)
    assert stamp.hour == 9 and stamp.minute == 15
    assert frame.iloc[0]["session_type"] == "normal"
    sync_daily(client, tmp_path, inst, from_date=date(2024, 1, 2), to_date=date(2024, 1, 5), now=when)
    assert client.calls == 1


def test_adjustment_matches_the_phase_a_split_check(tmp_path):
    assert adjustment_verdict(426.4, 420.2, 0.2) == "already_adjusted"
    assert adjustment_verdict(2100.0, 420.0, 0.2) == "unadjusted"
    inst = Instrument("NSE_EQ|INE000000001", "AAA", "Aaa", "equity", "NSE_EQ", "EQ")
    folder = tmp_path / "NSE_EQ_INE000000001"
    frame = pd.DataFrame([{
        "time": session_stamp(date(2024, 1, 2)),
        "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
        "volume": 100.0, "oi": float("nan"), "session_type": "normal",
    }])
    write_parquet(frame, folder / "daily.parquet")
    ledger = write_daily_adjusted(folder, as_of=date(2026, 11, 25), verdict="already_adjusted", reason="checked")
    assert ledger["source"] == "identity_copy"
    assert (folder / "daily_adj.parquet").is_file()


def test_filters_use_only_bars_up_to_that_day():
    start = date(2018, 1, 1)
    rows = []
    day = start
    while len(rows) < 21:
        if day.weekday() < 5:
            huge = len(rows) == 20
            rows.append({
                "time": session_stamp(day),
                "open": 60.0, "high": 61.0, "low": 59.0, "close": 60.0,
                "volume": 10_000_000_000.0 if huge else 1_000.0,
                "oi": float("nan"),
                "session_type": "normal",
            })
        day += timedelta(days=1)
    flags = apply_filters(pd.DataFrame(rows))
    assert flags.iloc[19]["turnover_20d"] < TURNOVER_MIN
    assert flags.iloc[20]["turnover_20d"] > flags.iloc[19]["turnover_20d"]
    assert flags.iloc[19]["listed_ok"] is False or flags.iloc[19]["listed_ok"] == False

    long_rows = []
    day = date(2018, 1, 1)
    end = date(2019, 6, 1)
    while day <= end:
        if day.weekday() < 5:
            close = 40.0 if day < date(2019, 5, 1) else 80.0
            long_rows.append({
                "time": session_stamp(day),
                "open": close, "high": close, "low": close, "close": close,
                "volume": 2_000_000.0, "oi": float("nan"), "session_type": "normal",
            })
        day += timedelta(days=1)
    long_flags = apply_filters(pd.DataFrame(long_rows))
    early = long_flags.loc[long_flags["date"] == date(2018, 6, 1)].iloc[0]
    late = long_flags.loc[long_flags["date"] == date(2019, 5, 31)].iloc[0]
    assert bool(early["listed_ok"]) is False
    assert bool(early["price_ok"]) is False
    assert bool(late["listed_ok"]) is True
    assert bool(late["price_ok"]) is True
    assert bool(late["passes"]) is True


def test_year_end_counts_and_the_worker_stays_out_of_the_app():
    rows = [
        {"segment": "large", "date": date(2020, 12, 30), "passes": True},
        {"segment": "large", "date": date(2020, 12, 31), "passes": False},
        {"segment": "mid", "date": date(2020, 12, 31), "passes": True},
        {"segment": "small", "date": date(2020, 12, 31), "passes": True},
    ]
    counted = year_end_passes(rows)
    assert counted == [{"year": 2020, "as_of": "2020-12-31", "large": 0, "mid": 1, "small": 1, "other": 0}]
    text = Path(__file__).resolve().parents[1].joinpath("app", "main.py").read_text()
    assert "swing" not in text
    assert refuse_if_live() is None


def test_a_fresh_tail_with_no_bar_is_fetched_again():
    covered = [(date(2024, 1, 1), date(2026, 11, 25))]
    trimmed = trim_open_tail(covered, date(2026, 11, 23), date(2026, 11, 25))
    assert trimmed == [(date(2024, 1, 1), date(2026, 11, 23))]
    old = trim_open_tail([(date(2015, 1, 1), date(2020, 12, 31))], date(2020, 6, 1), date(2026, 11, 25))
    assert old == [(date(2015, 1, 1), date(2020, 12, 31))]


def test_coverage_counts_a_missing_weekday(tmp_path):
    days = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 5)]
    frame = pd.DataFrame([{
        "time": session_stamp(day),
        "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
        "volume": 1.0, "oi": float("nan"), "session_type": "normal",
    } for day in days])
    path = tmp_path / "daily.parquet"
    write_parquet(frame, path)
    stats = coverage(path)
    assert stats["missing_weekdays"] == 1
    assert stats["largest_gap_days"] == 2
