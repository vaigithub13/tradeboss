"""Phase 5: the AI chart analyser.

These tests describe the public API. They were written before the implementation.
A fake model stands in for OpenAI. Nothing here opens a socket.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256

import pytest

from app.ai.analyse import MAX_RETRIES, analyse, analysis_prompt
from app.ai.chain import fetch_option_chain, weekly_expiry
from app.ai.context import CANDLE_LIMIT, LEVEL_BAND, build_context, context_hash, swing_points
from app.ai.cost import analysis_model, cost_label, neutral_band
from app.ai.record import hit_rates, save_analysis, score_analysis
from app.ai.schema import AnalysisError, validate_analysis
from app.indicators.registry import compute
from app.indicators.frame import candles_to_frame
from app.options.events import EventCalendar, EventDay

IST = timezone(timedelta(hours=5, minutes=30))
SECRET = "super-secret-token"


def ts(y: int, m: int, d: int, h: int = 9, mi: int = 15) -> int:
    return int(datetime(y, m, d, h, mi, tzinfo=IST).timestamp())


def bar(t: int, price: float, *, high: float | None = None, low: float | None = None) -> dict:
    return {
        "time": t, "open": price, "high": price if high is None else high,
        "low": price if low is None else low, "close": price, "volume": 1.0,
    }


def rising(start: int, n: int, step_min: int, price: float = 100.0) -> list[dict]:
    return [bar(start + i * step_min * 60, price + i) for i in range(n)]


def valid_analysis(price: float = 100.0) -> dict:
    return {
        "trends": {"5m": "up", "15m": "up", "1h": "sideways", "1D": "down"},
        "bias": "bull",
        "key_levels": [
            {"price": price - 1, "kind": "support", "label": "swing low"},
            {"price": price + 1, "kind": "resistance", "label": "swing high"},
        ],
        "patterns": ["higher lows"],
        "bull": {"trigger": price + 2, "invalidation": price - 2, "note": "holds above the low"},
        "bear": {"trigger": price - 2, "invalidation": price + 2, "note": "loses the low"},
        "confidence": 0.5,
        "reasoning": "The last swing low is intact.",
    }


class _Chain:
    def __init__(self, payload=None, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def get_json(self, path: str, params: dict) -> dict:
        self.calls.append((path, dict(params)))
        if self.error is not None:
            raise self.error
        return self.payload


class _Model:
    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.prompts: list[str] = []
        self.images: list[bytes | None] = []

    def complete(self, prompt: str, image: bytes | None = None) -> dict:
        self.prompts.append(prompt)
        self.images.append(image)
        return {
            "text": self._replies.pop(0),
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }


def _context(**overrides):
    as_of = ts(2026, 1, 6, 10, 0)
    five = rising(ts(2026, 1, 5), 50, 5)
    fifteen = rising(ts(2026, 1, 5), 20, 15)
    hourly = rising(ts(2026, 1, 5), 10, 60)
    daily = [bar(ts(2026, 1, d), 100.0 + d) for d in range(1, 8)]
    base = {
        "symbol": "RELIANCE",
        "as_of": as_of,
        "candles": {"5m": five, "15m": fifteen, "1h": hourly, "1D": daily},
        "vix": [bar(as_of - 60, 14.0)],
        "events": EventCalendar([EventDay(datetime(2026, 1, 6, tzinfo=IST).date(), "budget", "Budget")]),
        "chain": _Chain(),
    }
    base.update(overrides)
    return build_context(**base)


def test_context_is_compact_and_uses_the_shared_indicators() -> None:
    assert CANDLE_LIMIT == 40
    assert LEVEL_BAND == 0.05
    chain = _Chain()
    ctx = _context(chain=chain)
    assert chain.calls == []
    assert ctx["options"] is None
    assert ctx["symbol"] == "RELIANCE"
    assert len(ctx["timeframes"]["5m"]["candles"]) == 40
    frame = candles_to_frame(ctx["timeframes"]["5m"]["candles"])
    ema = compute(frame, "ema", {"length": 20, "source": "close"})["ema"][-1]
    rsi = compute(frame, "rsi", {"length": 14, "source": "close"})["rsi"][-1]
    macd = compute(frame, "macd", {"fast": 12, "slow": 26, "signal": 9, "source": "close"})
    assert ctx["timeframes"]["5m"]["indicators"]["ema20"] == pytest.approx(float(ema))
    assert ctx["timeframes"]["5m"]["indicators"]["rsi14"] == pytest.approx(float(rsi))
    assert ctx["timeframes"]["5m"]["indicators"]["macd"] == pytest.approx(float(macd["macd"][-1]))
    assert set(ctx["timeframes"]) == {"5m", "15m", "1h", "1D"}
    assert ctx["events"]["event_day"] is True
    assert "Budget" in ctx["events"]["names"]
    assert ctx["vix"] == {"value": 14.0, "stale": False}
    assert set(ctx["day"]) == {"open", "high", "low", "close"}
    dumped = json.dumps(ctx)
    assert SECRET not in dumped
    for banned in ("authorization", "api_key", "token", "Bearer"):
        assert banned not in dumped


def test_swings_are_strict_local_extremes_and_the_day_range_is_this_session() -> None:
    highs = [1, 2, 5, 2, 1, 3, 8, 3, 1]
    lows = [5, 4, 1, 4, 5, 4, 0, 4, 5]
    bars = [
        {"time": i, "open": 1, "high": highs[i], "low": lows[i], "close": 1, "volume": 1}
        for i in range(len(highs))
    ]
    swings = swing_points(bars, wing=2)
    assert [point["price"] for point in swings["highs"]] == [5, 8]
    assert [point["price"] for point in swings["lows"]] == [1, 0]
    as_of = ts(2026, 1, 6, 10, 0)
    monday = [bar(ts(2026, 1, 5, 9, 15), 90, high=91, low=89)]
    tuesday = [
        bar(ts(2026, 1, 6, 9, 15), 100, high=101, low=99),
        bar(ts(2026, 1, 6, 9, 20), 102, high=110, low=98),
    ]
    ctx = _context(as_of=as_of, candles={
        "5m": monday + tuesday, "15m": tuesday, "1h": tuesday, "1D": tuesday,
    })
    assert ctx["day"] == {"open": 100, "high": 110, "low": 98, "close": 102}
    expected = swing_points(monday + tuesday, wing=2)
    assert [point["price"] for point in ctx["levels"]["resistance"]] == [point["price"] for point in expected["highs"][-3:]]
    assert [point["price"] for point in ctx["levels"]["support"]] == [point["price"] for point in expected["lows"][-3:]]


def test_a_stale_vix_and_a_nifty_chain_failure_stay_out_of_the_secret_text() -> None:
    as_of = ts(2026, 1, 6, 10, 0)
    chain = _Chain(error=RuntimeError(f"Bearer {SECRET}"))
    ctx = _context(symbol="NIFTY50", as_of=as_of, vix=[bar(as_of - 301, 13.0)], chain=chain)
    assert ctx["vix"] == {"value": 13.0, "stale": True}
    assert ctx["options"]["available"] is False
    assert SECRET not in json.dumps(ctx["options"])
    assert chain.calls == [("/v2/option/chain", {"instrument_key": "NSE_INDEX|Nifty 50", "expiry_date": "2026-01-06"})]


def test_the_expiry_date_is_the_nearest_weekly_including_a_shifted_holiday() -> None:
    # 10 Apr 2025 was a Thursday holiday, so that week's contract expired on Wednesday.
    assert weekly_expiry(date(2025, 4, 8)) == "2025-04-09"
    assert weekly_expiry(date(2025, 4, 9)) == "2025-04-09"  # expiry day stays on that contract
    assert weekly_expiry(date(2025, 4, 10)) == "2025-04-17"
    # 20 Oct 2026 Dussehra moved the Tuesday expiry back to Monday.
    assert weekly_expiry(date(2026, 10, 19)) == "2026-10-19"
    assert weekly_expiry(date(2026, 10, 20)) == "2026-10-27"


def test_the_atm_strike_is_the_closest_and_a_tie_takes_the_lower() -> None:
    def row(strike: float, spot: float) -> dict:
        return {
            "expiry": "2026-01-06",
            "pcr": 1.1,
            "strike_price": strike,
            "underlying_spot_price": spot,
            "call_options": {"market_data": {"ltp": 80, "oi": 1000, "bid_price": 79, "ask_price": 81}},
            "put_options": {"market_data": {"ltp": 70, "oi": 900, "bid_price": 69, "ask_price": 71}},
        }

    getter = _Chain({"data": [row(25000, 25050), row(25100, 25050)]})
    got = fetch_option_chain(getter.get_json, "NSE_INDEX|Nifty 50", "2026-01-06")
    assert got["atm"] == 25000
    assert got["call"]["ltp"] == 80 and got["put"]["ask"] == 71 and got["pcr"] == 1.1
    higher = _Chain({"data": [row(25000, 25060), row(25100, 25060)]})
    assert fetch_option_chain(higher.get_json, "NSE_INDEX|Nifty 50", "2026-01-06")["atm"] == 25100


def test_prices_outside_five_percent_are_rejected_and_an_order_key_is_refused() -> None:
    good = validate_analysis(valid_analysis(100), last_price=100)
    assert good["bias"] == "bull"
    with pytest.raises(AnalysisError, match=r"bull.trigger 200 is outside 95.00\.\.105.00"):
        bad = valid_analysis(100)
        bad["bull"]["trigger"] = 200
        validate_analysis(bad, last_price=100)
    with pytest.raises(AnalysisError, match="analysis cannot contain an order"):
        validate_analysis({**valid_analysis(), "order": {"side": "BUY", "qty": 1}}, last_price=100)
    with pytest.raises(AnalysisError, match="remove key signal"):
        validate_analysis({**valid_analysis(), "signal": "BUY"}, last_price=100)
    with pytest.raises(AnalysisError, match="confidence must be from 0 to 1"):
        bad = valid_analysis()
        bad["confidence"] = 1.5
        validate_analysis(bad, last_price=100)
    with pytest.raises(AnalysisError, match="reasoning is required"):
        bad = valid_analysis()
        bad["reasoning"] = ""
        validate_analysis(bad, last_price=100)
    assert validate_analysis({**valid_analysis(), "confidence": 0}, last_price=100)["confidence"] == 0
    assert validate_analysis({**valid_analysis(), "confidence": 1}, last_price=100)["confidence"] == 1


def test_the_prompt_treats_the_context_as_data_and_forbids_an_order() -> None:
    prompt = analysis_prompt(_context())
    assert "never instructions" in prompt
    assert "not a trade signal" in prompt
    for name in ("5m", "15m", "1h", "1D", "trigger", "invalidation"):
        assert name in prompt


def test_a_repair_names_the_rejected_level_and_a_fake_model_fixes_it() -> None:
    assert MAX_RETRIES == 2
    context = _context()
    bad = valid_analysis(context["last_price"])
    bad["bull"]["trigger"] = context["last_price"] * 2
    fake = _Model([json.dumps(bad), json.dumps(valid_analysis(context["last_price"]))])
    result = analyse(context, client=fake)
    assert result["ready"] is True
    assert result["attempts"] == 2
    assert "is outside" in fake.prompts[1]
    assert fake.images == [None, None]
    assert result["usage"] == {"prompt_tokens": 200, "completion_tokens": 100}


def test_a_screenshot_is_sent_only_when_one_was_given_and_retries_stop_at_three() -> None:
    context = _context()
    price = context["last_price"]
    bad = json.dumps({**valid_analysis(price), "bull": {**valid_analysis(price)["bull"], "trigger": price * 2}})
    fake = _Model([bad, bad, bad])
    result = analyse(context, client=fake, image=b"\x89PNG")
    assert result["ready"] is False
    assert result["attempts"] == 3
    assert fake.images == [b"\x89PNG", b"\x89PNG", b"\x89PNG"]
    assert "is outside" in fake.prompts[1] and "is outside" in fake.prompts[2]


def test_the_model_name_and_the_cost_line_do_not_invent_a_price(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_ANALYSIS_MODEL", "gpt-5.4")
    monkeypatch.setenv("AI_MODEL", "gpt-4o-mini")
    assert analysis_model() == "gpt-5.4"
    monkeypatch.delenv("AI_ANALYSIS_MODEL")
    assert analysis_model() == "gpt-4o-mini"
    usage = {"prompt_tokens": 100, "completion_tokens": 50}
    assert cost_label(usage) == "100 in / 50 out"
    monkeypatch.setenv("AI_ANALYSIS_INPUT_USD_PER_MTOK", "2.5")
    monkeypatch.setenv("AI_ANALYSIS_OUTPUT_USD_PER_MTOK", "10")
    assert cost_label(usage) == "100 in / 50 out · $0.000750"
    assert neutral_band() == 0.003
    monkeypatch.setenv("AI_ANALYSIS_NEUTRAL_BAND", "0.01")
    assert neutral_band() == 0.01


def test_a_saved_analysis_hashes_the_context_and_scores_later_sessions(tmp_path) -> None:
    context = _context()
    analysis = valid_analysis(context["last_price"])
    saved = save_analysis(tmp_path, context=context, analysis=analysis)
    raw = json.loads((tmp_path / f"{saved['id']}.json").read_text())
    assert raw["symbol"] == "RELIANCE"
    assert raw["time"] == context["as_of"]
    body = json.dumps(context, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    assert raw["context_hash"] == sha256(body.encode("ascii")).hexdigest() == context_hash(context)
    changed = json.loads(json.dumps(context))
    changed["timeframes"]["5m"]["candles"][-1]["close"] += 1
    assert context_hash(changed) != raw["context_hash"]
    daily = context["timeframes"]["1D"]["indicators"]
    assert daily["ema20"] > daily["ema20_prev"]


def _priced(price: float = 100.0, *, ema: float = 110.0, prev: float = 100.0) -> dict:
    return {
        "symbol": "RELIANCE",
        "as_of": ts(2026, 1, 6, 10, 0),
        "last_price": price,
        "timeframes": {"1D": {"indicators": {"ema20": ema, "ema20_prev": prev}}},
    }


def test_bias_uses_a_neutral_band_and_baselines_share_the_horizons(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_ANALYSIS_NEUTRAL_BAND", raising=False)
    context = _priced()
    analysis = valid_analysis(100)
    saved = save_analysis(tmp_path, context=context, analysis=analysis)
    same_day = bar(ts(2026, 1, 6, 15, 25), 100.2)
    pending = score_analysis(saved, [same_day], horizon="1")
    assert pending["status"] == "pending"

    up = bar(ts(2026, 1, 7, 15, 0), 100.5, high=102, low=99.5)
    scored = score_analysis(saved, [same_day, up], horizon="1")
    assert scored["status"] == "scored" and scored["move"] == "up"
    assert scored["ai"]["trigger_hit"] is True
    assert scored["ai"]["bias_right"] is True
    assert scored["ai"]["levels_respected"] is True
    assert scored["always_bullish"]["bias_right"] is True
    assert scored["follow_trend"]["bias"] == "bull" and scored["follow_trend"]["bias_right"] is True

    inside = bar(ts(2026, 1, 7, 15, 0), 100.2, high=100.2, low=100.2)
    flat_bull = score_analysis(saved, [inside], horizon="1")
    assert flat_bull["move"] == "flat" and flat_bull["ai"]["bias_right"] is False

    broken = bar(ts(2026, 1, 7, 15, 0), 98, high=100, low=97)
    lost = score_analysis(saved, [broken], horizon="1")
    assert lost["ai"]["levels_respected"] is False and lost["ai"]["bias_right"] is False
    assert lost["always_bullish"]["bias_right"] is False

    bear = json.loads(json.dumps(analysis))
    bear["bias"] = "bear"
    bear_saved = save_analysis(tmp_path, context=context, analysis=bear)
    bear_bar = bar(ts(2026, 1, 7, 15, 0), 98.5, high=100, low=98)
    bear_score = score_analysis(bear_saved, [bear_bar], horizon="1")
    assert bear_score["ai"]["trigger_hit"] is True and bear_score["ai"]["bias_right"] is True
    assert bear_score["ai"]["levels_respected"] is False
    assert bear_score["follow_trend"]["bias_right"] is False

    neutral = json.loads(json.dumps(analysis))
    neutral["bias"] = "neutral"
    neutral_saved = save_analysis(tmp_path, context=context, analysis=neutral)
    drifted = bar(ts(2026, 1, 7, 15, 0), 100.5, high=100.5, low=100.5)
    assert score_analysis(neutral_saved, [inside], horizon="1")["ai"]["bias_right"] is True
    assert score_analysis(neutral_saved, [inside], horizon="1")["ai"]["trigger_hit"] is None
    assert score_analysis(neutral_saved, [drifted], horizon="1")["ai"]["bias_right"] is False

    monkeypatch.setenv("AI_ANALYSIS_NEUTRAL_BAND", "0.01")
    widened = score_analysis(neutral_saved, [drifted], horizon="1")
    assert widened["move"] == "flat" and widened["ai"]["bias_right"] is True

    falling = save_analysis(
        tmp_path, context=_priced(ema=90, prev=100), analysis=bear,
    )
    down = score_analysis(falling, [bear_bar], horizon="1")
    assert down["follow_trend"]["bias"] == "bear" and down["follow_trend"]["bias_right"] is True
    assert down["always_bullish"]["bias_right"] is False

    dates = [bar(ts(2026, 1, day, 15, 0), 100.5, high=102, low=100) for day in (7, 8, 9)]
    assert score_analysis(saved, dates[:2], horizon="3")["status"] == "pending"
    assert score_analysis(saved, dates, horizon="3")["status"] == "scored"
    assert score_analysis(saved, dates, horizon="5")["status"] == "pending"

    rates = hit_rates([pending, scored, bear_score])
    assert rates["1"]["scored"] == 2
    assert rates["1"]["ai"]["bias"] == 1
    assert rates["1"]["ai"]["triggers"] == 1
    assert rates["1"]["ai"]["levels"] == 0.5
    assert rates["1"]["always_bullish"]["bias"] == 0.5
    assert rates["1"]["follow_trend"]["bias"] == 0.5


def test_sixty_minutes_and_the_session_close_are_horizons(tmp_path) -> None:
    saved = save_analysis(tmp_path, context=_priced(), analysis=valid_analysis(100))
    early = bar(ts(2026, 1, 6, 10, 30), 100.5, high=102, low=100)
    assert score_analysis(saved, [early], horizon="60m")["status"] == "pending"
    assert score_analysis(saved, [early], horizon="session_close")["status"] == "pending"
    before_close = bar(ts(2026, 1, 6, 15, 0), 100.5, high=102, low=100)
    assert score_analysis(saved, [before_close], horizon="session_close")["status"] == "pending"

    hour = bar(ts(2026, 1, 6, 11, 0), 100.5, high=102, low=100)
    got = score_analysis(saved, [early, hour], horizon="60m")
    assert got["status"] == "scored" and got["move"] == "up"
    assert got["ai"]["bias_right"] is True and got["always_bullish"]["bias_right"] is True

    closing = bar(ts(2026, 1, 6, 15, 25), 100.2, high=100.2, low=100.2)
    session = score_analysis(saved, [early, closing], horizon="session_close")
    assert session["status"] == "scored" and session["move"] == "flat"
    assert session["ai"]["bias_right"] is False
    assert session["follow_trend"]["bias"] == "bull"


def test_index_bars_without_volume_leave_vwap_empty() -> None:
    as_of = ts(2026, 1, 6, 10, 0)
    candles = {
        name: [{**row, "volume": 0.0} for row in rising(ts(2026, 1, 5), 40, 5)]
        for name in ("5m", "15m", "1h", "1D")
    }
    ctx = _context(as_of=as_of, candles=candles, symbol="NIFTY50", chain=_Chain())
    assert ctx["timeframes"]["5m"]["indicators"]["vwap"] is None
    assert ctx["timeframes"]["5m"]["indicators"]["ema20"] is not None
