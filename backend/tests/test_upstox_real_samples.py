"""Responses recorded from the real Upstox API (2026-10-01), replayed offline.

Locks in what the live API really does: newest-first candles, 375 1m bars for an index with
volume 0, 385 for a future (10 post-close prints after 15:30) with volume and OI."""

from __future__ import annotations

from datetime import date, datetime

import httpx

from app.data.importer import build_frame
from app.data.resampler import resample
from app.upstox.client import UpstoxClient, parse_candles
from app.upstox.instruments import IST
from app.upstox.token import DataToken
from tests.upstox_helpers import FAKE_TOKEN, fixture, mock_client

INDEX = "real_historical_1m_nifty_2026-10-01.json"
FUT = "real_historical_1m_future_2026-10-01.json"


def ts(h: int, m: int) -> int:
    return int(datetime(2026, 10, 1, h, m, tzinfo=IST).timestamp())


def test_real_index_day_is_newest_first_and_yields_a_complete_1m_session() -> None:
    rows = fixture(INDEX)["data"]["candles"]
    assert rows[0][0].startswith("2026-10-01T15:29") and rows[-1][0].startswith("2026-10-01T09:15")  # newest first
    bars = parse_candles(rows)
    df, rep = build_frame(bars, bar_minutes=1)
    assert (rep.read, rep.kept, rep.dropped_out_of_session, rep.sessions_by_type) == (375, 375, 0, {"normal": 1})
    assert df["time"].iloc[0] == ts(9, 15) and df["time"].iloc[-1] == ts(15, 29)
    assert (df["volume"] == 0).all()  # index candles carry no volume
    assert df["oi"].isna().all()


def test_real_future_day_drops_the_post_close_prints_and_keeps_volume_and_oi() -> None:
    bars = parse_candles(fixture(FUT)["data"]["candles"])
    assert len(bars) == 385
    df, rep = build_frame(bars, bar_minutes=1, keep_oi=True)
    assert (rep.kept, rep.dropped_out_of_session) == (375, 10)
    assert df["time"].iloc[-1] == ts(15, 29)
    assert df["volume"].sum() > 0 and df["oi"].notna().all()


def test_real_day_resamples_to_75_five_minute_bars_with_the_open_bar_at_0915() -> None:
    bars = parse_candles(fixture(INDEX)["data"]["candles"])
    df, _ = build_frame(bars, bar_minutes=1)
    candles = df.to_dict("records")
    five = resample(candles, "5m", 1)  # type: ignore[arg-type]
    assert len(five) == 75 and five[0]["time"] == ts(9, 15) and five[-1]["time"] == ts(15, 25)
    first_five = candles[:5]
    assert five[0]["open"] == first_five[0]["open"] and five[0]["close"] == first_five[4]["close"]
    assert five[0]["high"] == max(c["high"] for c in first_five)
    assert five[0]["low"] == min(c["low"] for c in first_five)


def test_client_replays_the_recorded_response_through_the_real_code_path() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fixture(INDEX))

    c = UpstoxClient(DataToken(FAKE_TOKEN), http=mock_client(handler))
    rows = c.historical_candles("NSE_INDEX|Nifty 50", date(2026, 10, 1), date(2026, 10, 1))
    assert len(parse_candles(rows)) == 375
