"""Pre-market view: parsers, the five checks, the verdict, scoring after the close, the route and the
08:50 hook. No test opens a socket (httpx MockTransport)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.market_view import service, sources
from app.market_view.view import (
    bias_of,
    build_view,
    day_type,
    expected_range,
    plan_of,
    score_crude,
    score_fii,
    score_global,
    score_outcome,
    score_structure,
    score_vix,
    structure,
)
from app.options.gex_scheduler import premarket_due
from app.routes import market_view as mv_route

IST = timezone(timedelta(hours=5, minutes=30))
DAY = date(2026, 11, 9)


def q(pct: float, last: float = 100.0) -> dict[str, Any]:
    return {"symbol": "X", "name": "X", "last": last, "prev_close": last / (1 + pct / 100), "change_pct": pct, "time": 0}


def daily(n: int, start: float = 24000.0, step: float = 20.0) -> list[dict[str, Any]]:
    out = []
    for i in range(n):
        base = start + i * step
        out.append({"time": i, "open": base, "high": base + 60, "low": base - 40, "close": base + 30})
    return out


# ------------------------------------------------------------------ parsers
def test_parse_yahoo_uses_the_previous_session_close() -> None:
    body = {"chart": {"result": [{"meta": {"regularMarketPrice": 101.0, "chartPreviousClose": 100.0,
                                           "regularMarketTime": 1700000000}}]}}
    got = sources.parse_yahoo(body, "^GSPC", "S&P 500")
    assert got["change_pct"] == pytest.approx(1.0) and got["time"] == 1700000000
    assert sources.parse_yahoo({"chart": {"result": []}}, "x", "x") is None
    assert sources.parse_yahoo({"chart": {"result": [{"meta": {"regularMarketPrice": 1}}]}}, "x", "x") is None


def test_parse_fii_dii() -> None:
    body = [
        {"category": "DII **", "date": "06-Nov-2026", "buyValue": "12,000.5", "sellValue": "10,000", "netValue": "2,000.50"},
        {"category": "FII/FPI *", "date": "06-Nov-2026", "buyValue": "9,000", "sellValue": "11,140", "netValue": "-2,140.00"},
    ]
    got = sources.parse_fii_dii(body)
    assert got == {"dii_net": 2000.5, "date": "06-Nov-2026", "fii_net": -2140.0}
    assert sources.parse_fii_dii({"x": 1}) is None


PARTICIPANT_CSV = """Participant wise Open Interest (no. of contracts) in Equity Derivatives as on Nov 06 2026
Client Type,Future Index Long,Future Index Short,Future Stock Long
Client,100,200,5
DII,50,60,5
FII,30000,70000,5
Pro,10,20,5
TOTAL,1,1,1
"""


def test_parse_participant_oi() -> None:
    got = sources.parse_participant_oi(PARTICIPANT_CSV)
    assert got["fii_long_pct"] == pytest.approx(30.0)
    assert sources.parse_participant_oi("nothing,here\n") is None


def test_participant_oi_skips_holidays_and_tries_both_hosts() -> None:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        if "nsearchives" in str(req.url):
            return httpx.Response(404)
        return httpx.Response(200, text=PARTICIPANT_CSV)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    # Monday 9 Nov: Sunday and Saturday are skipped, Friday 6 Nov is used
    got = sources.fetch_participant_oi(http, DAY, lambda d: d.weekday() < 5)
    assert got["date"] == "2026-11-06"
    assert [u.rsplit("_", 1)[1] for u in seen] == ["06112026.csv", "06112026.csv"]


def test_a_failing_quote_is_none_not_an_error() -> None:
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert sources.fetch_quote(http, "^GSPC", "S&P 500") is None
    assert sources.fetch_fii_dii(http) is None


# ------------------------------------------------------------------ the five checks
def test_global_mixes_us_futures_and_asia() -> None:
    f = score_global({"sp500": q(-0.8), "nasdaq": q(-1.0), "us_futures": q(-0.3), "nikkei": q(-0.5), "hang_seng": None})
    assert f["score"] == -1 and "US -0.90%" in f["reason"]
    assert score_global({"sp500": q(0.1)})["score"] == 0
    assert score_global({})["available"] is False


def test_fii_votes_cash_and_futures_positioning() -> None:
    assert score_fii({"fii_net": -2140.0, "dii_net": 2000.0}, {"fii_long_pct": 30.0 - 1})["score"] == -1
    assert score_fii({"fii_net": 1500.0}, {"fii_long_pct": 20.0})["score"] == 0
    assert score_fii(None, {"fii_long_pct": 60.0})["score"] == 1
    assert score_fii(None, None)["available"] is False


def test_crude_and_vix_are_inverted() -> None:
    assert score_crude(q(2.0, 80))["score"] == -1
    assert score_crude(q(-2.0, 80))["score"] == 1
    assert score_crude(q(0.5, 80))["score"] == 0
    assert score_vix(15.0, 14.0)["score"] == -1   # +7%
    assert score_vix(13.0, 14.0)["score"] == 1    # -7%
    assert score_vix(14.1, 14.0)["score"] == 0
    assert score_vix(None, None)["available"] is False


def test_structure_cpr_and_trend() -> None:
    st = structure(daily(25))
    y = daily(25)[-1]
    pivot = (y["high"] + y["low"] + y["close"]) / 3
    assert st["pivot"] == pytest.approx(pivot) and st["pdh"] == y["high"] and st["pdl"] == y["low"]
    assert st["tc"] >= st["bc"]
    assert st["trend"] == "up"
    assert score_structure(st)["score"] == 1
    down = structure(daily(25, start=26000, step=-20))
    assert down["trend"] == "down" and score_structure(down)["score"] == -1
    assert structure(daily(1)) is None


def test_bias_range_daytype_and_plan() -> None:
    f = lambda s: {"score": s}  # noqa: E731
    assert bias_of([f(1), f(1), f(0), f(None), f(1)]) == (3, "Bullish")
    assert bias_of([f(-1), f(0)]) == (-1, "Mild bearish")
    lo, hi = expected_range(25000, 15.0)
    assert hi - 25000 == pytest.approx(25000 * 0.15 / 252 ** 0.5)
    assert expected_range(25000, None) is None
    assert day_type({"cpr_ratio": 1.0}, -5.0)[0] == "trend"
    assert day_type({"cpr_ratio": 1.5}, 5.0)[0] == "range"
    assert day_type({"cpr_ratio": 0.5}, None)[0] == "trend"
    assert day_type(None, None)[0] == "mixed"
    st = {"pdh": 25180.0, "pdl": 24760.0, "tc": 25050.0, "bc": 24990.0}
    assert plan_of(-1, st, 24894.0).startswith("Puts on a break below 24,760")
    assert "Calls on a hold above 25,180" in plan_of(2, st, None)
    assert plan_of(0, st, None).startswith("No edge")


def test_build_view_and_score_after_the_close() -> None:
    view = build_view(
        day="2026-11-09", built_at=1, quotes={"sp500": q(-1.0)}, brent=q(0.1, 80), fii_cash={"fii_net": -2000.0},
        fii_oi=None, vix_last=14.0, vix_prev=13.0, daily=daily(25), gex={"total_net": 120.0, "gamma_flip": 24900.0},
    )
    assert [f["key"] for f in view["factors"]] == ["global", "fii", "crude", "vix", "structure"]
    assert view["score"] == sum(f["score"] for f in view["factors"])
    assert {lv["key"] for lv in view["levels"]} == {"pdh", "pdl", "tc", "bc", "range_hi", "range_lo"}
    lo, hi = view["expected_range"]
    candles = [{"open": 100.0, "high": 110.0, "low": 95.0, "close": 98.0},
               {"open": 98.0, "high": 99.0, "low": 88.0, "close": 89.0}]
    out = score_outcome({**view, "expected_range": [80.0, 120.0]}, candles)
    assert out["move"] == -11.0 and out["actual_type"] == "trend" and out["range_held"] is True
    assert out["bias_hit"] is (view["score"] < 0)
    assert score_outcome(view, []) is None


# ------------------------------------------------------------------ service, route, schedule
def test_build_for_survives_every_source_failing(tmp_path: Path) -> None:
    def factory() -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))

    view = service.build_for(DAY, data_dir=tmp_path, store=object(), is_trading_day=lambda d: True,
                             http_factory=factory, clock=lambda: 1.0)
    assert view["score"] == 0 and view["bias"] == "Neutral"
    assert all(not f["available"] for f in view["factors"])
    assert service.load_view(tmp_path, DAY)["day"] == "2026-11-09"


def test_history_rates(tmp_path: Path) -> None:
    for d, hit in (("2026-11-09", True), ("2026-11-10", False), ("2026-11-11", None)):
        service.save_view(tmp_path, {"day": d, "bias": "x", "score": 1, "day_type": "range",
                                     "outcome": {"bias_hit": hit, "range_held": True, "day_type_hit": None}})
    h = service.history(tmp_path)
    assert h["bias_hit"] == {"n": 2, "rate": 0.5}
    assert h["range_held"]["rate"] == 1.0 and h["day_type_hit"]["n"] == 0
    assert h["days"][0]["day"] == "2026-11-11"


@pytest.fixture
def route(tmp_path: Path):
    calls: list[date] = []

    def builder(day: date, **kw: Any) -> dict[str, Any]:
        calls.append(day)
        view = {"day": day.isoformat(), "bias": "Neutral", "n": len(calls)}
        service.save_view(kw["data_dir"], view)
        return view

    now = datetime(2026, 11, 9, 8, 55, tzinfo=IST).timestamp()
    app.dependency_overrides[mv_route.get_mv_data_dir] = lambda: tmp_path
    app.dependency_overrides[mv_route.get_mv_store] = lambda: object()
    app.dependency_overrides[mv_route.get_mv_clock] = lambda: (lambda: now)
    app.dependency_overrides[mv_route.get_mv_builder] = lambda: builder
    app.dependency_overrides[mv_route.get_mv_trading_day] = lambda: (lambda d: True)
    yield {"http": TestClient(app), "calls": calls, "dir": tmp_path}
    app.dependency_overrides.clear()


def test_route_builds_once_then_serves_the_saved_view(route: dict) -> None:
    assert route["http"].get("/api/market-view").json()["n"] == 1
    assert route["http"].get("/api/market-view").json()["n"] == 1
    assert route["http"].get("/api/market-view?refresh=true").json()["n"] == 2
    assert route["calls"] == [DAY, DAY]
    assert route["http"].get("/api/market-view/history").status_code == 200


def test_premarket_runs_once_between_0850_and_0914() -> None:
    at = lambda h, m: datetime(2026, 11, 9, h, m, tzinfo=IST)  # noqa: E731
    assert premarket_due(at(8, 49), None) is False
    assert premarket_due(at(8, 50), None) is True
    assert premarket_due(at(9, 0), DAY) is False
    assert premarket_due(at(9, 15), None) is False
