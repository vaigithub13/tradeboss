"""HTTP layer for the Upstox features, all on mocked data: no network, no real token."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.data.history import read_parquet, sync_symbol
from app.data.jobs import JobManager
from app.data.store import CandleStore
from app.main import app
from app.routes.candles import get_default_sessions, get_store
from app.upstox import deps
from app.upstox.client import UpstoxAuthError, UpstoxClient
from app.upstox.instruments import IST, take_snapshot
from app.upstox.status import StatusCache
from app.upstox.token import DataToken
from tests.upstox_helpers import FAKE_TOKEN, FakeClient, ist, json_response, make_master_bytes, mock_client


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    candles_dir, inst_dir = tmp_path / "candles", tmp_path / "instruments"
    take_snapshot(inst_dir, now=ist(2026, 10, 3, 9, 0), fetch=make_master_bytes)
    state: dict[str, Any] = {"runner": None, "auth_errors": 0}
    cache = StatusCache(ttl_s=0)

    def runner() -> Any:
        return state["runner"]

    jobs = JobManager(runner, deps.symbol_dir_name, threaded=False, on_auth_error=cache.invalidate)
    app.dependency_overrides[deps.get_candles_dir] = lambda: candles_dir
    app.dependency_overrides[deps.get_instruments_dir] = lambda: inst_dir
    app.dependency_overrides[deps.get_jobs] = lambda: jobs
    app.dependency_overrides[deps.get_status_cache] = lambda: cache
    app.dependency_overrides[get_store] = lambda: CandleStore(candles_dir)
    app.dependency_overrides[get_default_sessions] = lambda: ("normal", "weekend_full")
    state.update(candles_dir=candles_dir, inst_dir=inst_dir, jobs=jobs, client=TestClient(app))
    yield state
    app.dependency_overrides.clear()


# ------------------------------------------------------------------ token status
def test_status_missing_when_no_token_is_configured(env: dict[str, Any]) -> None:
    body = env["client"].get("/api/upstox/status").json()
    assert body["state"] == "missing" and "UPSTOX_ANALYTICS_TOKEN" in body["message"]


def test_status_valid_after_a_successful_cheap_call_and_the_response_never_contains_the_token(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic import SecretStr

    monkeypatch.setattr(settings, "upstox_analytics_token", SecretStr(FAKE_TOKEN))
    paths: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        paths.append(req.url.path)
        return json_response("market_status_nse.json")

    monkeypatch.setattr(
        deps, "make_client",
        lambda **kw: UpstoxClient(DataToken(FAKE_TOKEN), http=mock_client(handler), sleep=lambda s: None, **kw),
    )
    import app.routes.upstox as routes

    monkeypatch.setattr(routes, "make_client", deps.make_client)
    res = env["client"].get("/api/upstox/status", params={"refresh": True})
    assert res.json()["state"] == "valid"
    assert paths == ["/v2/market/status/NSE"]
    assert FAKE_TOKEN not in res.text


def test_status_invalid_when_upstox_rejects_the_token(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import SecretStr

    import app.routes.upstox as routes

    monkeypatch.setattr(settings, "upstox_analytics_token", SecretStr("opaque-token"))
    monkeypatch.setattr(
        routes, "make_client",
        lambda **kw: UpstoxClient(DataToken("opaque-token"), http=mock_client(lambda r: httpx.Response(401, json={"errors": [{"errorCode": "UDAPI100050", "message": "Invalid token"}]})), sleep=lambda s: None, **kw),
    )
    body = env["client"].get("/api/upstox/status").json()
    assert body["state"] == "invalid" and "opaque-token" not in str(body)


# ------------------------------------------------------------------ instruments
def test_search_returns_instruments_with_symbol_id_and_data_flags(env: dict[str, Any]) -> None:
    body = env["client"].get("/api/instruments/search", params={"q": "nifty", "limit": 5}).json()
    assert body["snapshot_date"] == "2026-10-03"
    first = body["items"][0]
    assert first["instrument_key"] == "NSE_INDEX|Nifty 50" and first["symbol_id"] == "NIFTY50"
    assert first["has_data"] is False and first["has_1m"] is False


def test_search_flags_stored_symbols(env: dict[str, Any]) -> None:
    sync_symbol(FakeClient(), env["candles_dir"], _nifty(env), from_date=date(2026, 9, 28), to_date=date(2026, 9, 28), now=ist(2026, 10, 3, 4, 0))
    items = env["client"].get("/api/instruments/search", params={"q": "nifty"}).json()["items"]
    nifty = next(i for i in items if i["instrument_key"] == "NSE_INDEX|Nifty 50")
    assert nifty["has_data"] and nifty["has_1m"]


def _nifty(env: dict[str, Any]):  # noqa: ANN202
    from app.upstox.instruments import current_index

    idx = current_index(env["inst_dir"])
    assert idx is not None
    inst = idx.get("NSE_INDEX|Nifty 50")
    assert inst is not None
    return inst


def test_search_kind_filter_and_validation(env: dict[str, Any]) -> None:
    c = env["client"]
    kinds = {i["kind"] for i in c.get("/api/instruments/search", params={"kind": "option", "limit": 50}).json()["items"]}
    assert kinds == {"option"}
    assert c.get("/api/instruments/search", params={"kind": "bond"}).status_code == 422


def test_search_without_any_snapshot_explains_what_to_do(env: dict[str, Any], tmp_path: Path) -> None:
    empty = tmp_path / "none"
    app.dependency_overrides[deps.get_instruments_dir] = lambda: empty
    body = env["client"].get("/api/instruments/search").json()
    assert body["items"] == [] and body["snapshot_date"] is None and "snapshot" in body["message"]


# ------------------------------------------------------------------ history sync
def use_fake(env: dict[str, Any], client: Any, now: Any) -> None:
    candles_dir = env["candles_dir"]

    def run(instrument, from_date, to_date, on_progress):  # noqa: ANN001, ANN202
        return sync_symbol(client, candles_dir, instrument, from_date=from_date, to_date=to_date, now=now, on_progress=on_progress)

    env["runner"] = run


def test_sync_runs_a_job_that_stores_1m_and_reports_progress(env: dict[str, Any]) -> None:
    fake = FakeClient()
    use_fake(env, fake, ist(2026, 10, 3, 4, 0))
    res = env["client"].post("/api/history/sync", json={"instrument_key": "NSE_EQ|INE002A01018", "from_date": "2026-09-14"})
    assert res.status_code == 200
    job = env["client"].get(f"/api/history/jobs/{res.json()['id']}").json()
    assert job["status"] == "done" and job["symbol"] == "NSE_EQ_INE002A01018"
    assert job["windows_done"] == job["windows_total"] >= 1 and job["bars_added"] > 0
    assert len(read_parquet(env["candles_dir"] / "NSE_EQ_INE002A01018" / "1m.parquet")) > 0
    syms = {s["symbol"]: s for s in env["client"].get("/api/symbols").json()["symbols"]}
    s = syms["NSE_EQ_INE002A01018"]
    assert s["base_timeframe"] == "1m" and "1m" in s["available_timeframes"] and "3m" in s["available_timeframes"]
    assert s["display_name"] == "RELIANCE" and s["instrument_key"] == "NSE_EQ|INE002A01018"


def test_default_start_depends_on_the_instrument(env: dict[str, Any]) -> None:
    fake = FakeClient()
    use_fake(env, fake, ist(2026, 10, 3, 4, 0))
    c = env["client"]
    c.post("/api/history/sync", json={"instrument_key": "NSE_EQ|INE002A01018"})  # a stock: ~90 days
    first_call_from = min(a for _, a, _ in fake.historical_calls)
    assert 85 <= (date(2026, 10, 3) - first_call_from).days <= 95
    fake.historical_calls.clear()
    c.post("/api/history/sync", json={"instrument_key": "NSE_FO|48704"})  # future: ~120 days
    assert 115 <= (date(2026, 10, 3) - min(a for _, a, _ in fake.historical_calls)).days <= 125


def test_sync_validates_input(env: dict[str, Any]) -> None:
    c = env["client"]
    assert c.post("/api/history/sync", json={"instrument_key": "NSE_EQ|NOPE"}).status_code == 404
    assert c.post("/api/history/sync", json={"instrument_key": "NSE_EQ|INE002A01018", "from_date": "2999-01-01"}).status_code == 422
    assert c.get("/api/history/jobs/unknown").status_code == 404


def test_sync_without_a_token_fails_the_job_with_a_clear_message(env: dict[str, Any]) -> None:
    env["runner"] = None
    job = env["client"].post("/api/history/sync", json={"instrument_key": "NSE_EQ|INE002A01018", "from_date": "2026-09-14"}).json()
    assert job["status"] == "error" and job["auth_error"] is True and "UPSTOX_ANALYTICS_TOKEN" in job["error"]


def test_a_rejected_token_mid_sync_is_reported_as_an_auth_error_not_a_crash(env: dict[str, Any]) -> None:
    def run(instrument, from_date, to_date, on_progress):  # noqa: ANN001, ANN202
        raise UpstoxAuthError("Upstox rejected the data token (HTTP 401, UDAPI100050): Invalid token")

    env["runner"] = run
    job = env["client"].post("/api/history/sync", json={"instrument_key": "NSE_EQ|INE002A01018", "from_date": "2026-09-14"}).json()
    assert job["status"] == "error" and job["auth_error"] is True


def test_unexpected_failures_are_contained_in_the_job(env: dict[str, Any]) -> None:
    def run(instrument, from_date, to_date, on_progress):  # noqa: ANN001, ANN202
        raise RuntimeError(f"disk exploded near Bearer {FAKE_TOKEN}")

    env["runner"] = run
    job = env["client"].post("/api/history/sync", json={"instrument_key": "NSE_EQ|INE002A01018", "from_date": "2026-09-14"}).json()
    assert job["status"] == "error" and FAKE_TOKEN not in job["error"] and job["auth_error"] is False


def test_coverage_endpoint_lists_fetched_ranges(env: dict[str, Any]) -> None:
    use_fake(env, FakeClient(), ist(2026, 10, 3, 4, 0))
    env["client"].post("/api/history/sync", json={"instrument_key": "NSE_EQ|INE002A01018", "from_date": "2026-09-14"})
    cov = env["client"].get("/api/history/coverage", params={"instrument_key": "NSE_EQ|INE002A01018"}).json()
    assert cov["covered"] == [["2026-09-14", "2026-10-02"]] and cov["symbol_id"] == "NSE_EQ_INE002A01018"


def test_a_second_request_for_the_same_instrument_while_running_returns_the_same_job(tmp_path: Path) -> None:
    started: list[Any] = []
    jobs = JobManager(lambda: (lambda *a: started.append(a) or None), deps.symbol_dir_name, threaded=False)  # type: ignore[arg-type,return-value]
    from app.upstox.instruments import Instrument

    inst = Instrument("NSE_EQ|X", "X", "X", "equity", "NSE_EQ", "EQ")
    j1 = jobs.start(inst, None, None)
    assert j1.status == "done" and len(started) == 1  # ran synchronously (threaded=False)
    j1.status = "running"
    assert jobs.start(inst, None, None) is j1 and len(started) == 1
