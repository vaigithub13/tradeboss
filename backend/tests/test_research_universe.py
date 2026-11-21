"""Phase A research universe: resolve, corporate-action factors, quality jumps."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from app.data.importer import COLUMNS, write_parquet
from app.data.history import symbol_dir_name
from app.research.corporate_actions import (
    ADJ_NAME,
    LEDGER_NAME,
    RAW_NAME,
    CorporateAction,
    apply_adjustments,
    factors_for,
    parse_corporate_actions,
    parse_ratio,
    write_adjusted_series,
)
from app.research.quality import detect_split_jumps, assess_symbol
from app.research.universe import equity_isin, load_universe_spec, resolve_universe
from app.upstox.instruments import BANKNIFTY_INDEX_KEY, Instrument, InstrumentIndex


def test_universe_spec_lists_thirty_stocks_and_banknifty() -> None:
    spec = load_universe_spec()
    assert spec["id"] == "research_universe_v1"
    assert spec["as_of"] == "2026-11-21"
    assert spec["history_from"] == "2022-01-03"
    assert spec["index"]["instrument_key"] == BANKNIFTY_INDEX_KEY
    assert len(spec["stocks"]) == 30
    assert "TATAMOTORS" in spec["stocks"]
    assert "M&M" in spec["stocks"]


def test_resolve_universe_exact_match_only_and_reports_missing() -> None:
    bank = Instrument(BANKNIFTY_INDEX_KEY, "Nifty Bank", "Nifty Bank", "index", "NSE_INDEX", "INDEX")
    fut = Instrument(
        "NSE_FO|99999",
        "BANKNIFTY26NOVFUT",
        "Bank Nifty",
        "future",
        "NSE_FO",
        "FUT",
        expiry=date(2026, 11, 25),
        underlying_key=BANKNIFTY_INDEX_KEY,
    )
    hdfc = Instrument("NSE_EQ|INE040A01034", "HDFCBANK", "HDFC Bank", "equity", "NSE_EQ", "EQ")
    # No TATAMOTORS — demerger leave unresolved
    idx = InstrumentIndex([bank, fut, hdfc], snapshot_day=date(2026, 11, 21))
    res = resolve_universe(idx, today=date(2026, 11, 21))
    symbols = {m.symbol for m in res.resolved}
    assert "Nifty Bank" in symbols or "BANKNIFTY" in {m.folder for m in res.resolved}
    assert any(m.role == "future" for m in res.resolved)
    assert any(m.symbol == "HDFCBANK" for m in res.resolved)
    assert "TATAMOTORS" in res.unresolved
    assert any("TATAMOTORS" in n for n in res.notes)
    assert "TMPV" not in res.unresolved  # never silently substituted


def test_equity_isin() -> None:
    assert equity_isin("NSE_EQ|INE002A01018") == "INE002A01018"
    assert equity_isin("NSE_INDEX|Nifty Bank") is None


def test_split_and_bonus_factors() -> None:
    assert parse_ratio("1:5") == (1.0, 5.0)
    assert factors_for("Split", "1:5") == (1 / 5, 5 / 1)
    assert factors_for("Bonus", "1:1") == (1 / 2, 2 / 1)
    assert factors_for("Dividend", "1:1") == (None, None)


def test_parse_corporate_actions_reads_ex_date_and_ratio() -> None:
    payload = [
        {
            "name": "Split",
            "ratio": "1:2",
            "expiry_date": "04 Jul 2023",
            "amount": None,
        },
        {
            "name": "Bonus",
            "ratio": "1:1",
            "event_details": [{"name": "Ex Date", "value": "15 Sep 2024"}],
        },
        {"name": "Dividend", "expiry_date": "01 Jan 2024", "amount": 10.0, "ratio": None},
    ]
    actions = parse_corporate_actions(payload)
    assert len(actions) == 3
    split = next(a for a in actions if a.kind == "Split")
    assert split.ex_date == date(2023, 7, 4)
    assert split.price_factor == pytest.approx(0.5)
    bonus = next(a for a in actions if a.kind == "Bonus")
    assert bonus.ex_date == date(2024, 9, 15)
    assert bonus.price_factor == pytest.approx(0.5)


def _minute_bar(day: date, close: float, volume: float = 100.0) -> dict:
    # 09:15 IST
    from datetime import datetime, timedelta, timezone

    ist = timezone(timedelta(hours=5, minutes=30))
    ts = int(datetime(day.year, day.month, day.day, 9, 15, tzinfo=ist).timestamp())
    return {
        "time": ts,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": volume,
        "oi": 0.0,
        "session_type": "normal",
    }


def test_apply_adjustments_multiplies_bars_before_ex_date() -> None:
    pre = _minute_bar(date(2023, 7, 3), 200.0, volume=100.0)
    post = _minute_bar(date(2023, 7, 4), 100.0, volume=200.0)
    df = pd.DataFrame([pre, post], columns=COLUMNS)
    actions = [
        CorporateAction("Split", date(2023, 7, 4), "1:2", None, 0.5, 2.0, {}),
    ]
    out = apply_adjustments(df, actions, as_of=date(2026, 11, 21))
    assert out.iloc[0]["close"] == pytest.approx(100.0)
    assert out.iloc[0]["volume"] == pytest.approx(200.0)
    assert out.iloc[1]["close"] == pytest.approx(100.0)
    assert out.iloc[1]["volume"] == pytest.approx(200.0)


def test_write_adjusted_series_leaves_raw_and_writes_ledger(tmp_path: Path) -> None:
    pre = _minute_bar(date(2023, 7, 3), 200.0)
    post = _minute_bar(date(2023, 7, 4), 100.0)
    write_parquet(pd.DataFrame([pre, post], columns=COLUMNS), tmp_path / RAW_NAME)
    actions = [CorporateAction("Split", date(2023, 7, 4), "1:2", None, 0.5, 2.0, {})]
    ledger = write_adjusted_series(tmp_path, actions, as_of=date(2026, 11, 21), source="test")
    assert (tmp_path / ADJ_NAME).is_file()
    assert (tmp_path / LEDGER_NAME).is_file()
    assert (tmp_path / RAW_NAME).is_file()
    assert ledger["applied_splits_bonuses"][0]["ex_date"] == "2023-07-04"
    assert ledger["rows_raw"] == 2


def test_detect_split_jumps_flags_unexplained_gap() -> None:
    closes = [
        (date(2023, 7, 3), 200.0),
        (date(2023, 7, 4), 100.0),  # 50% drop
        (date(2023, 7, 5), 101.0),
    ]
    hits = detect_split_jumps(closes, ca_dates=set())
    assert len(hits) == 1
    assert "50.0%" in hits[0]
    assert detect_split_jumps(closes, ca_dates={date(2023, 7, 4)}) == []


def test_banknifty_dir_alias() -> None:
    assert symbol_dir_name(BANKNIFTY_INDEX_KEY) == "BANKNIFTY"


def test_assess_symbol_empty_folder(tmp_path: Path) -> None:
    q = assess_symbol(
        symbol="HDFCBANK",
        folder="NSE_EQ_INE040A01034",
        role="equity",
        instrument_key="NSE_EQ|INE040A01034",
        candles_dir=tmp_path,
        history_from=date(2022, 1, 3),
        history_to=date(2022, 1, 10),
    )
    assert q.rows == 0
    assert q.missing_days > 0
    assert "no 1m_adj.parquet yet" in q.notes
