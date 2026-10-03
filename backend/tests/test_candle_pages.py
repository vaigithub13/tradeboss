"""Lazy-loading pages: get_candle_page and GET /api/candles?limit=&before=."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.data.service import get_candle_page, get_candles
from app.data.store import CandleStore, SymbolNotFound
from app.main import app
from app.routes.candles import get_default_sessions, get_store

REAL_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "candles"
ALL_TYPES = ("normal", "weekend_full", "special_short", "muhurat")


def walk_back(store: CandleStore, tf: str, limit: int, **kw: object) -> list[list[int]]:
    """Fetch pages newest -> oldest like the chart does; returns each page's times."""
    pages: list[list[int]] = []
    before: int | None = None
    while True:
        page = get_candle_page(store, "NIFTY50", tf, limit=limit, before=before, **kw)  # type: ignore[arg-type]
        pages.append([c["time"] for c in page.candles])
        if not page.has_more:
            return pages
        before = page.candles[0]["time"]


@pytest.mark.parametrize("tf", ["5m", "15m", "1h", "1D", "1W"])
@pytest.mark.parametrize("limit", [1, 7, 70, 120])
def test_pages_stitch_into_exactly_the_full_series(store: CandleStore, tf: str, limit: int) -> None:
    full = [c["time"] for c in get_candles(store, "NIFTY50", tf).candles]
    pages = walk_back(store, tf, limit)
    stitched = [t for page in reversed(pages) for t in page]
    assert stitched == full
    assert all(len(p) == limit for p in pages[:-1])  # only the oldest page may be short
    assert 1 <= len(pages[-1]) <= limit


def test_latest_page_is_the_tail(store: CandleStore) -> None:
    full = get_candles(store, "NIFTY50", "5m").candles
    page = get_candle_page(store, "NIFTY50", "5m", limit=100)
    assert page.candles == full[-100:]
    assert page.has_more is True
    assert page.source_minutes == 5


def test_has_more_is_false_when_everything_fits(store: CandleStore) -> None:
    total = len(get_candles(store, "NIFTY50", "5m").candles)
    exact = get_candle_page(store, "NIFTY50", "5m", limit=total)
    assert len(exact.candles) == total and exact.has_more is False
    huge = get_candle_page(store, "NIFTY50", "5m", limit=total + 1000)
    assert len(huge.candles) == total and huge.has_more is False


def test_before_is_exclusive_and_page_ends_just_before_it(store: CandleStore) -> None:
    full = get_candles(store, "NIFTY50", "5m").candles
    cut = full[150]["time"]
    page = get_candle_page(store, "NIFTY50", "5m", limit=40, before=cut)
    assert page.candles == full[110:150]
    assert page.has_more is True
    assert all(c["time"] < cut for c in page.candles)


def test_before_older_than_all_data_is_an_empty_last_page(store: CandleStore) -> None:
    first = get_candles(store, "NIFTY50", "5m").candles[0]["time"]
    page = get_candle_page(store, "NIFTY50", "5m", limit=10, before=first)
    assert page.candles == [] and page.has_more is False


def test_pages_respect_the_session_filter(store: CandleStore) -> None:
    default_full = get_candles(store, "NIFTY50", "5m").candles
    all_full = get_candles(store, "NIFTY50", "5m", session_types=ALL_TYPES).candles
    assert len(all_full) > len(default_full)
    page = get_candle_page(store, "NIFTY50", "5m", limit=10_000, session_types=ALL_TYPES)
    assert page.candles == all_full
    only_normal = get_candle_page(store, "NIFTY50", "5m", limit=10_000, session_types=("normal",))
    assert only_normal.candles == get_candles(store, "NIFTY50", "5m", session_types=("normal",)).candles


def test_errors(store: CandleStore) -> None:
    with pytest.raises(ValueError):
        get_candle_page(store, "NIFTY50", "5m", limit=0)
    with pytest.raises(SymbolNotFound):
        get_candle_page(store, "NOPE", "5m", limit=5)


@pytest.mark.skipif(not (REAL_DATA_DIR / "NIFTY50" / "5m.parquet").exists(), reason="no real data")
@pytest.mark.parametrize("tf", ["5m", "15m", "1h", "1D"])
def test_real_nifty_pages_stitch_into_the_full_series(tf: str) -> None:
    real = CandleStore(REAL_DATA_DIR)
    full = [c["time"] for c in get_candles(real, "NIFTY50", tf).candles]
    pages = walk_back(real, tf, 2000)
    assert [t for p in reversed(pages) for t in p] == full


# --------------------------------------------------------------------------- HTTP
@pytest.fixture
def client(store: CandleStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_default_sessions] = lambda: ("normal", "weekend_full")
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_api_limit_returns_newest_with_has_more(client: TestClient) -> None:
    res = client.get("/api/candles", params={"symbol": "NIFTY50", "timeframe": "5m", "limit": 100})
    assert res.status_code == 200
    body = res.json()
    assert len(body["candles"]) == 100 and body["has_more"] is True
    full = client.get("/api/candles", params={"symbol": "NIFTY50", "timeframe": "5m"}).json()
    assert body["candles"] == full["candles"][-100:]
    assert full["has_more"] is False  # no limit -> the whole range


def test_api_before_pages_backwards_until_has_more_is_false(client: TestClient) -> None:
    times: list[int] = []
    before: int | None = None
    for _ in range(10):
        params: dict[str, object] = {"symbol": "NIFTY50", "timeframe": "5m", "limit": 120}
        if before is not None:
            params["before"] = before
        body = client.get("/api/candles", params=params).json()
        times = [c["time"] for c in body["candles"]] + times
        before = body["candles"][0]["time"]
        if not body["has_more"]:
            break
    assert len(times) == 300 and times == sorted(set(times))


def test_api_before_without_limit_gives_everything_older(client: TestClient) -> None:
    full = client.get("/api/candles", params={"symbol": "NIFTY50", "timeframe": "5m"}).json()["candles"]
    cut = full[100]["time"]
    older = client.get("/api/candles", params={"symbol": "NIFTY50", "timeframe": "5m", "before": cut}).json()
    assert [c["time"] for c in older["candles"]] == [c["time"] for c in full[:100]]


@pytest.mark.parametrize(
    "extra",
    [{"limit": 0}, {"limit": -3}, {"limit": 10, "from": 1}, {"limit": 10, "to": 5}],
)
def test_api_bad_limit_combinations_are_422(client: TestClient, extra: dict[str, int]) -> None:
    res = client.get("/api/candles", params={"symbol": "NIFTY50", "timeframe": "5m", **extra})
    assert res.status_code == 422


def test_api_limit_errors_keep_their_status_codes(client: TestClient) -> None:
    get: Callable[..., int] = lambda **p: client.get("/api/candles", params={"limit": 5, **p}).status_code  # noqa: E731
    assert get(symbol="NOPE", timeframe="5m") == 404
    assert get(symbol="NIFTY50", timeframe="3m") == 422
