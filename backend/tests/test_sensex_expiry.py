"""Sensex option expiry selection + straddle failure reasons (market-view only)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.market_view.service import (
    build_sensex_for,
    nearest_sensex_expiry,
    sensex_option_expiries_from_instruments,
    sensex_view_stale,
)
from app.upstox.client import UpstoxApiError
from app.upstox.instruments import Instrument, SENSEX_INDEX_KEY

IST = timezone(timedelta(hours=5, minutes=30))


def test_nearest_expiry_keeps_today_during_session() -> None:
    today = date(2026, 11, 20)  # Friday Sensex weekly
    expiries = [today, date(2026, 11, 27), date(2026, 12, 4)]
    noon = datetime(2026, 11, 20, 10, 27, tzinfo=IST)
    assert nearest_sensex_expiry(expiries, today=today, now=noon) == today


def test_nearest_expiry_rolls_after_close_on_expiry_day() -> None:
    today = date(2026, 11, 20)
    expiries = [today, date(2026, 11, 27)]
    after = datetime(2026, 11, 20, 15, 31, tzinfo=IST)
    assert nearest_sensex_expiry(expiries, today=today, now=after) == date(2026, 11, 27)


def test_nearest_expiry_skips_past_dates() -> None:
    today = date(2026, 11, 21)
    expiries = [date(2026, 11, 20), date(2026, 11, 27)]
    assert nearest_sensex_expiry(expiries, today=today, now=datetime(2026, 11, 21, 10, 0, tzinfo=IST)) == date(
        2026, 11, 27
    )


def test_nearest_expiry_empty() -> None:
    assert nearest_sensex_expiry([], today=date(2026, 11, 20)) is None


def test_sensex_view_stale_when_straddle_missing() -> None:
    assert sensex_view_stale({"expected_move": None}) is True
    assert sensex_view_stale({"expected_move": {"atm": 82000}}) is False
    assert sensex_view_stale(None) is True


def test_build_records_reason_when_chain_fetcher_reports_http_error(tmp_path: Path) -> None:
    from app.data.importer import build_frame, write_parquet
    from app.data.store import CandleStore

    sdir = tmp_path / "candles" / "SENSEX"
    sdir.mkdir(parents=True)
    rows = []
    for i in range(5):
        d = date(2026, 11, 10) + timedelta(days=i)
        t0 = int(datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST).timestamp())
        for m in range(10):
            o = 82000.0
            rows.append(
                {"t": (t0 + m * 60) * 1000, "open": o, "high": o + 1, "low": o - 1, "close": o, "volume": 0}
            )
    df, _ = build_frame(rows, bar_minutes=1)
    write_parquet(df, sdir / "1m.parquet")
    (sdir / "meta.json").write_text(
        __import__("json").dumps(
            {
                "instrument": {"instrument_key": SENSEX_INDEX_KEY, "kind": "index", "symbol": "SENSEX"},
                "covered": [["2026-11-10", "2026-11-14"]],
                "source_empty": [],
            }
        )
    )
    store = CandleStore(tmp_path / "candles")

    def fail_chain() -> tuple[date | None, list[dict[str, Any]], str | None]:
        return date(2026, 11, 20), [], "no chain for expiry 2026-11-20: HTTP 400"

    view = build_sensex_for(
        date(2026, 11, 20),
        data_dir=tmp_path,
        store=store,
        chain_fetcher=fail_chain,
    )
    assert view["expected_move"] is None
    assert view["expected_move_reason"] == "no chain for expiry 2026-11-20: HTTP 400"


def test_build_records_reason_when_atm_quotes_missing(tmp_path: Path) -> None:
    from app.data.importer import build_frame, write_parquet
    from app.data.store import CandleStore

    sdir = tmp_path / "candles" / "SENSEX"
    sdir.mkdir(parents=True)
    t0 = int(datetime(2026, 11, 20, 9, 15, tzinfo=IST).timestamp())
    rows = [
        {"t": (t0 + m * 60) * 1000, "open": 82000.0, "high": 82010.0, "low": 81990.0, "close": 82031.8, "volume": 0}
        for m in range(30)
    ]
    # prior day so structure exists
    t1 = int(datetime(2026, 11, 19, 9, 15, tzinfo=IST).timestamp())
    rows.extend(
        {
            "t": (t1 + m * 60) * 1000,
            "open": 82100.0,
            "high": 82150.0,
            "low": 82050.0,
            "close": 82120.0,
            "volume": 0,
        }
        for m in range(30)
    )
    df, _ = build_frame(rows, bar_minutes=1)
    write_parquet(df, sdir / "1m.parquet")
    (sdir / "meta.json").write_text(
        __import__("json").dumps(
            {
                "instrument": {"instrument_key": SENSEX_INDEX_KEY, "kind": "index", "symbol": "SENSEX"},
                "covered": [["2026-11-19", "2026-11-20"]],
                "source_empty": [],
            }
        )
    )
    store = CandleStore(tmp_path / "candles")

    def chain_no_quotes() -> tuple[date | None, list[dict[str, Any]], str | None]:
        return (
            date(2026, 11, 20),
            [
                {
                    "strike_price": 82000,
                    "call_options": {"market_data": {}},
                    "put_options": {"market_data": {}},
                    "underlying_spot_price": 82031.8,
                }
            ],
            None,
        )

    view = build_sensex_for(
        date(2026, 11, 20),
        data_dir=tmp_path,
        store=store,
        chain_fetcher=chain_no_quotes,
    )
    assert view["expected_move"] is None
    assert "ATM quotes unavailable" in (view.get("expected_move_reason") or "")
    assert "2026-11-20" in view["expected_move_reason"]


def test_upstox_api_error_message_includes_status() -> None:
    err = UpstoxApiError("bad expiry", status=400, code="UDAPI100010")
    assert err.status == 400
    assert "bad expiry" in str(err)


def test_instrument_expiries_skip_past_and_non_sensex() -> None:
    class FakeIndex:
        instruments = [
            Instrument(
                key="BSE_FO|1",
                symbol="SENSEX26N2082000CE",
                name="SENSEX 20 NOV CE",
                kind="option",
                segment="BSE_FO",
                instrument_type="CE",
                expiry=date(2026, 11, 20),
                strike=82000.0,
                lot_size=10,
                underlying_key=SENSEX_INDEX_KEY,
            ),
            Instrument(
                key="BSE_FO|2",
                symbol="SENSEX26N2782000PE",
                name="SENSEX 27 NOV PE",
                kind="option",
                segment="BSE_FO",
                instrument_type="PE",
                expiry=date(2026, 11, 27),
                strike=82000.0,
                lot_size=10,
                underlying_key=SENSEX_INDEX_KEY,
            ),
            Instrument(
                key="NSE_FO|x",
                symbol="NIFTY",
                name="NIFTY",
                kind="option",
                segment="NSE_FO",
                instrument_type="CE",
                expiry=date(2026, 11, 25),
                strike=24000.0,
                lot_size=65,
                underlying_key="NSE_INDEX|Nifty 50",
            ),
            Instrument(
                key="BSE_FO|old",
                symbol="OLD",
                name="old",
                kind="option",
                segment="BSE_FO",
                instrument_type="CE",
                expiry=date(2026, 11, 13),
                strike=82000.0,
                lot_size=10,
                underlying_key=SENSEX_INDEX_KEY,
            ),
        ]

    assert sensex_option_expiries_from_instruments(FakeIndex(), today=date(2026, 11, 20)) == [
        date(2026, 11, 20),
        date(2026, 11, 27),
    ]
