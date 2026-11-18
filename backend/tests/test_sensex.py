"""SENSEX instruments, history folder, live chart keys, and market-view straddle."""

from __future__ import annotations

import gzip
import json
from datetime import date, datetime
from pathlib import Path

from app.data.history import DIR_ALIASES, symbol_dir_name
from app.live.hub import parse_views
from app.live.service import LiveConfig, symbols_dirtied_by
from app.market_view.service import _atm_straddle_move, build_sensex_for
from app.upstox.instruments import (
    BSE_SNAPSHOT_FILE,
    IST,
    NIFTY_INDEX_KEY,
    SENSEX_INDEX_KEY,
    Instrument,
    InstrumentIndex,
    SNAPSHOT_FILE,
    load_instruments,
    take_bse_snapshot,
)


def _bse_master_bytes() -> bytes:
    rows = [
        {
            "instrument_key": SENSEX_INDEX_KEY,
            "trading_symbol": "SENSEX",
            "name": "SENSEX",
            "segment": "BSE_INDEX",
            "instrument_type": "INDEX",
        },
        {
            "instrument_key": "BSE_FO|111",
            "trading_symbol": "SENSEX 26 NOV 26 FUT",
            "name": "SENSEX",
            "segment": "BSE_FO",
            "instrument_type": "FUT",
            "expiry": int(datetime(2026, 11, 26, 15, 30, tzinfo=IST).timestamp() * 1000),
            "lot_size": 20,
            "underlying_key": SENSEX_INDEX_KEY,
        },
        {
            "instrument_key": "BSE_FO|222",
            "trading_symbol": "SENSEX 26 NOV 26 85000 CE",
            "name": "SENSEX",
            "segment": "BSE_FO",
            "instrument_type": "CE",
            "expiry": int(datetime(2026, 11, 26, 15, 30, tzinfo=IST).timestamp() * 1000),
            "strike_price": 85000,
            "lot_size": 20,
            "underlying_key": SENSEX_INDEX_KEY,
        },
        # filler so parse_master length check passes
        *[
            {
                "instrument_key": f"BSE_EQ|{i}",
                "trading_symbol": f"FILL{i}",
                "name": f"FILL{i}",
                "segment": "BSE_EQ",
                "instrument_type": "EQ",
                "lot_size": 1,
            }
            for i in range(1200)
        ],
    ]
    return gzip.compress(json.dumps(rows).encode())


def test_sensex_dir_alias_and_config_keys() -> None:
    assert DIR_ALIASES[SENSEX_INDEX_KEY] == "SENSEX"
    assert symbol_dir_name(SENSEX_INDEX_KEY) == "SENSEX"
    assert SENSEX_INDEX_KEY.startswith("BSE_INDEX|")


def test_bse_snapshot_and_sensex_offerings(tmp_path: Path) -> None:
    day = date(2026, 11, 18)
    # NSE stub so list_snapshots / day layout exists
    (tmp_path / day.isoformat()).mkdir(parents=True)
    (tmp_path / day.isoformat() / SNAPSHOT_FILE).write_bytes(gzip.compress(json.dumps([
        {
            "instrument_key": NIFTY_INDEX_KEY,
            "trading_symbol": "Nifty 50",
            "name": "Nifty 50",
            "segment": "NSE_INDEX",
            "instrument_type": "INDEX",
        },
        *[{"instrument_key": f"NSE_EQ|{i}", "trading_symbol": f"N{i}", "name": f"N{i}",
           "segment": "NSE_EQ", "instrument_type": "EQ", "lot_size": 1} for i in range(1200)],
    ]).encode()))
    result = take_bse_snapshot(
        tmp_path,
        now=datetime(2026, 11, 18, 9, 0, tzinfo=IST),
        fetch=_bse_master_bytes,
    )
    assert result.outcome == "saved"
    assert (tmp_path / day.isoformat() / BSE_SNAPSHOT_FILE).is_file()
    instruments = load_instruments(result.path, require_key=SENSEX_INDEX_KEY)
    keys = {i.key for i in instruments}
    assert SENSEX_INDEX_KEY in keys
    assert "BSE_FO|111" in keys
    assert "BSE_FO|222" in keys
    idx = InstrumentIndex(instruments, day)
    fut = idx.front_future(day, underlying_key=SENSEX_INDEX_KEY)
    assert fut is not None and fut.key == "BSE_FO|111"
    assert idx.front_future(day) is None  # no Nifty futures in this BSE-only set


