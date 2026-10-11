"""End-of-day check at trade level (M1, review 2026-10-11).

Paper and the backtest are matched on the trade: the entry's status (filled or not), the contract, the fill minute,
the exit minute and the exit reason. A paper signal that did not fill is no longer counted as agreement with a
backtest trade on the same bar.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.paper.check import compare_trades

IST = timezone(timedelta(hours=5, minutes=30))
T0915 = int(datetime(2026, 10, 9, 9, 15, tzinfo=IST).timestamp())
STEP = 300


def at(hh: int, mm: int, ss: int = 0) -> int:
    return int(datetime(2026, 10, 9, hh, mm, ss, tzinfo=IST).timestamp())


def row(*, direction: str = "SHORT", bar: int, fill: int, contract: str = "NIFTY 22500 PE 13 OCT 26",
        exit_time: int, reason: str = "reversal", source: str = "live") -> dict[str, Any]:
    return {"direction": direction, "signal_time": bar, "fill_time": fill, "contract": contract,
            "exit_time": exit_time, "exit_reason": reason, "net": 1.0, "source": source}


def signal(*, side: str = "SELL", bar: int, decided: int, status: str = "filled", note: str = "") -> dict[str, Any]:
    return {"time": bar, "side": side, "decided_at_ms": decided * 1000, "status": status, "note": note,
            "source": "live"}


BAR = {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0}


def check(live: list, signals: list, backtest: list, *, incomplete: set[int] | None = None,
          live_bars: dict | None = None, bt_bars: dict | None = None) -> list[dict[str, Any]]:
    bars = {at(9, 20): BAR, at(9, 25): BAR, at(10, 0): BAR}
    return compare_trades(live, signals, backtest, live_bars=bars if live_bars is None else live_bars,
                          bt_bars=bars if bt_bars is None else bt_bars, incomplete=incomplete or set(),
                          step=STEP, basis="decision")


def test_the_same_trades_have_no_differences_even_inside_the_minute() -> None:
    live = [row(bar=at(9, 20), fill=at(9, 26, 0), exit_time=at(15, 15, 0))]  # paper: 09:26:00.137 -> 09:26:00
    bt = [row(bar=at(9, 20), fill=at(9, 26), exit_time=at(15, 15), source="backtest")]
    assert check(live, [signal(bar=at(9, 20), decided=at(9, 26, 0))], bt) == []


def test_a_paper_signal_that_did_not_fill_is_a_difference_when_the_backtest_traded() -> None:
    """The review's case: same bar and side, but paper made nothing."""
    unfilled = signal(bar=at(9, 20), decided=at(9, 26), status="unfilled", note="no quote and no model price")
    bt = [row(bar=at(9, 20), fill=at(9, 26), exit_time=at(15, 15), source="backtest")]
    diffs = check([], [unfilled], bt)
    assert len(diffs) == 1
    d = diffs[0]
    assert (d["time"], d["side"], d["source"], d["fields"]) == ("09:26", "SELL", "backtest", ["status"])
    assert "unfilled" in d["reason"] and "no quote" in d["reason"]


def test_contract_exit_minute_and_exit_reason_are_compared() -> None:
    live = [row(bar=at(9, 20), fill=at(9, 26, 1), contract="NIFTY 22500 PE 13 OCT 26",
                exit_time=at(11, 2, 30), reason="target")]
    bt = [row(bar=at(9, 20), fill=at(9, 26), contract="NIFTY 22550 PE 13 OCT 26", exit_time=at(11, 3),
              reason="stop", source="backtest")]
    diffs = check(live, [signal(bar=at(9, 20), decided=at(9, 26, 1))], bt)
    assert len(diffs) == 1
    assert diffs[0]["source"] == "both"
    assert diffs[0]["fields"] == ["contract", "exit_time", "exit_reason"]
    assert "NIFTY 22550 PE 13 OCT 26" in diffs[0]["reason"] and "11:02" in diffs[0]["reason"]


def test_a_backtest_trade_without_any_paper_signal_gets_the_bar_reason() -> None:
    bt = [row(bar=at(9, 20), fill=at(9, 26), exit_time=at(15, 15), source="backtest")]
    diffs = check([], [], bt, incomplete={at(9, 20)})
    assert [(d["source"], d["fields"], d["reason"]) for d in diffs] == [
        ("backtest", ["status"], "live 5m bar incomplete (feed gap)")]


def test_a_paper_trade_the_backtest_does_not_have_is_a_difference() -> None:
    live = [row(bar=at(10, 0), fill=at(10, 6, 2), exit_time=at(15, 15, 0))]
    diffs = check(live, [signal(bar=at(10, 0), decided=at(10, 6, 2))], [])
    assert [(d["time"], d["source"], d["fields"]) for d in diffs] == [("10:06", "live", ["status"])]


def test_a_skipped_paper_signal_with_no_backtest_trade_agrees() -> None:
    """Already in that position: paper skips it, the backtest broker cancels it. Neither trades."""
    skipped = signal(bar=at(10, 0), decided=at(10, 6), status="skipped", note="already in that position")
    assert check([], [skipped], []) == []


def test_two_trades_in_order_are_matched_one_to_one() -> None:
    live = [row(bar=at(9, 20), fill=at(9, 26, 0), exit_time=at(10, 6, 0)),
            row(direction="LONG", bar=at(10, 0), fill=at(10, 6, 0), contract="NIFTY 22500 CE 13 OCT 26",
                exit_time=at(15, 15, 0), reason="square_off")]
    bt = [row(bar=at(9, 20), fill=at(9, 26), exit_time=at(10, 6), source="backtest"),
          row(direction="LONG", bar=at(10, 0), fill=at(10, 6), contract="NIFTY 22500 CE 13 OCT 26",
              exit_time=at(15, 15), reason="square_off", source="backtest")]
    signals = [signal(bar=at(9, 20), decided=at(9, 26, 0)), signal(side="BUY", bar=at(10, 0), decided=at(10, 6, 0))]
    assert check(live, signals, bt) == []


def test_the_reconcile_captures_the_option_bars_before_the_paper_check(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """The check prices the backtest's premium exits from the stored option bars, so the capture runs first."""
    import asyncio
    from datetime import date

    from app.data.store import CandleStore
    from app.live import service as service_module
    from app.live.service import LiveConfig, LiveService
    from app.options.history import OptionHistoryStore

    cfg = LiveConfig(
        candles_dir=tmp_path / "candles", recordings_dir=tmp_path / "rec", state_dir=tmp_path / "state",
        instruments_dir=tmp_path / "inst", option_history_dir=tmp_path / "options",
    )
    svc = LiveService(cfg, CandleStore(tmp_path / "candles"), client_factory=lambda: object(),
                      authorize=lambda: "wss://example.invalid")

    class Ok:
        ok = True
        key = "NSE_INDEX|Nifty 50"

    order: list[Any] = []
    monkeypatch.setattr(service_module, "reconcile_today", lambda *a, **k: [Ok()])

    async def capture(day: date) -> None:
        order.append("capture")

    monkeypatch.setattr(svc, "capture_options", capture)
    monkeypatch.setattr(svc.paper, "reconcile_check",
                        lambda store, symbol, day, tape=None: order.append(("check", type(tape))))
    svc.engine.day = date(2026, 10, 9)
    asyncio.run(svc.reconcile_now())
    assert order == ["capture", ("check", OptionHistoryStore)]
