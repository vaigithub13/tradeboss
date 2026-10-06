"""Live 1-minute candle builder for ONE instrument and ONE trading day (critical module).

Spec: tests/test_live_builder.py (and tests/test_live_engine.py for the frame-level rules).

Pure and deterministic: it is driven only by events carrying EXCHANGE timestamps (tick `ltt`,
I1 `ts`). It never reads a clock (a test patches every clock to raise), so replaying a
recording at any speed, or in any batching, gives the same bars.

Sources of a bar, lowest to highest rank (a higher source replaces a lower one, every change of
a value is returned as a `Diff` so nothing is silent):

    filled    flat bar for a minute without trades while we were connected (official history
              does the same: flat at the previous close, volume 0)
    tick      built from last-trade ticks
    i1        the feed's exchange 1-minute OHLC entry, once final (see below)
    backfill  completed minutes fetched from the intraday API after a connection gap
    official  the intraday candles at 15:45, then the historical candles the next morning

I1 is final when a LATER I1 timestamp has been seen, or when the session has ended. That rule
works whether I1 is the forming bar or the last completed bar (the docs do not say).

Volume of a tick-built bar = (highest `vtt` in the minute) - (baseline), where the baseline is the
highest `vtt` of the previous minute that had ticks. After a connection gap the first minute's
volume is unknown (None) until I1 / backfill arrives. For the first bar of the day the baseline
is `pre_open_inclusive` (0, so the 09:15 bar includes pre-open volume; default since 5 Oct 2026)
or `first_tick` (first accepted tick: vtt - ltq, so pre-open volume is not counted).
"""

from __future__ import annotations

import bisect
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from app.live.model import (
    FINAL_RANK,
    MS_MIN,
    SOURCE_RANK,
    Bar,
    BarEvent,
    Diff,
    I1Bar,
    Tick,
    day_open_minute,
    diff_fields,
    ist_date,
    ist_ms_of_day,
    minute_of,
)

OpenBaseline = Literal["first_tick", "pre_open_inclusive"]

OPEN_MS = 9 * 3_600_000 + 15 * MS_MIN
CLOSE_MS = 15 * 3_600_000 + 30 * MS_MIN


@dataclass(frozen=True)
class BuilderConfig:
    open_ms_of_day: int = OPEN_MS
    close_ms_of_day: int = CLOSE_MS  # exclusive
    has_volume: bool = True  # False for indices (volume is 0 and always "known")
    has_oi: bool = False
    open_volume_baseline: OpenBaseline = "pre_open_inclusive"


@dataclass
class _Minute:
    """Everything the ticks of one minute say."""

    o: float = 0.0
    h: float = 0.0
    l: float = 0.0  # noqa: E741
    c: float = 0.0
    o_key: tuple[int, int] = (0, 0)  # (ltt, arrival seq) of the tick that opened the bar
    c_key: tuple[int, int] = (0, 0)
    n: int = 0
    first_ltt: int = 0
    last_ltt: int = 0
    vtt_hi: int | None = None
    oi: float | None = None
    oi_ltt: int = -1
    seen: set[tuple] = field(default_factory=set)
    late_after_final: int = 0


@dataclass
class I1Obs:
    """What we saw of the feed's I1 entry for one minute (the evidence the minute log reports)."""

    minute: int
    bar: I1Bar
    first_ref_minute: int | None  # minute of the same message's last-trade time
    first_frame: int | None
    last_frame: int | None
    n_updates: int = 1
    final_how: str | None = None  # "next_i1" | "session_end"


