"""The model price used when a paper fill has no fresh quote: the option model the drawings and the
backtest overlay already use (options/position.py `priced`), priced at the index level and VIX of that moment."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

from app.data.store import CandleStore
from app.options.contract import OptionContract
from app.options.model import OptionModelConfig, load_option_model
from app.options.position import priced

VIX_SYMBOL = "NSE_INDEX_India_VIX"
IST = timezone(timedelta(hours=5, minutes=30))
ModelPrice = Callable[[OptionContract, float, int], float]


def model_price_for(vix_at: Callable[[int], float], cfg: OptionModelConfig | None = None) -> ModelPrice:
    """(contract, index spot, time in ms) -> model premium. `vix_at(unix seconds)` gives the VIX of that moment."""
    model = cfg or load_option_model()

    def price(contract: OptionContract, spot: float, ts_ms: int) -> float:
        when = ts_ms // 1000
        return priced(contract.kind, spot, contract.strike, when, contract.expiry, vix_at(when), model)

    return price


def vix_from_store(store: CandleStore, day: date) -> Callable[[int], float]:
    """VIX closes from the stored candles for the day: the last close at or before a time."""
    start = int(datetime(day.year, day.month, day.day, tzinfo=IST).timestamp())
    rows, _ = store.load(VIX_SYMBOL, from_time=start, to_time=start + 86_400, session_types=("normal", "weekend_full"))
    times = [int(r["time"]) for r in rows]
    closes = [float(r["close"]) for r in rows]

    def at(when: int) -> float:
        i = bisect_right(times, when) - 1
        if i < 0:
            raise LookupError(f"no VIX close at or before {when} on {day.isoformat()}")
        return closes[i]

    return at
