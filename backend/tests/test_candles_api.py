"""GET /api/candles and GET /api/symbols."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.data.store import CandleStore
from app.main import app
from app.routes.candles import get_default_sessions, get_store
from tests.conftest import ist_ts

DEFAULT = ("normal", "weekend_full")


@pytest.fixture
def client(store: CandleStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_default_sessions] = lambda: DEFAULT
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_candles_basic(client: TestClient) -> None:
    res = client.get("/api/candles", params={"symbol": "NIFTY50", "timeframe": "1h"})
    assert res.status_code == 200
    body = res.json()
    assert body["symbol"] == "NIFTY50"
    assert body["timeframe"] == "1h"
    assert body["source_minutes"] == 5
    assert body["sessions"] == ["normal", "weekend_full"]
    assert len(body["candles"]) == 28
    assert body["candles"][0] == {
        "time": ist_ts(2024, 10, 28, 9, 15),
        "open": 100.0, "high": 113.0, "low": 99.0, "close": 112.0,
        "volume": 120.0, "oi": None,
    }  # fmt: skip


def test_candles_from_to_are_unix_seconds(client: TestClient) -> None:
    lo = ist_ts(2024, 10, 29, 9, 15)
    hi = ist_ts(2024, 10, 29, 9, 25)
    res = client.get(
        "/api/candles",
        params={"symbol": "NIFTY50", "timeframe": "5m", "from": lo, "to": hi},
    )
    assert res.status_code == 200
    assert [c["time"] for c in res.json()["candles"]] == [lo, lo + 300, lo + 600]


def test_sessions_param_overrides_default(client: TestClient) -> None:
    base = {"symbol": "NIFTY50", "timeframe": "1D"}
    assert len(client.get("/api/candles", params=base).json()["candles"]) == 4

    only_normal = client.get("/api/candles", params={**base, "sessions": "normal"}).json()
    assert only_normal["sessions"] == ["normal"]
    assert len(only_normal["candles"]) == 3

    everything = client.get(
        "/api/candles",
        params={**base, "sessions": "normal,weekend_full,special_short,muhurat"},
    ).json()
    assert len(everything["candles"]) == 6


def test_default_sessions_come_from_setting(store: CandleStore) -> None:
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_default_sessions] = lambda: ("normal",)
    try:
        res = TestClient(app).get("/api/candles", params={"symbol": "NIFTY50", "timeframe": "1D"})
        assert res.json()["sessions"] == ["normal"]
        assert len(res.json()["candles"]) == 3
    finally:
        app.dependency_overrides.clear()


def test_unknown_session_type_422(client: TestClient) -> None:
    res = client.get(
        "/api/candles",
        params={"symbol": "NIFTY50", "timeframe": "5m", "sessions": "normal,saturday"},
    )
    assert res.status_code == 422
    assert "saturday" in res.json()["detail"]


def test_unknown_symbol_404(client: TestClient) -> None:
    res = client.get("/api/candles", params={"symbol": "NOPE", "timeframe": "5m"})
    assert res.status_code == 404


@pytest.mark.parametrize("tf", ["2m", "4h", "xyz"])
def test_unknown_timeframe_422(client: TestClient, tf: str) -> None:
    res = client.get("/api/candles", params={"symbol": "NIFTY50", "timeframe": tf})
    assert res.status_code == 422


@pytest.mark.parametrize("tf", ["1m", "3m"])
def test_timeframe_finer_than_base_data_422_with_reason(client: TestClient, tf: str) -> None:
    res = client.get("/api/candles", params={"symbol": "NIFTY50", "timeframe": tf})
    assert res.status_code == 422
    assert "5m" in res.json()["detail"]


def test_missing_params_422(client: TestClient) -> None:
    assert client.get("/api/candles").status_code == 422


def test_symbols_lists_available_timeframes_and_session_defaults(client: TestClient) -> None:
    res = client.get("/api/symbols")
    assert res.status_code == 200
    body = res.json()
    assert body["default_sessions"] == ["normal", "weekend_full"]
    assert body["session_types"] == ["normal", "weekend_full", "special_short", "muhurat"]
    assert body["timeframes"] == ["1m", "3m", "5m", "15m", "30m", "1h", "1D", "1W"]
    assert body["symbols"] == [
        {
            "symbol": "NIFTY50",
            "display_name": "NIFTY50",  # no meta.json written for this fixture
            "instrument_key": None,
            "kind": None,
            "base_timeframe": "5m",
            "available_timeframes": ["5m", "15m", "30m", "1h", "1D", "1W"],
            "first_time": ist_ts(2024, 10, 28, 9, 15),
            "last_time": ist_ts(2024, 11, 17, 10, 55),
        }
    ]
