"""The model price used when a paper fill has no fresh quote: the option model the drawings and the
backtest overlay already use (options/position.py `priced`), priced at the index level and the VIX
of that moment. VIX comes from the feed (or, in a replay, from the same recorded VIX ticks).
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable

from app.options.contract import OptionContract
from app.options.model import OptionModelConfig, load_option_model
from app.options.position import priced

# The backtest's slippage label for option premiums. Applied against us on modelled fills only:
# a buy pays this much more and a sell receives this much less than the model price.
MODEL_SLIPPAGE_POINTS = 0.5

ModelPrice = Callable[[OptionContract, float, int], float | None]


class VixSeries:
    """VIX closes seen so far, by the unix second the bar starts. A later copy of the same bar replaces it."""

    def __init__(self) -> None:
        self._times: list[int] = []
        self._closes: list[float] = []

    def add(self, when: int, close: float) -> None:
        if self._times and when < self._times[-1]:
            return  # an older bar than the newest we hold: the feed has moved on
        if self._times and when == self._times[-1]:
            self._closes[-1] = float(close)
            return
        self._times.append(int(when))
        self._closes.append(float(close))

    def at(self, when: int) -> float | None:
        """The last VIX close that started at or before `when`; None before the first one."""
        i = bisect_right(self._times, when) - 1
        return None if i < 0 else self._closes[i]


def model_price_for(vix_at: Callable[[int], float | None], cfg: OptionModelConfig | None = None) -> ModelPrice:
    """(contract, index spot, time in ms) -> model premium, or None when no VIX is known yet."""
    model = cfg or load_option_model()

    def price(contract: OptionContract, spot: float, ts_ms: int) -> float | None:
        when = ts_ms // 1000
        vix = vix_at(when)
        if vix is None:
            return None
        return priced(contract.kind, spot, contract.strike, when, contract.expiry, vix, model)

    return price