class CandleBuilder:
    def __init__(self, key: str, day, cfg: BuilderConfig | None = None) -> None:  # noqa: ANN001
        self.key = key
        self.day = day  # IST trading date (given by the engine; never taken from bar times)
        self.cfg = cfg or BuilderConfig()
        self.open_minute = day_open_minute(day, self.cfg.open_ms_of_day)
        self.close_minute = day_open_minute(day, self.cfg.close_ms_of_day)  # exclusive
        self.counters: Counter[str] = Counter()
        self._ticks: dict[int, _Minute] = {}
        self._bars: dict[int, Bar] = {}
        self._events: list[BarEvent] = []
        self._diffs: list[Diff] = []
        self._seq = 0
        self._vtt_points: list[tuple[int, int]] = []  # sorted (ltt, vtt) of accepted ticks
        self._earliest: tuple[int, int, int | None, int] | None = None  # (ltt, seq, vtt, ltq)
        self._open_base: float | None = None
        self._breaks: set[int] = set()
        self._gap: set[int] = set()
        self._partial: set[int] = set()
        self._resume_pending = False
        self._wanted: tuple[int, int] | None = None  # backfill request (first, last minute)
        self._max_minute: int | None = None
        self._i1_latest: I1Obs | None = None
        self._i1_obs: dict[int, I1Obs] = {}
        self._i1_revisions: list[dict] = []
        self._official: dict[int, Bar] = {}
        self.ended = False
        self.saw_post_close_tick = False
        self.last_ltt: int | None = None  # newest accepted exchange time

    # ------------------------------------------------------------------ public: inputs
    def begin_resume(self) -> None:
        """The data stream was interrupted (or started mid-session). The next accepted tick
        starts a gap: minutes in between are NOT flat-filled, they are requested via backfill."""
        self._resume_pending = True

    def on_tick(self, t: Tick) -> bool:
        """Feed one last-trade tick. Returns True when it was used."""
        if not (math.isfinite(t.ltp) and t.ltp > 0):
            self.counters["bad_price"] += 1
            return False
        if ist_date(t.ltt) != self.day:
            self.counters["stale_snapshot" if t.snapshot else "not_trading_day"] += 1
            return False
        md = ist_ms_of_day(t.ltt)
        if md < self.cfg.open_ms_of_day:
            self.counters["pre_open"] += 1
            return False
        if md >= self.cfg.close_ms_of_day:
            self.counters["post_close"] += 1
            self.saw_post_close_tick = True
            return False
        minute = minute_of(t.ltt)
        if minute in self._gap:
            self.counters["before_resume"] += 1  # the backfill owns this minute
            return False
        if self._resume_pending:
            self._start_resume(minute)
        existing = self._bars.get(minute)
        if existing is not None and SOURCE_RANK[existing.source] >= FINAL_RANK:
            self.counters["late_after_final"] += 1
            mm = self._ticks.get(minute)
            if mm is not None:
                mm.late_after_final += 1
            return False

        mm = self._ticks.get(minute)
        if mm is None:
            mm = self._ticks[minute] = _Minute()
        sig = (t.ltt, t.ltp, t.ltq, t.vtt)
        if sig in mm.seen:
            self.counters["duplicate"] += 1
            return False
        mm.seen.add(sig)
        self._seq += 1
        key = (t.ltt, self._seq)
        if mm.n == 0:
            mm.o = mm.h = mm.l = mm.c = t.ltp
            mm.o_key = mm.c_key = key
            mm.first_ltt = mm.last_ltt = t.ltt
        else:
            if key < mm.o_key:
                mm.o, mm.o_key = t.ltp, key
            if key > mm.c_key:
                mm.c, mm.c_key = t.ltp, key
            mm.h = max(mm.h, t.ltp)
            mm.l = min(mm.l, t.ltp)
            mm.first_ltt = min(mm.first_ltt, t.ltt)
            mm.last_ltt = max(mm.last_ltt, t.ltt)
        mm.n += 1
        if t.oi is not None and t.ltt >= mm.oi_ltt:
            mm.oi, mm.oi_ltt = t.oi, t.ltt
        self._take_vtt(mm, t, key)
        self.last_ltt = t.ltt if self.last_ltt is None else max(self.last_ltt, t.ltt)

        if self._max_minute is not None and minute > self._max_minute:
            self._fill(self._max_minute + 1, minute)
        self._max_minute = minute if self._max_minute is None else max(self._max_minute, minute)
        self._refresh(minute)
        # a late tick can change the baseline (and so the volume) of the next minute
        if (minute + 1) in self._ticks:
            self._refresh(minute + 1)
        return True

    def on_i1(
        self, i1: I1Bar, *, ref_ltt: int | None = None, frame: int | None = None
    ) -> None:
        """Feed the feed's exchange 1-minute entry."""
        vals = (i1.open, i1.high, i1.low, i1.close)
        ok = (
            i1.ts % MS_MIN == 0
            and all(math.isfinite(v) and v > 0 for v in vals)
            and i1.high >= max(i1.open, i1.close, i1.low)
            and i1.low <= min(i1.open, i1.close, i1.high)
        )
        if not ok:
            self.counters["i1_malformed"] += 1
            return
        if ist_date(i1.ts) != self.day:
            self.counters["i1_not_trading_day"] += 1
            return
        md = ist_ms_of_day(i1.ts)
        if md >= self.cfg.close_ms_of_day:
            self.counters["i1_post_close"] += 1  # e.g. Nifty 15:30 flat bar, future 15:39
            return
        if md < self.cfg.open_ms_of_day:
            self.counters["i1_pre_open"] += 1
            return
        minute = i1.ts // MS_MIN
        latest = self._i1_latest
        if latest is not None and minute < latest.minute:
            self.counters["i1_stale"] += 1
            old = self._i1_obs.get(minute)
            if old is not None and old.final_how and old.bar != i1:
                self._i1_revisions.append({"minute": minute, "kept": old.bar, "ignored": i1})
                self.counters["i1_revision"] += 1
            return
        if latest is not None and minute == latest.minute:
            if latest.bar != i1:
                latest.bar = i1
                latest.n_updates += 1
            latest.last_frame = frame
            return
        if latest is not None:
            self._finalize_i1(latest, "next_i1")
        obs = I1Obs(
            minute=minute,
            bar=i1,
            first_ref_minute=None if ref_ltt is None else minute_of(ref_ltt),
            first_frame=frame,
            last_frame=frame,
        )
        self._i1_latest = obs
        self._i1_obs[minute] = obs

    def on_session_end(self) -> None:
        """Session-end evidence arrived: finalise the last I1, flat-fill quiet minutes up to the
        close (only when no connection gap is unresolved)."""
        if self.ended:
            return
        self.ended = True
        if self._i1_latest is not None and self._i1_latest.final_how is None:
            self._finalize_i1(self._i1_latest, "session_end")
        # The session's last minute usually has no I1 of its own (the feed's last I1 is the minute before).
        # Once the session has ended no trade can follow it, so its complete tick bar is final now.
        last = self.close_minute - 1
        tick_bar = self._bars.get(last)
        if (tick_bar is not None and tick_bar.source == "tick" and not tick_bar.partial
                and last not in self._gap and last not in self._partial):
            self._set_bar(Bar(last, tick_bar.open, tick_bar.high, tick_bar.low, tick_bar.close,
                              tick_bar.volume, tick_bar.oi, "session_end"))
        if self._resume_pending:
            # we were disconnected and never saw a tick again: everything after the last bar is a gap
            self._start_resume(self.close_minute, no_break=True)
        elif self._max_minute is not None:
            self._fill(self._max_minute + 1, self.close_minute)

    def apply_backfill(self, bars: list[Bar]) -> list[Diff]:
        """Completed minutes fetched from the intraday API for the gap. Never replaces a bar that
        is already i1-final / backfill / official."""
        diffs: list[Diff] = []
        for b in bars:
            m = b.minute
            if not (self.open_minute <= m < self.close_minute):
                self.counters["backfill_out_of_session"] += 1
                continue
            if m not in self._gap and m not in self._partial:
                self.counters["backfill_not_wanted"] += 1
                continue
            old = self._bars.get(m)
            if old is not None and SOURCE_RANK[old.source] >= FINAL_RANK:
                self.counters["backfill_kept_final"] += 1
                self._gap.discard(m)
                self._partial.discard(m)
                continue
            nb = Bar(m, b.open, b.high, b.low, b.close, b.volume if self.cfg.has_volume else 0.0, b.oi, "backfill")
            d = self._set_bar(nb)
            if d is not None:
                diffs.append(d)
            self._gap.discard(m)
            self._partial.discard(m)
            self._max_minute = m if self._max_minute is None else max(self._max_minute, m)
        self._diffs.extend(diffs)
        self._refresh_wanted()
        return diffs

    def apply_official(self, bars: list[Bar]) -> list[Diff]:
        """Reconcile with the historical API: the official bars replace everything; minutes we
        have that official lacks are removed (logged). An EMPTY official list changes nothing."""
        official = {b.minute: b for b in bars if self.open_minute <= b.minute < self.close_minute}
        if not official:
            self.counters["official_empty"] += 1
            return []
        diffs: list[Diff] = []
        for m in sorted(official):
            ob = official[m]
            nb = Bar(m, ob.open, ob.high, ob.low, ob.close, ob.volume if self.cfg.has_volume else 0.0,
                     ob.oi if self.cfg.has_oi else None, "official")
            old = self._bars.get(m)
            self._official[m] = nb
            if old is None:
                diffs.append(Diff(self.key, m, "added", None, "official"))
            else:
                f = diff_fields(old, nb)
                if f or old.volume is None:
                    diffs.append(Diff(self.key, m, "changed", old.source, "official", f))
            self._set_bar(nb)
            self._gap.discard(m)
            self._partial.discard(m)
        lo, hi = min(official), max(official)
        for m in sorted(x for x in self._bars if x not in official and lo <= x <= hi):
            old = self._bars.pop(m)  # inside the official span but official has no such bar
            diffs.append(Diff(self.key, m, "removed", old.source, "official"))
        self._gap.clear()
        self._partial.clear()
        self._wanted = None
        self._diffs.extend(diffs)
        return diffs

    # ------------------------------------------------------------------ public: outputs
    def bars(self) -> list[Bar]:
        return [self._bars[m] for m in sorted(self._bars)]

    def bar(self, minute: int) -> Bar | None:
        return self._bars.get(minute)

    @property
    def forming_minute(self) -> int | None:
        return self._max_minute

    def take_events(self) -> list[BarEvent]:
        ev, self._events = self._events, []
        return ev

    def take_diffs(self) -> list[Diff]:
        d, self._diffs = self._diffs, []
        return d

    def gap_minutes(self) -> list[int]:
        return sorted(self._gap | self._partial)

    def wanted_backfill(self) -> tuple[int, int] | None:
        """(first, last) minute still missing from the intraday API, or None."""
        return self._wanted

    @property
    def revisions(self) -> list[dict]:
        return list(self._i1_revisions)

    def minute_record(self, minute: int) -> dict:
        """Side-by-side view of one minute for the recorder's minute log."""
        mm = self._ticks.get(minute)
        tick = None
        if mm is not None:
            vol = self._tick_volume(minute)
            tick = {
                "open": mm.o, "high": mm.h, "low": mm.l, "close": mm.c,
                "volume": vol, "n_ticks": mm.n,
                "first_ltt": mm.first_ltt, "last_ltt": mm.last_ltt,
                "oi": mm.oi, "late_after_final": mm.late_after_final,
                "partial": minute in self._partial,
            }
        obs = self._i1_obs.get(minute)
        i1 = None
        if obs is not None:
            i1 = {
                "open": obs.bar.open, "high": obs.bar.high, "low": obs.bar.low, "close": obs.bar.close,
                "volume": obs.bar.vol, "n_updates": obs.n_updates, "final_how": obs.final_how,
                "first_frame": obs.first_frame, "last_frame": obs.last_frame,
                # the minute of the trade time in the message where this I1 first appeared:
                #   == minute -> I1 is the FORMING bar;  == minute + 1 -> it is the last COMPLETED bar
                "seen_while_ltt_minute_offset": None if obs.first_ref_minute is None else obs.first_ref_minute - minute,
            }
        off = self._official.get(minute)
        official = None if off is None else {
            "open": off.open, "high": off.high, "low": off.low, "close": off.close, "volume": off.volume, "oi": off.oi,
        }
        eff = self._bars.get(minute)
        return {
            "key": self.key, "minute": minute, "tick": tick, "i1": i1, "official": official,
            "effective_source": None if eff is None else eff.source,
        }

    def minutes_with_records(self) -> list[int]:
        return sorted(set(self._ticks) | set(self._i1_obs) | set(self._official) | set(self._bars))

    def open_volume_candidates(self) -> dict[str, float | None] | None:
        """Both candidates for the 09:15 bar's volume (the log reports both whatever the setting)."""
        mm = self._ticks.get(self.open_minute)
        if mm is None or mm.vtt_hi is None or self.open_minute in self._breaks or self._earliest is None:
            return None
        _, _, vtt0, ltq0 = self._earliest
        if vtt0 is None:
            return None
        return {"first_tick": float(mm.vtt_hi - (vtt0 - ltq0)), "pre_open_inclusive": float(mm.vtt_hi)}

    # ------------------------------------------------------------------ internals
    def _take_vtt(self, mm: _Minute, t: Tick, key: tuple[int, int]) -> None:
        if self._earliest is None or key < (self._earliest[0], self._earliest[1]):
            self._earliest = (t.ltt, key[1], t.vtt, t.ltq)
        v = t.vtt
        if v is None:
            return
        if v == 0 and self._vtt_points and self._vtt_points[-1][1] > 0:
            self.counters["vtt_missing"] += 1
            return
        i = bisect.bisect_right(self._vtt_points, (t.ltt, 1 << 62))
        lo = self._vtt_points[i - 1][1] if i > 0 else None
        hi = self._vtt_points[i][1] if i < len(self._vtt_points) else None
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            self.counters["vtt_regress"] += 1
            return
        self._vtt_points.insert(i, (t.ltt, v))
        mm.vtt_hi = v if mm.vtt_hi is None else max(mm.vtt_hi, v)
        self._open_base = None  # recomputed lazily from the earliest tick

    def _baseline(self, minute: int) -> float | None:
        prev = None
        for p in self._ticks:
            if p < minute and self._ticks[p].vtt_hi is not None and (prev is None or p > prev):
                prev = p
        if prev is not None:
            if any(prev < b <= minute for b in self._breaks):
                return None
            return float(self._ticks[prev].vtt_hi)  # type: ignore[arg-type]
        if minute in self._breaks or self._earliest is None:
            return None
        ltt0, _, vtt0, ltq0 = self._earliest
        if minute_of(ltt0) != minute:
            return None  # earlier minutes had no usable vtt
        if self.cfg.open_volume_baseline == "pre_open_inclusive":
            return 0.0
        return None if vtt0 is None else float(max(vtt0 - ltq0, 0))

    def _tick_volume(self, minute: int) -> float | None:
        if not self.cfg.has_volume:
            return 0.0
        mm = self._ticks.get(minute)
        if mm is None or mm.vtt_hi is None:
            return None
        base = self._baseline(minute)
        if base is None:
            return None
        return float(max(mm.vtt_hi - base, 0.0))

    def _start_resume(self, minute: int, *, no_break: bool = False) -> None:
        """First tick (minute M) after an interruption: define what the backfill must supply."""
        self._resume_pending = False
        last = self._max_minute
        if last is None:
            if minute > self.open_minute:
                self._gap.update(m for m in range(self.open_minute, minute) if m not in self._bars)
        elif last < minute:
            self._partial.add(last)
            self._gap.update(m for m in range(last + 1, minute) if m not in self._bars)
        # else: resumed inside the same minute: the cumulative vtt keeps the volume right
        if not no_break and (last is None or last < minute):
            self._breaks.add(minute)
        if last is not None and last < minute:
            b = self._bars.get(last)
            if b is not None and not b.final:
                self._bars[last] = Bar(last, b.open, b.high, b.low, b.close, None, b.oi, b.source, partial=True)
                self._events.append(BarEvent(self.key, self._bars[last]))
        self._refresh_wanted()

    def _refresh_wanted(self) -> None:
        pending = sorted(self._gap | self._partial)
        self._wanted = (pending[0], pending[-1]) if pending else None

    def _fill(self, first: int, upto: int) -> None:
        """Flat bars for quiet minutes first..upto-1 (connected, so no trades happened)."""
        for m in range(first, upto):
            if m < self.open_minute or m >= self.close_minute or m in self._gap or m in self._bars:
                continue
            prev = [x for x in self._bars if x < m]
            if not prev:
                continue  # nothing is filled before the day's first bar
            pc = self._bars[max(prev)].close
            self._set_bar(Bar(m, pc, pc, pc, pc, 0.0, None, "filled"))

    def _refresh(self, minute: int) -> None:
        mm = self._ticks.get(minute)
        if mm is None:
            return
        old = self._bars.get(minute)
        if old is not None and SOURCE_RANK[old.source] >= FINAL_RANK:
            return
        # the first minute after a resume lacks the trades before we connected -> partial, volume unknown
        partial = minute in self._partial or minute in self._breaks
        vol = None if partial else self._tick_volume(minute)
        if not self.cfg.has_volume:
            vol = 0.0
        self._set_bar(
            Bar(minute, mm.o, mm.h, mm.l, mm.c, vol, mm.oi if self.cfg.has_oi else None, "tick", partial=partial)
        )

    def _set_bar(self, b: Bar) -> Diff | None:
        old = self._bars.get(b.minute)
        if old is not None and old.values() == b.values() and old.source == b.source and old.partial == b.partial:
            return None
        self._bars[b.minute] = b
        self._events.append(BarEvent(self.key, b))
        if old is not None and SOURCE_RANK[b.source] > SOURCE_RANK[old.source]:
            f = diff_fields(old, b)
            if f or (old.volume is None) != (b.volume is None):
                return Diff(self.key, b.minute, "changed", old.source, b.source, f)
        return None

    def _finalize_i1(self, obs: I1Obs, how: str) -> None:
        obs.final_how = how
        m = obs.minute
        i1 = obs.bar
        nb = Bar(m, i1.open, i1.high, i1.low, i1.close, float(i1.vol) if self.cfg.has_volume else 0.0, None, "i1")
        old = self._bars.get(m)
        if old is not None and SOURCE_RANK[old.source] >= FINAL_RANK:
            if diff_fields(old, nb):
                self._i1_revisions.append({"minute": m, "kept": old, "ignored": nb})
                self.counters["i1_revision"] += 1
            return
        if m in self._ticks and old is not None and old.oi is not None:
            nb = Bar(m, nb.open, nb.high, nb.low, nb.close, nb.volume, old.oi, "i1")
        d = self._set_bar(nb)
        if d is not None:
            self._diffs.append(d)
        self._gap.discard(m)
        self._partial.discard(m)
        self._max_minute = m if self._max_minute is None else max(self._max_minute, m)
        self._refresh_wanted()
