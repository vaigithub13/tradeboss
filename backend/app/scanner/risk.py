"""PASS or BLOCK for one plan. Missing event data is a warning, not a block."""

from __future__ import annotations

from app.scanner.bars import ENTRY_CUTOFF_MIN, minute_of
from app.scanner.config import ScannerSettings
from app.scanner.models import Plan

CHECK_NEWS = "check news"


def assess(
    plan: Plan,
    *,
    verdict: str | None,
    settings: ScannerSettings,
    daily_loss: float,
    trades_today: int,
    open_symbols: set[str],
    event_today: bool | None = None,
) -> dict[str, object]:
    """`event_today` True blocks. None means we have no calendar, so the row says check news and still passes that check."""
    reasons: list[str] = []
    if plan.status == "waiting":
        return {"decision": "BLOCK", "reasons": ["still waiting for a trigger"], "news": CHECK_NEWS}
    if plan.entry is None or plan.stop is None or plan.target is None:
        reasons.append("plan is incomplete")
    if plan.qty < 1:
        reasons.append("quantity under 1")
    if plan.rupee_risk is not None and plan.rupee_risk - settings.max_loss_per_trade > 1e-6:
        reasons.append("risk above max loss per trade")
    if plan.rr is not None and plan.rr + 1e-9 < settings.min_rr:
        reasons.append("reward:risk below 1:2")
    if daily_loss <= -settings.daily_loss_limit:
        reasons.append("daily loss limit reached")
    if trades_today >= settings.max_trades_per_day:
        reasons.append("max trades per day reached")
    if minute_of(plan.entry_time) >= ENTRY_CUTOFF_MIN:
        reasons.append("entry at or after 14:30")
    if plan.capital is not None and plan.capital - settings.max_capital_per_trade > 1e-6:
        reasons.append("capital above max capital per trade")
    if plan.symbol in open_symbols:
        reasons.append("same stock already in a trade")
    if verdict != "GOOD":
        reasons.append(f"time machine {verdict or 'missing'}")
    news = CHECK_NEWS
    if event_today is True:
        reasons.append("results or major event today")
        news = "event today"
    elif event_today is False:
        news = "no event on file"
    return {"decision": "PASS" if not reasons else "BLOCK", "reasons": reasons, "news": news}
