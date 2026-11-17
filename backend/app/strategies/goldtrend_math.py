"""EA indicator maths from AurumBoss research/goldtrend6.py + goldtrend_book.py (read-only port).

RMA uses a running-average seed for the first `p` values, then Wilder. EMA is seeded with the
first value. Supertrend matches the EA's CalcST. Used by the GOLD TREND research strategies.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

from app.strategies.daily_levels import IST_OFFSET_S, minute_of_day, session_day

DAY_S = 86_400
SESSION_OPEN_MIN = 9 * 60 + 15
SESSION_CLOSE_MIN = 15 * 60 + 30


class RMA:
    """EA CalcRMA: running average for the first p values, then Wilder."""

    def __init__(self, p: int) -> None:
        self.p, self.n, self.s, self.v = p, 0, 0.0, None

    def update(self, x: float) -> float:
        if self.n < self.p:
            self.s += x
            self.n += 1
            self.v = self.s / self.n
        else:
            assert self.v is not None
            self.v = self.v + (x - self.v) / self.p
        return self.v


class ATRx:
    def __init__(self, p: int = 14) -> None:
        self.r, self.pc = RMA(p), None

    def update(self, h: float, l: float, c: float) -> float:
        tr = h - l if self.pc is None else max(h, self.pc) - min(l, self.pc)
        self.pc = c
        return self.r.update(tr)


class EMAx:
    """EA CalcEMA: seeded with the first value."""

    def __init__(self, p: int) -> None:
        self.k, self.v = 2.0 / (p + 1), None

    def update(self, x: float) -> float:
        self.v = x if self.v is None else x * self.k + self.v * (1 - self.k)
        return self.v


class SupertrendEA:
    """EA CalcST(period, mult) direction (+1 up / -1 down)."""

    def __init__(self, per: int = 10, mult: float = 3.0) -> None:
        self.atr, self.mult = ATRx(per), float(mult)
        self.up = self.dn = None
        self.d: int | None = None
        self.pc: float | None = None

    def update(self, h: float, l: float, c: float) -> int:
        a = self.atr.update(h, l, c)
        hl = (h + l) / 2
        u, w = hl - self.mult * a, hl + self.mult * a
        if self.d is None:
            d = 1
        else:
            assert self.up is not None and self.dn is not None and self.pc is not None
            if self.pc > self.up:
                u = max(u, self.up)
            if self.pc < self.dn:
                w = min(w, self.dn)
            if self.d == -1 and c > self.dn:
                d = 1
            elif self.d == 1 and c < self.up:
                d = -1
            else:
                d = self.d
        self.up, self.dn, self.d, self.pc = u, w, d, c
        return d


class RSIx:
    """EA CalcRSI: RMA of gains/losses, first change = 0."""

    def __init__(self, p: int = 14) -> None:
        self.g, self.l, self.pc, self.v = RMA(p), RMA(p), None, None

    def update(self, c: float) -> float:
        x = 0.0 if self.pc is None else c - self.pc
        self.pc = c
        ag, al = self.g.update(max(x, 0.0)), self.l.update(max(-x, 0.0))
        self.v = 100.0 if al == 0 else 100.0 - 100.0 / (1 + ag / al)
        return self.v


class MACDx:
    def __init__(self, f: int = 12, s: int = 26, sig: int = 9) -> None:
        self.f, self.s, self.sig = EMAx(f), EMAx(s), EMAx(sig)

    def update(self, c: float) -> tuple[float, float]:
        m = self.f.update(c) - self.s.update(c)
        return m, self.sig.update(m)


class Ichimoku:
    """Tenkan/Kijun/cloud; spans A/B are the values computed `shift` bars ago."""

    def __init__(self, t: int = 9, k: int = 26, b: int = 52, shift: int = 26) -> None:
        self.t, self.k, self.b, self.shift = t, k, b, shift
        self.hl: deque[tuple[float, float]] = deque(maxlen=b)
        self.spans: deque[tuple[float, float]] = deque(maxlen=shift + 1)

    @staticmethod
    def _mid(xs: list[tuple[float, float]]) -> float:
        return (max(h for h, _ in xs) + min(l for _, l in xs)) / 2

    def update(self, h: float, l: float) -> tuple[float, float, float | None, float | None]:
        self.hl.append((h, l))
        xs = list(self.hl)
        ten = self._mid(xs[-self.t :])
        kij = self._mid(xs[-self.k :])
        sb = self._mid(xs[-self.b :])
        self.spans.append(((ten + kij) / 2, sb))
        if len(self.spans) <= self.shift:
            return ten, kij, None, None
        a, b = self.spans[0]
        return ten, kij, max(a, b), min(a, b)


@dataclass
class HtBar:
    time: int
    minutes: int
    open: float
    high: float
    low: float
    close: float


class SessionResampler:
    """Build N-minute bars from closed source bars, anchored to 09:15 IST (no fabricated gaps)."""

    def __init__(self, minutes: int, source_minutes: int = 5) -> None:
        if minutes < 1 or source_minutes < 1 or minutes % source_minutes != 0:
            raise ValueError(f"need minutes % source_minutes == 0, got {minutes}/{source_minutes}")
        self.minutes = minutes
        self.source_minutes = source_minutes
        self.current: HtBar | None = None

    def reset(self) -> None:
        self.current = None

    def on_bar(self, bar: dict[str, Any]) -> list[HtBar]:
        t = int(bar["time"])
        minute = minute_of_day(t)
        if minute < SESSION_OPEN_MIN or minute >= SESSION_CLOSE_MIN:
            return []
        day = session_day(t)
        k = (minute - SESSION_OPEN_MIN) // self.minutes
        start_min = SESSION_OPEN_MIN + k * self.minutes
        start_t = day * DAY_S - IST_OFFSET_S + start_min * 60
        done: list[HtBar] = []
        if self.current is not None and self.current.time != start_t:
            done.append(self.current)
            self.current = None
        o, h, l, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])
        if self.current is None:
            self.current = HtBar(start_t, self.minutes, o, h, l, c)
        else:
            self.current.high = max(self.current.high, h)
            self.current.low = min(self.current.low, l)
            self.current.close = c
        end_min = start_min + self.minutes
        # Last source bar of the bucket, or last bar that still fits before session close.
        if minute + self.source_minutes >= end_min or minute + self.source_minutes >= SESSION_CLOSE_MIN:
            done.append(self.current)
            self.current = None
        return done
