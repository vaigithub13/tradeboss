"""Bull/bear flag: a 1–3 bar impulse, a tight 3–8 bar pause, then a close through the flag."""

from __future__ import annotations

from app.scanner.bars import Bar, closed_bars, entry_allowed, last_swing, nearest_level, prior_avg_volume, wilder_atr
from app.scanner.models import Hit


def find_flags(bars: list[Bar], *, as_of: int | None = None) -> list[Hit]:
    bars = closed_bars(bars, as_of)
    atrs = wilder_atr(bars, 14)
    hits: list[Hit] = []
    seen: set[int] = set()
    for i in range(20, len(bars)):
        if int(bars[i]["time"]) in seen:
            continue
        if not entry_allowed(int(bars[i]["time"])):
            continue
        found = _match(bars, atrs, i)
        if found is None:
            continue
        seen.add(found.time)
        hits.append(found)
    return hits


def _match(bars: list[Bar], atrs: list[float | None], i: int) -> Hit | None:
    avg = prior_avg_volume(bars, i, 20)
    if avg is None or avg <= 0 or float(bars[i]["volume"]) < 1.2 * avg:
        return None
    for cons_len in range(3, 9):
        cons_end = i - 1
        cons_start = cons_end - cons_len + 1
        if cons_start < 1:
            continue
        for imp_len in range(1, 4):
            imp_end = cons_start - 1
            imp_start = imp_end - imp_len + 1
            if imp_start < 0:
                continue
            atr = atrs[imp_end]
            if atr is None or atr <= 0:
                continue
            impulse = bars[imp_start : imp_end + 1]
            cons = bars[cons_start : cons_end + 1]
            imp_hi = max(float(b["high"]) for b in impulse)
            imp_lo = min(float(b["low"]) for b in impulse)
            imp_range = imp_hi - imp_lo
            if imp_range < 1.5 * atr:
                continue
            if float(impulse[-1]["close"]) > float(impulse[0]["open"]):
                side = "long"
            elif float(impulse[-1]["close"]) < float(impulse[0]["open"]):
                side = "short"
            else:
                continue
            cons_hi = max(float(b["high"]) for b in cons)
            cons_lo = min(float(b["low"]) for b in cons)
            cons_range = cons_hi - cons_lo
            if cons_range > 0.5 * imp_range:
                continue
            slope_room = 0.25 * cons_range
            if side == "long" and float(cons[-1]["close"]) > float(cons[0]["close"]) + slope_room:
                continue
            if side == "short" and float(cons[-1]["close"]) < float(cons[0]["close"]) - slope_room:
                continue
            close = float(bars[i]["close"])
            if side == "long" and close <= cons_hi:
                continue
            if side == "short" and close >= cons_lo:
                continue
            stop = cons_lo if side == "long" else cons_hi
            return Hit(
                setup="flag",
                side=side,  # type: ignore[arg-type]
                time=int(bars[i]["time"]),
                close=close,
                stop=stop,
                atr=atrs[i],
                swing=last_swing(bars, i, kind="low" if side == "long" else "high"),
                volume_ratio=float(bars[i]["volume"]) / avg,
                resistance=nearest_level(bars, i, close, side="long"),
                support=nearest_level(bars, i, close, side="short"),
            )
    return None
