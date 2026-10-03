"""Forward pages (`after`) for the chart window: they mirror the backward pages exactly."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.data.service import get_candle_page, get_candle_page_after, get_candles
from app.data.store import CandleStore
from app.main import app
from app.routes.candles import get_default_sessions, get_store


def walk_forward(store: CandleStore, tf: str, limit: int) -> list[list[int]]:
    pages: list[list[int]] = []
    after = -1
    while True:
        page = get_candle_page_after(store, "NIFTY50", tf, limit=limit, after=after)
        if not page.candles:
            return pages
        pages.append([c["time"] for c in page.candles])
        after = page.candles[-1]["time"]
        if not page.has_more:
            return pages


@pytest.mark.parametrize("tf", ["5m", "15m", "1h", "1D", "1W"])
@pytest.mark.parametrize("limit", [1, 7, 70, 120])
def test_forward_pages_stitch_into_exactly_the_full_series(store: CandleStore, tf: str, limit: int) -> None:
    full = [c["time"] for c in get_candles(store, "NIFTY50", tf).candles]
    pages = walk_forward(store, tf, limit)
    assert [t for p in pages for t in p] == full
    assert all(len(p) == limit for p in pages[:-1])


def test_after_the_last_candle_there_is_nothing_more(store: CandleStore) -> None:
    last = get_candles(store, "NIFTY50", "15m").candles[-1]["time"]
    page = get_candle_page_after(store, "NIFTY50", "15m", limit=10, after=last)
    assert page.candles == [] and page.has_more is False


def test_a_backward_page_followed_by_a_forward_page_is_contiguous(store: CandleStore) -> None:
    newest = get_candle_page(store, "NIFTY50", "15m", limit=50)
    older = get_candle_page(store, "NIFTY50", "15m", limit=50, before=newest.candles[0]["time"])
    fwd = get_candle_page_after(store, "NIFTY50", "15m", limit=50, after=older.candles[-1]["time"])
    assert [c["time"] for c in fwd.candles] == [c["time"] for c in newest.candles]


@pytest.fixture
def client(store: CandleStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_default_sessions] = lambda: ("normal", "weekend_full")
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_api_after_returns_the_oldest_candles_and_has_more_newer(client: TestClient) -> None:
    full = client.get("/api/candles", params={"symbol": "NIFTY50", "timeframe": "15m"}).json()["candles"]
    cut = full[9]["time"]
    res = client.get("/api/candles", params={"symbol": "NIFTY50", "timeframe": "15m", "limit": 20, "after": cut})
    assert res.status_code == 200
    body = res.json()
    assert [c["time"] for c in body["candles"]] == [c["time"] for c in full[10:30]]
    assert body["has_more_newer"] is True
    last = client.get("/api/candles", params={"symbol": "NIFTY50", "timeframe": "15m", "limit": 20, "after": full[-5]["time"]}).json()
    assert len(last["candles"]) == 4 and last["has_more_newer"] is False


def test_api_after_rejects_combinations(client: TestClient) -> None:
    base = {"symbol": "NIFTY50", "timeframe": "15m", "after": 1}
    assert client.get("/api/candles", params=base).status_code == 422  # needs limit
    assert client.get("/api/candles", params={**base, "limit": 5, "before": 9}).status_code == 422
    assert client.get("/api/candles", params={**base, "limit": 5, "from": 9}).status_code == 422
