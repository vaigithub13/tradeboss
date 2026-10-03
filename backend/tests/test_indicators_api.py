"""POST /api/indicators."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from app.data.store import CandleStore
from app.main import app
from app.routes.candles import get_default_sessions, get_store
from tests.conftest import ist_ts

TUE_OPEN = ist_ts(2024, 10, 29, 9, 15)


@pytest.fixture
def make_client() -> Iterator[Callable[[CandleStore], TestClient]]:
    def _make(store: CandleStore) -> TestClient:
        app.dependency_overrides[get_store] = lambda: store
        app.dependency_overrides[get_default_sessions] = lambda: ("normal", "weekend_full")
        return TestClient(app)

    yield _make
    app.dependency_overrides.clear()


def body(indicators: list[dict[str, object]], **extra: object) -> dict[str, object]:
    return {"symbol": "NIFTY50", "timeframe": "5m", "indicators": indicators, **extra}


def test_basic_response_shape_with_defaults_filled(
    make_client: Callable[[CandleStore], TestClient], store: CandleStore
) -> None:
    client = make_client(store)
    res = client.post(
        "/api/indicators",
        json=body([{"id": "a", "type": "ema", "params": {"length": 3}}], **{"from": TUE_OPEN}),
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["symbol"] == "NIFTY50" and data["timeframe"] == "5m"
    assert data["times"][0] == TUE_OPEN and len(data["times"]) == 225
    [ind] = data["indicators"]
    assert ind["id"] == "a" and ind["type"] == "ema"
    assert ind["params"] == {"length": 3, "source": "close"}  # default source filled in
    assert list(ind["outputs"]) == ["ema"]
    assert len(ind["outputs"]["ema"]) == 225


def test_params_are_optional_and_nan_is_null(
    make_client: Callable[[CandleStore], TestClient], store: CandleStore
) -> None:
    client = make_client(store)
    res = client.post("/api/indicators", json=body([{"id": "s", "type": "sma"}]))
    assert res.status_code == 200
    [ind] = res.json()["indicators"]
    assert ind["params"] == {"length": 20, "source": "close"}
    values = ind["outputs"]["sma"]
    assert values[:19] == [None] * 19 and values[19] is not None


def test_multiple_indicators_and_copies_in_one_request(
    make_client: Callable[[CandleStore], TestClient], store: CandleStore
) -> None:
    client = make_client(store)
    res = client.post(
        "/api/indicators",
        json=body(
            [
                {"id": "ema-20", "type": "ema", "params": {"length": 20}},
                {"id": "ema-50", "type": "ema", "params": {"length": 50}},
                {"id": "bb", "type": "bb"},
                {"id": "st", "type": "supertrend"},
                {"id": "rsi", "type": "rsi"},
                {"id": "macd", "type": "macd"},
                {"id": "vwap", "type": "vwap"},
            ],
            timeframe="15m",
        ),
    )
    assert res.status_code == 200, res.text
    inds = {i["id"]: i for i in res.json()["indicators"]}
    assert list(inds) == ["ema-20", "ema-50", "bb", "st", "rsi", "macd", "vwap"]
    assert list(inds["bb"]["outputs"]) == ["basis", "upper", "lower"]
    assert list(inds["macd"]["outputs"]) == ["macd", "signal", "hist"]
    assert list(inds["st"]["outputs"]) == ["supertrend", "direction"]


def test_from_to_sessions_are_honoured(
    make_client: Callable[[CandleStore], TestClient], store: CandleStore
) -> None:
    client = make_client(store)
    res = client.post(
        "/api/indicators",
        json=body(
            [{"id": "e", "type": "ema"}],
            timeframe="1D",
            sessions=["normal"],
        ),
    )
    assert res.status_code == 200
    assert len(res.json()["times"]) == 3  # normal sessions only (no Saturday budget session)


# --------------------------------------------------------------------------- errors
def test_vwap_on_zero_volume_symbol_is_422(
    make_client: Callable[[CandleStore], TestClient], zero_volume_store: CandleStore
) -> None:
    client = make_client(zero_volume_store)
    res = client.post("/api/indicators", json=body([{"id": "v", "type": "vwap"}]))
    assert res.status_code == 422
    assert "volume" in res.json()["detail"].lower()


def test_vwap_on_daily_is_422(
    make_client: Callable[[CandleStore], TestClient], store: CandleStore
) -> None:
    client = make_client(store)
    res = client.post("/api/indicators", json=body([{"id": "v", "type": "vwap"}], timeframe="1D"))
    assert res.status_code == 422
    assert "intraday" in res.json()["detail"].lower()


@pytest.mark.parametrize(
    "indicators",
    [
        [{"id": "x", "type": "nope"}],
        [{"id": "x", "type": "ema", "params": {"length": 0}}],
        [{"id": "x", "type": "ema", "params": {"source": "volume"}}],
        [{"id": "x", "type": "macd", "params": {"fast": 30, "slow": 20}}],
        [{"id": "x", "type": "ema"}, {"id": "x", "type": "sma"}],  # duplicate id
        [{"id": "", "type": "ema"}],
    ],
)
def test_invalid_requests_are_422(
    make_client: Callable[[CandleStore], TestClient], store: CandleStore,
    indicators: list[dict[str, object]],
) -> None:  # fmt: skip
    client = make_client(store)
    res = client.post("/api/indicators", json=body(indicators))
    assert res.status_code == 422


def test_unknown_symbol_404_and_unavailable_timeframe_422(
    make_client: Callable[[CandleStore], TestClient], store: CandleStore
) -> None:
    client = make_client(store)
    assert client.post(
        "/api/indicators", json={**body([{"id": "e", "type": "ema"}]), "symbol": "NOPE"}
    ).status_code == 404
    assert client.post(
        "/api/indicators", json=body([{"id": "e", "type": "ema"}], timeframe="3m")
    ).status_code == 422
