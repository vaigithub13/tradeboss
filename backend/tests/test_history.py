"""1m history ingestion: only missing ranges, windows <= 28 days, merges, resumability."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from app.data.history import (
    MIN_HISTORY_DATE,
    WINDOW_DAYS,
    merge_ranges,
    merge_window,
    missing_ranges,
    read_meta,
    read_parquet,
    split_windows,
    symbol_dir_name,
    sync_symbol,
    write_parquet_atomic,
)
from app.data.importer import build_frame
from app.data.store import CandleStore
from app.upstox.instruments import IST, Instrument
from tests.upstox_helpers import FakeClient, ist, session_rows

D = date
NIFTY = Instrument("NSE_INDEX|Nifty 50", "NIFTY", "Nifty 50", "index", "NSE_INDEX", "INDEX")
FUT = Instrument("NSE_FO|48704", "NIFTY FUT 27 OCT 26", "NIFTY", "future", "NSE_FO", "FUT", expiry=D(2026, 10, 27))
# Saturday 2026-10-03 04:00 IST: the previous trading day is Fri 10-02
NOW = ist(2026, 10, 3, 4, 0)


# ------------------------------------------------------------------ pure range arithmetic
def test_merge_ranges_merges_overlapping_and_adjacent_but_not_separated() -> None:
    got = merge_ranges([(D(2026, 1, 10), D(2026, 1, 20)), (D(2026, 1, 1), D(2026, 1, 9)), (D(2026, 1, 15), D(2026, 1, 25)), (D(2026, 3, 1), D(2026, 3, 2))])
    assert got == [(D(2026, 1, 1), D(2026, 1, 25)), (D(2026, 3, 1), D(2026, 3, 2))]
    assert merge_ranges([]) == []


def test_missing_ranges_returns_only_the_gaps() -> None:
    covered = [(D(2026, 1, 5), D(2026, 1, 10)), (D(2026, 1, 20), D(2026, 1, 25))]
    assert missing_ranges(covered, D(2026, 1, 1), D(2026, 1, 31)) == [
        (D(2026, 1, 1), D(2026, 1, 4)),
        (D(2026, 1, 11), D(2026, 1, 19)),
        (D(2026, 1, 26), D(2026, 1, 31)),
    ]
    assert missing_ranges(covered, D(2026, 1, 6), D(2026, 1, 9)) == []
    assert missing_ranges([], D(2026, 1, 1), D(2026, 1, 3)) == [(D(2026, 1, 1), D(2026, 1, 3))]
    assert missing_ranges(covered, D(2026, 2, 2), D(2026, 2, 1)) == []  # empty want
    assert missing_ranges(covered, D(2026, 1, 8), D(2026, 1, 22)) == [(D(2026, 1, 11), D(2026, 1, 19))]


def test_split_windows_never_exceeds_the_limit_and_covers_everything_exactly_once() -> None:
    rng = (D(2022, 1, 1), D(2026, 10, 2))
    wins = split_windows(rng)
    assert all((b - a).days + 1 <= WINDOW_DAYS for a, b in wins)
    assert wins[0][0] == rng[0] and wins[-1][1] == rng[1]
    assert all(wins[i][1] + timedelta(days=1) == wins[i + 1][0] for i in range(len(wins) - 1))
    assert split_windows((D(2026, 1, 1), D(2026, 1, 1))) == [(D(2026, 1, 1), D(2026, 1, 1))]
    assert split_windows((D(2026, 1, 2), D(2026, 1, 1))) == []


def test_symbol_dir_names_are_filename_safe_and_nifty_keeps_its_existing_folder() -> None:
    assert symbol_dir_name("NSE_INDEX|Nifty 50") == "NIFTY50"
    assert symbol_dir_name("NSE_INDEX|India VIX") == "NSE_INDEX_India_VIX"
    assert symbol_dir_name("NSE_EQ|INE002A01018") == "NSE_EQ_INE002A01018"
    assert "/" not in symbol_dir_name("NSE_FO|48704") and "|" not in symbol_dir_name("NSE_FO|48704")


# ------------------------------------------------------------------ merging
def frame(day: date, base: float) -> pd.DataFrame:
    raw = [{"t": int(datetime.strptime(r[0][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=IST).timestamp()) * 1000,
            "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5]} for r in session_rows(day, base=base)]
    return build_frame(raw, bar_minutes=1)[0]


def test_merge_window_replaces_the_days_of_the_window_and_keeps_the_rest() -> None:
    old = pd.concat([frame(D(2026, 9, 28), 100), frame(D(2026, 9, 29), 200), frame(D(2026, 9, 30), 300)], ignore_index=True)
    new = frame(D(2026, 9, 29), 999)
    merged = merge_window(old, new, (D(2026, 9, 29), D(2026, 9, 29)))
    assert len(merged) == 3 * 375
    assert merged["time"].is_monotonic_increasing and merged["time"].is_unique
    day29 = merged[(merged["time"] >= int(ist(2026, 9, 29).timestamp())) & (merged["time"] < int(ist(2026, 9, 30).timestamp()))]
    assert day29["open"].iloc[0] == 999 and len(day29) == 375
    assert merged["open"].iloc[0] == 100


def test_merge_window_with_an_empty_fetch_still_clears_nothing_outside_and_handles_empty_existing() -> None:
    empty = read_parquet(Path("/nonexistent/none.parquet"))
    assert len(merge_window(empty, empty, (D(2026, 1, 1), D(2026, 1, 2)))) == 0
    only_new = merge_window(empty, frame(D(2026, 9, 30), 5), (D(2026, 9, 30), D(2026, 9, 30)))
    assert len(only_new) == 375


# ------------------------------------------------------------------ sync_symbol
def test_first_sync_fetches_everything_newest_window_first_in_windows_of_at_most_28_days(tmp_path: Path) -> None:
    client = FakeClient()
    res = sync_symbol(client, tmp_path, NIFTY, from_date=D(2026, 7, 1), now=NOW)
    calls = client.historical_calls
    assert calls[0][2] == D(2026, 10, 2)  # up to YESTERDAY: today comes from the intraday endpoint
    assert all((b - a).days + 1 <= WINDOW_DAYS for _, a, b in calls)
    assert [c[2] for c in calls] == sorted((c[2] for c in calls), reverse=True)  # newest first
    assert calls[-1][1] == D(2026, 7, 1)
    assert client.intraday_calls == ["NSE_INDEX|Nifty 50"]
    assert res.windows_fetched == len(calls) and res.today_refreshed
    # NIFTY50 folder is reused for Nifty; 1m file is written there
    assert (tmp_path / "NIFTY50" / "1m.parquet").is_file()


def test_second_sync_fetches_only_what_is_missing_and_never_refetches_covered_days(tmp_path: Path) -> None:
    sync_symbol(FakeClient(), tmp_path, NIFTY, from_date=D(2026, 7, 1), now=NOW)
    again = FakeClient()
    sync_symbol(again, tmp_path, NIFTY, from_date=D(2026, 7, 1), now=NOW)
    assert again.historical_calls == []  # everything up to yesterday is covered
    assert again.intraday_calls == ["NSE_INDEX|Nifty 50"]  # today is always refreshed

    # a later day: only the new day(s) are asked for
    later = FakeClient()
    sync_symbol(later, tmp_path, NIFTY, from_date=D(2026, 7, 1), now=ist(2026, 10, 6, 4, 0))
    assert [(a, b) for _, a, b in later.historical_calls] == [(D(2026, 10, 3), D(2026, 10, 5))]

    # asking for older history fetches only the older gap
    older = FakeClient()
    sync_symbol(older, tmp_path, NIFTY, from_date=D(2026, 6, 3), now=ist(2026, 10, 6, 4, 0))
    assert [(a, b) for _, a, b in older.historical_calls] == [(D(2026, 6, 3), D(2026, 6, 30))]  # exactly 28 days


def test_stored_1m_is_complete_labeled_and_sorted(tmp_path: Path) -> None:
    sync_symbol(FakeClient(), tmp_path, NIFTY, from_date=D(2026, 9, 28), now=NOW)  # Mon..Fri 28/9-2/10
    store = CandleStore(tmp_path)
    assert store.base_minutes("NIFTY50") == 1
    candles, minutes = store.load("NIFTY50")
    assert minutes == 1 and len(candles) == 5 * 375
    assert [c["time"] for c in candles] == sorted(c["time"] for c in candles)
    first = candles[0]
    assert first["time"] == int(ist(2026, 9, 28, 9, 15).timestamp())  # the 09:15 bar is present
    assert candles[374]["time"] == int(ist(2026, 9, 28, 15, 29).timestamp())
    assert first["oi"] is None  # index: no OI
    assert store.dates_of_type("NIFTY50", "muhurat") == set()


def test_coverage_is_saved_after_each_window_so_an_interrupted_run_resumes(tmp_path: Path) -> None:
    class Dies(FakeClient):
        def historical_candles(self, key, from_date, to_date, **kw):  # noqa: ANN001
            if len(self.historical_calls) == 2:
                raise RuntimeError("network died")
            return super().historical_candles(key, from_date, to_date, **kw)

    with pytest.raises(RuntimeError):
        sync_symbol(Dies(), tmp_path, NIFTY, from_date=D(2026, 5, 1), now=NOW)
    meta = read_meta(tmp_path / "NIFTY50")
    assert len(meta.covered) == 1  # two newest windows (adjacent) merged into one range
    done_from = meta.covered[0][0]
    resume = FakeClient()
    sync_symbol(resume, tmp_path, NIFTY, from_date=D(2026, 5, 1), now=NOW)
    assert all(b < done_from for _, _, b in resume.historical_calls)
    assert resume.historical_calls[-1][1] == D(2026, 5, 1)


def test_holidays_and_empty_windows_still_count_as_covered(tmp_path: Path) -> None:
    class Empty(FakeClient):
        def historical_candles(self, key, from_date, to_date, **kw):  # noqa: ANN001
            self.historical_calls.append((key, from_date, to_date))
            return []

    sync_symbol(Empty(), tmp_path, FUT, from_date=D(2026, 9, 1), now=NOW)
    again = FakeClient()
    sync_symbol(again, tmp_path, FUT, from_date=D(2026, 9, 1), now=NOW)
    assert again.historical_calls == []
    parquet = tmp_path / symbol_dir_name(FUT.key) / "1m.parquet"
    assert not parquet.exists()
    assert not parquet.with_name(parquet.name + ".tmp").exists()


def test_an_empty_download_does_not_create_or_wipe_a_candle_file(tmp_path: Path) -> None:
    path = tmp_path / "NSE_FO_35005_26_12_2024" / "1m.parquet"
    write_parquet_atomic(frame(D(2026, 9, 30), 5), path)
    kept = path.read_bytes()
    empty = read_parquet(Path("/nonexistent/none.parquet"))
    write_parquet_atomic(empty, path)
    assert path.read_bytes() == kept
    missing = tmp_path / "NEW" / "1m.parquet"
    write_parquet_atomic(empty, missing)
    assert not missing.exists()
    assert not missing.with_name(missing.name + ".tmp").exists()


def test_an_interrupted_candle_write_keeps_the_previous_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "NIFTY50" / "1m.parquet"
    write_parquet_atomic(frame(D(2026, 9, 30), 5), path)
    kept = path.read_bytes()

    def boom(_df: pd.DataFrame, dest: Path) -> None:
        dest.write_bytes(b"partial")
        raise RuntimeError("interrupted")

    monkeypatch.setattr("app.data.importer._copy_parquet", boom)
    with pytest.raises(RuntimeError, match="interrupted"):
        write_parquet_atomic(frame(D(2026, 9, 30), 6), path)
    assert path.read_bytes() == kept
    assert not path.with_name(path.name + ".tmp").exists()


def test_history_never_goes_before_january_2022(tmp_path: Path) -> None:
    c = FakeClient()
    sync_symbol(c, tmp_path, NIFTY, from_date=D(2019, 1, 1), to_date=D(2022, 1, 20), now=NOW)
    assert min(a for _, a, _ in c.historical_calls) == MIN_HISTORY_DATE


def test_oi_is_kept_for_futures_and_dropped_for_indices_and_stocks(tmp_path: Path) -> None:
    sync_symbol(FakeClient(oi=7000.0), tmp_path, FUT, from_date=D(2026, 9, 28), to_date=D(2026, 9, 28), now=NOW)
    sync_symbol(FakeClient(oi=7000.0), tmp_path, NIFTY, from_date=D(2026, 9, 28), to_date=D(2026, 9, 28), now=NOW)
    fut_df = read_parquet(tmp_path / symbol_dir_name(FUT.key) / "1m.parquet")
    idx_df = read_parquet(tmp_path / "NIFTY50" / "1m.parquet")
    assert fut_df["oi"].iloc[0] == 7000.0
    assert idx_df["oi"].isna().all()


def test_meta_records_the_instrument_and_the_covered_ranges(tmp_path: Path) -> None:
    sync_symbol(FakeClient(), tmp_path, FUT, from_date=D(2026, 9, 1), to_date=D(2026, 9, 20), now=NOW)
    meta = read_meta(tmp_path / symbol_dir_name(FUT.key))
    assert meta.instrument["instrument_key"] == "NSE_FO|48704" and meta.instrument["expiry"] == "2026-10-27"
    assert meta.covered == [(D(2026, 9, 1), D(2026, 9, 20))]


# ------------------------------------------------------------------ today's partial session
def partial_today(day: date, bars: int) -> list[list]:
    return session_rows(day, bars=bars)


def test_todays_partial_session_is_kept_as_a_normal_session_not_special_short(tmp_path: Path) -> None:
    now = ist(2026, 10, 5, 11, 0)  # Monday, market open
    client = FakeClient(today_rows=lambda: partial_today(D(2026, 10, 5), 105))  # 09:15..11:00
    sync_symbol(client, tmp_path, NIFTY, from_date=D(2026, 10, 5), now=now)
    store = CandleStore(tmp_path)
    candles, _ = store.load("NIFTY50")  # default sessions = normal + weekend_full
    assert len(candles) == 105


def test_a_late_starting_partial_day_is_not_rescued(tmp_path: Path) -> None:
    now = ist(2026, 10, 5, 11, 0)
    rows = session_rows(D(2026, 10, 5), bars=40, start_minute=10 * 60 + 30)  # starts 10:30
    sync_symbol(FakeClient(today_rows=lambda: rows), tmp_path, NIFTY, from_date=D(2026, 10, 5), now=now)
    candles, _ = CandleStore(tmp_path).load("NIFTY50")
    assert candles == []  # special_short: hidden by default
    all_candles, _ = CandleStore(tmp_path).load("NIFTY50", session_types=["special_short"])
    assert len(all_candles) == 40


def test_today_is_refreshed_every_time_and_replaced_not_duplicated(tmp_path: Path) -> None:
    now = ist(2026, 10, 5, 11, 0)
    sync_symbol(FakeClient(today_rows=lambda: partial_today(D(2026, 10, 5), 60)), tmp_path, NIFTY, from_date=D(2026, 10, 5), now=now)
    sync_symbol(FakeClient(today_rows=lambda: partial_today(D(2026, 10, 5), 90)), tmp_path, NIFTY, from_date=D(2026, 10, 5), now=ist(2026, 10, 5, 11, 30))
    df = read_parquet(tmp_path / "NIFTY50" / "1m.parquet")
    assert len(df) == 90 and df["time"].is_unique
    assert read_meta(tmp_path / "NIFTY50").covered == []  # today is never marked covered


def test_after_the_close_a_complete_today_gets_its_real_label(tmp_path: Path) -> None:
    now = ist(2026, 10, 5, 16, 0)
    sync_symbol(FakeClient(today_rows=lambda: session_rows(D(2026, 10, 5))), tmp_path, NIFTY, from_date=D(2026, 10, 5), now=now)
    df = read_parquet(tmp_path / "NIFTY50" / "1m.parquet")
    assert set(df["session_type"]) == {"normal"} and len(df) == 375


def test_no_intraday_call_when_the_requested_range_ends_before_today(tmp_path: Path) -> None:
    c = FakeClient()
    sync_symbol(c, tmp_path, NIFTY, from_date=D(2026, 9, 1), to_date=D(2026, 9, 15), now=NOW)
    assert c.intraday_calls == []