def test_chart_keys_keep_nifty_ahead_of_sensex_within_cap(tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    from app.data.store import CandleStore
    from app.live.service import MAX_SUBSCRIPTIONS, LiveService

    cfg = LiveConfig(
        candles_dir=tmp_path / "candles",
        recordings_dir=tmp_path / "rec",
        state_dir=tmp_path / "state",
        instruments_dir=tmp_path / "inst",
        base_keys=(NIFTY_INDEX_KEY, "NSE_INDEX|India VIX", SENSEX_INDEX_KEY),
        sensex_live_enabled=True,
    )
    store = CandleStore(cfg.candles_dir)
    svc = LiveService(cfg, store, client_factory=lambda: None, enabled=False)
    svc.hub = MagicMock()
    svc.hub.watched_symbols.return_value = set()
    keys = svc._chart_keys()
    assert keys[0] == NIFTY_INDEX_KEY
    assert SENSEX_INDEX_KEY in keys
    assert len(keys) <= MAX_SUBSCRIPTIONS


def test_sensex_future_tick_dirties_sensex_not_nifty() -> None:
    dirty = symbols_dirtied_by(
        {"BSE_FO_111"},
        future_dir="NSE_FO_48704",
        sensex_future_dir="BSE_FO_111",
    )
    assert "SENSEX" in dirty
    assert "NIFTY50" not in dirty


def test_multi_view_parse_keeps_nifty_and_sensex() -> None:
    views = parse_views(
        {
            "views": [
                {"symbol": "NIFTY50", "timeframe": "5m", "sessions": ["normal"]},
                {"symbol": "SENSEX", "timeframe": "15m", "sessions": ["normal"]},
            ]
        },
        ("normal",),
    )
    assert [v.symbol for v in views] == ["NIFTY50", "SENSEX"]
    assert views[1].timeframe == "15m"


def test_atm_straddle_expected_move() -> None:
    chain = [
        {
            "strike_price": 85000,
            "call_options": {"market_data": {"ltp": 120.5}},
            "put_options": {"market_data": {"ltp": 110.0}},
        },
        {
            "strike_price": 85100,
            "call_options": {"market_data": {"ltp": 80.0}},
            "put_options": {"market_data": {"ltp": 150.0}},
        },
    ]
    out = _atm_straddle_move(chain, spot=85020)
    assert out is not None
    assert out["atm"] == 85000
    assert out["straddle"] == 230.5
    assert out["range"] == [84789.5, 85250.5]


def test_sensex_market_view_builds_from_daily(tmp_path: Path) -> None:
    from app.data.importer import build_frame, write_parquet
    from app.data.store import CandleStore
    from datetime import timedelta, timezone

    ist = timezone(timedelta(hours=5, minutes=30))
    sdir = tmp_path / "candles" / "SENSEX"
    sdir.mkdir(parents=True)
    rows = []
    for i in range(25):
        d = date(2026, 10, 1) + __import__("datetime").timedelta(days=i)
        if d.weekday() >= 5:
            continue
        t0 = int(datetime(d.year, d.month, d.day, 9, 15, tzinfo=ist).timestamp())
        # one 1m bar per day is enough for daily resample in store tests — write daily-ish 1m
        for m in range(375):
            ts = (t0 + m * 60) * 1000
            o = 84000 + i * 10
            rows.append({"t": ts, "open": o, "high": o + 20, "low": o - 15, "close": o + 5, "volume": 0})
    df, _ = build_frame(rows, bar_minutes=1)
    write_parquet(df, sdir / "1m.parquet")
    (sdir / "meta.json").write_text(json.dumps({
        "instrument": {"instrument_key": SENSEX_INDEX_KEY, "kind": "index", "symbol": "SENSEX"},
        "covered": [["2026-10-01", "2026-10-31"]],
        "source_empty": [],
    }))
    store = CandleStore(tmp_path / "candles")
    view = build_sensex_for(
        date(2026, 11, 1),
        data_dir=tmp_path,
        store=store,
        chain_fetcher=lambda: [
            {
                "strike_price": 84200,
                "call_options": {"market_data": {"ltp": 100}},
                "put_options": {"market_data": {"ltp": 95}},
            }
        ],
    )
    assert view["symbol"] == "SENSEX"
    assert view["structure"] is not None
    assert view["expected_move"]["straddle"] == 195
    assert (tmp_path / "market-view" / "sensex" / "2026-11-01.json").is_file()


def test_live_bar_builder_crosses_5m_boundary_for_sensex() -> None:
    """Same bar builder path as Nifty: ticks spanning a 5m open emit the closed prior minute."""
    from app.live.builder import CandleBuilder
    from app.live.model import Tick, minute_of

    day = date(2026, 11, 18)

    def T(h: int, m: int, s: int = 0) -> int:
        return int(datetime(day.year, day.month, day.day, h, m, s, tzinfo=IST).timestamp()) * 1000

    b = CandleBuilder(SENSEX_INDEX_KEY, day)
    assert b.on_tick(Tick(T(9, 19, 50), 85000.0, 1))
    assert b.on_tick(Tick(T(9, 20, 5), 85010.0, 1))  # crosses into next 5m bucket start
    events = b.take_events()
    closed = [e.bar.minute for e in events if e.bar.source != "filled" and e.bar.minute == minute_of(T(9, 19))]
    assert closed, "expected the 09:19 bar to emit when the clock crossed 09:20"
