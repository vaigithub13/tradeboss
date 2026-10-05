"""LiveEngine: frames in, bars + backfill requests out. Deterministic; no clock, no network.

The engine owns one `CandleBuilder` per instrument and the frame-level rules the builder cannot
know about:

* TRADING DAY comes from the newest frame `currentTs` (the exchange server's clock), converted to
  an IST date and never moving backwards. Never from the local clock, never from bar / tick times
  (a stale snapshot carries last Friday's times and must not roll the day back or forward).
* F6 "future tick" hold: a trade cannot be in the future. A tick whose ltt is more than
  `hold_ahead_ms` (5 s) ahead of the SAME frame's currentTs is held; it is released when a later
  frame's currentTs catches up, otherwise dropped and logged after `hold_max_ms` of currentTs time.
  A real quiet spell (an illiquid option trading again after 25 minutes) is NOT held: its ltt is
  not ahead of currentTs.
* RESUME: a new builder created at/after the open, and every builder after a reconnect, starts a
  gap; the builder asks for a backfill and the engine withholds that instrument's live bars
  (`publishable`) until the backfill is applied (the service releases it after a timeout).
* SESSION END evidence (any of): a tick with ltt >= close for that instrument; a closed
  market_info status for its segment after that segment was seen open today; a frame whose
  currentTs >= close + 30 s. Only on a day we know was a trading day.

`on_frame` is the ONLY entry point for data, so a recording replayed at any speed produces
identical bars.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import date

from app.live.builder import CLOSE_MS, OPEN_MS, BuilderConfig, CandleBuilder, OpenBaseline
from app.live.frames import CLOSED_STATUSES, OPENISH_STATUSES, Frame, FrameError, decode_frame
from app.live.model import Bar, BarEvent, Diff, Tick, fmt_minute, ist_date, ist_ms_of_day

log = logging.getLogger("tradeboss.live.engine")

HOLD_AHEAD_MS = 5_000
HOLD_MAX_MS = 120_000
SESSION_END_GRACE_MS = 30_000
INDEX_SEGMENTS = {"NSE_INDEX", "BSE_INDEX", "MCX_INDEX"}


@dataclass(frozen=True)
class EngineConfig:
    open_volume_baseline: OpenBaseline = "pre_open_inclusive"
    hold_ahead_ms: int = HOLD_AHEAD_MS
    hold_max_ms: int = HOLD_MAX_MS
    open_ms_of_day: int = OPEN_MS
    close_ms_of_day: int = CLOSE_MS


@dataclass(frozen=True)
class BackfillRequest:
    key: str
    day: date
    first_minute: int
    last_minute: int  # inclusive


@dataclass
class _Held:
    key: str
    tick: Tick
    since_ts: int  # currentTs of the frame it was first held in
    has_i1: bool = False


@dataclass
class Latency:
    n: int = 0
    total_ms: int = 0
    min_ms: int | None = None
    max_ms: int | None = None

    def add(self, ms: int) -> None:
        self.n += 1
        self.total_ms += ms
        self.min_ms = ms if self.min_ms is None else min(self.min_ms, ms)
        self.max_ms = ms if self.max_ms is None else max(self.max_ms, ms)

    def summary(self) -> dict:
        return {"n": self.n, "mean_ms": None if not self.n else round(self.total_ms / self.n),
                "min_ms": self.min_ms, "max_ms": self.max_ms}


def segment_of(key: str) -> str:
    return key.split("|", 1)[0]


class LiveEngine:
    def __init__(self, cfg: EngineConfig | None = None) -> None:
        self.cfg = cfg or EngineConfig()
        self.day: date | None = None
        self.current_ts = 0
        self.builders: dict[str, CandleBuilder] = {}
        self.segments: dict[str, str] = {}
        self.seen_open: set[str] = set()  # segments seen open-ish today
        self.counters: Counter[str] = Counter()
        self.latency = Latency()
        self.held: list[_Held] = []
        self.held_dropped: list[dict] = []
        self.frames = 0
        self._events: list[BarEvent] = []
        self._requested: dict[str, tuple[int, int]] = {}
        self._withheld: set[str] = set()
        self._reconnected = False
        self._finished: list[tuple[date, str]] = []
        self._ended: set[str] = set()
        self._diffs: list[Diff] = []
        self._new_requests: list[BackfillRequest] = []
        self._trading = False
        self.last_live_wall_ms: int | None = None  # receive time of the newest live_feed frame (badge)

    # ------------------------------------------------------------------ input
    def on_disconnect(self) -> None:
        """The feed connection dropped. After the next frame arrives every builder starts a gap."""
        self._reconnected = True

    def on_frame(self, raw: bytes, recv_wall_ms: int | None = None, frame_idx: int | None = None) -> None:
        try:
            frame = decode_frame(raw)
        except FrameError as exc:
            self.counters["bad_frame"] += 1
            log.warning("undecodable feed frame: %s", exc)
            return
        self.frames += 1
        idx = self.frames if frame_idx is None else frame_idx
        if frame.current_ts <= 0:
            self.counters["no_current_ts"] += 1
            return
        if recv_wall_ms is not None:
            self.latency.add(recv_wall_ms - frame.current_ts)  # logging only
        self._update_day(frame.current_ts)
        self.current_ts = max(self.current_ts, frame.current_ts)
        if self.day is None:
            return
        if self._reconnected:
            self._reconnected = False
            if ist_ms_of_day(frame.current_ts) >= self.cfg.open_ms_of_day and ist_date(frame.current_ts) == self.day:
                for b in self.builders.values():
                    if not b.ended:
                        b.begin_resume()
        if frame.kind == "market_info":
            self._on_market_info(frame)
        else:
            if frame.kind == "live_feed" and recv_wall_ms is not None:
                self.last_live_wall_ms = recv_wall_ms
            self._on_feed(frame, idx)
        self._after_frame(frame)

    # ------------------------------------------------------------------ trading day
    def _update_day(self, current_ts: int) -> None:
        d = ist_date(current_ts)
        if self.day is None:
            self.day = d
            return
        if d > self.day:
            self._roll_day(d)

    def _roll_day(self, new_day: date) -> None:
        log.info("trading day %s -> %s (from the feed's currentTs)", self.day, new_day)
        self.end_day()
        for h in self.held:
            self.held_dropped.append(self._held_record(h, "day_rolled"))
        self.held.clear()
        self.builders = {}
        self.seen_open = set()
        self.segments = {}
        self._requested = {}
        self._withheld = set()
        self._ended = set()
        self._trading = False
        self.day = new_day

    # ------------------------------------------------------------------ frames
    def _on_market_info(self, frame: Frame) -> None:
        for seg, status in frame.segments.items():
            self.segments[seg] = status
            if status in OPENISH_STATUSES:
                if ist_date(frame.current_ts) == self.day:
                    self.seen_open.add(seg)
            elif status in CLOSED_STATUSES and seg in self.seen_open:
                for key, b in self.builders.items():
                    if segment_of(key) == seg and not b.ended:
                        self._end_builder(key, b, "market_info_closed")

    def _builder(self, key: str, frame: Frame) -> CandleBuilder:
        b = self.builders.get(key)
        if b is None:
            seg = segment_of(key)
            b = CandleBuilder(
                key,
                self.day,
                BuilderConfig(
                    open_ms_of_day=self.cfg.open_ms_of_day,
                    close_ms_of_day=self.cfg.close_ms_of_day,
                    has_volume=seg not in INDEX_SEGMENTS,
                    has_oi="_FO" in seg,
                    open_volume_baseline=self.cfg.open_volume_baseline,
                ),
            )
            if ist_ms_of_day(frame.current_ts) >= self.cfg.open_ms_of_day:
                b.begin_resume()  # cold start mid-session: everything since the open is a gap
            self.builders[key] = b
        return b

    def _on_feed(self, frame: Frame, idx: int) -> None:
        self._release_held(frame.current_ts)
        snapshot = frame.kind == "initial_feed"
        for item in frame.items:
            b = self._builder(item.key, frame)
            if b.ended:
                self.counters["after_end"] += 1
                continue
            held = False
            if item.ltt > 0 and item.ltp > 0:
                tick = Tick(item.ltt, item.ltp, item.ltq, item.vtt if item.has_volume else None, item.oi, snapshot)
                if item.ltt > frame.current_ts + self.cfg.hold_ahead_ms:
                    self.held.append(_Held(item.key, tick, frame.current_ts))
                    self.counters["held"] += 1
                    held = True
                else:
                    b.on_tick(tick)
            if item.i1 is not None:
                b.on_i1(item.i1, ref_ltt=None if held or item.ltt <= 0 else item.ltt, frame=idx)

    def _release_held(self, current_ts: int) -> None:
        if not self.held:
            return
        keep: list[_Held] = []
        for h in self.held:
            b = self.builders.get(h.key)
            if b is None or b.ended:
                continue
            if h.tick.ltt <= current_ts + self.cfg.hold_ahead_ms:
                self.counters["released"] += 1
                b.on_tick(h.tick)
            elif current_ts - h.since_ts > self.cfg.hold_max_ms:
                self.counters["held_dropped"] += 1
                rec = self._held_record(h, "never_caught_up")
                self.held_dropped.append(rec)
                log.warning("dropped future-dated tick %s", rec)
            else:
                keep.append(h)
        self.held = keep

    def _held_record(self, h: _Held, why: str) -> dict:
        return {"key": h.key, "ltt": h.tick.ltt, "ltp": h.tick.ltp, "held_at_current_ts": h.since_ts,
                "ahead_ms": h.tick.ltt - h.since_ts, "reason": why}

    # ------------------------------------------------------------------ after every frame
    def _after_frame(self, frame: Frame) -> None:
        trading = self.trading_today()
        end_by_ts = (
            trading
            and ist_date(frame.current_ts) == self.day
            and ist_ms_of_day(frame.current_ts) >= self.cfg.close_ms_of_day + SESSION_END_GRACE_MS
        )
        for key, b in self.builders.items():
            if b.ended:
                continue
            if b.saw_post_close_tick:
                self._end_builder(key, b, "post_close_tick")
            elif end_by_ts:
                self._end_builder(key, b, "current_ts")
        self._collect()

    def trading_today(self) -> bool:
        """Is there evidence that today is a trading day (real trades, or an open segment)?"""
        if not self._trading:
            self._trading = bool(self.seen_open) or any(
                b.saw_post_close_tick or b.last_ltt is not None for b in self.builders.values()
            )
        return self._trading

    def _end_builder(self, key: str, b: CandleBuilder, why: str) -> None:
        if not self.trading_today():
            return
        self.counters[f"session_end_{why}"] += 1
        b.on_session_end()
        self._ended.add(key)
        self._finished.append((self.day, key))  # type: ignore[arg-type]

    def end_day(self) -> None:
        """Force the end of the session for every instrument (scheduled reconcile / day roll)."""
        for key, b in self.builders.items():
            if not b.ended:
                self._end_builder(key, b, "forced")
        self._collect()

    def _collect(self) -> None:
        for key, b in self.builders.items():
            evs = b.take_events()
            self._diffs.extend(b.take_diffs())
            want = b.wanted_backfill()
            if want is None:
                self._requested.pop(key, None)
                self._withheld.discard(key)
            else:
                if self._requested.get(key) != want:
                    self._requested[key] = want
                    self._new_requests.append(BackfillRequest(key, self.day, want[0], want[1]))  # type: ignore[arg-type]
                if self.trading_today():
                    self._withheld.add(key)
            if key not in self._withheld:
                self._events.extend(evs)

    # ------------------------------------------------------------------ outputs
    def take_backfill_requests(self) -> list[BackfillRequest]:
        out, self._new_requests = self._new_requests, []
        return out

    def outstanding_backfills(self) -> list[BackfillRequest]:
        """Everything still missing (for retries); the service rate-limits the calls."""
        return [BackfillRequest(k, self.day, a, z) for k, (a, z) in sorted(self._requested.items())]  # type: ignore[arg-type]

    def take_events(self) -> list[BarEvent]:
        out, self._events = self._events, []
        return out

    def take_diffs(self) -> list[Diff]:
        out, self._diffs = self._diffs, []
        return out

    def take_finished(self) -> list[tuple[date, str]]:
        """(day, key) pairs whose session just ended: persist them."""
        out, self._finished = self._finished, []
        return out

    def publishable(self, key: str) -> bool:
        return key not in self._withheld

    def release(self, key: str) -> None:
        """Stop withholding `key` (the backfill timed out): live bars flow, the gap stays marked."""
        if key in self._withheld:
            self._withheld.discard(key)
            b = self.builders.get(key)
            if b is not None:
                self._events.extend(BarEvent(key, x) for x in b.bars()[-2:])
            self.counters["withheld_released"] += 1

    def bars(self, key: str, *, include_withheld: bool = False) -> list[Bar]:
        b = self.builders.get(key)
        if b is None or (key in self._withheld and not include_withheld):
            return []
        return b.bars()

    def apply_backfill(self, key: str, bars: list[Bar]) -> list[Diff]:
        b = self.builders.get(key)
        if b is None:
            return []
        diffs = b.apply_backfill(bars)
        self._collect()
        if b.wanted_backfill() is None and key in self._withheld:
            self._withheld.discard(key)
        if key not in self._withheld:
            self._events.extend(BarEvent(key, x) for x in b.bars()[-2:])
        self._diffs.extend(diffs)
        return diffs

    def apply_official(self, key: str, bars: list[Bar]) -> list[Diff]:
        b = self.builders.get(key)
        if b is None:
            return []
        diffs = b.apply_official(bars)
        self._collect()
        return diffs

    def keys(self) -> list[str]:
        return sorted(self.builders)

    def summary(self) -> dict:
        return {
            "day": None if self.day is None else self.day.isoformat(),
            "frames": self.frames,
            "counters": dict(self.counters),
            "latency_ms": self.latency.summary(),
            "held_dropped": list(self.held_dropped),
            "instruments": {
                k: {"bars": len(b.bars()), "counters": dict(b.counters), "gap": len(b.gap_minutes()),
                    "ended": b.ended, "withheld": k in self._withheld}
                for k, b in sorted(self.builders.items())
            },
        }

    def minute_records(self) -> list[dict]:
        """One side-by-side record per instrument-minute: tick bar | I1 bar | official bar."""
        out: list[dict] = []
        for key, b in sorted(self.builders.items()):
            for m in b.minutes_with_records():
                rec = b.minute_record(m)
                rec["time"] = fmt_minute(m)
                rec["tick_vs_i1"] = _cmp(rec["tick"], rec["i1"])
                rec["i1_vs_official"] = _cmp(rec["i1"], rec["official"])
                rec["tick_vs_official"] = _cmp(rec["tick"], rec["official"])
                out.append(rec)
        return out

    def daily_summary(self) -> dict:
        """Counts that answer the open questions about I1 and the 09:15 volume baseline."""
        verdict: Counter[str] = Counter()
        open_vol: dict[str, dict] = {}
        for key, b in sorted(self.builders.items()):
            for m in b.minutes_with_records():
                i1 = b.minute_record(m)["i1"]
                if i1 is None or i1["seen_while_ltt_minute_offset"] is None:
                    continue
                off = i1["seen_while_ltt_minute_offset"]
                verdict["i1_is_forming_bar" if off == 0 else "i1_is_last_completed_bar" if off == 1 else f"other_offset_{off}"] += 1
            cand = b.open_volume_candidates()
            if cand is not None:
                o = b.minute_record(b.open_minute)["official"]
                open_vol[key] = {**cand, "official": None if o is None else o["volume"]}
        return {"day": None if self.day is None else self.day.isoformat(), "i1_timing": dict(verdict),
                "open_bar_volume": open_vol, **self.summary()}


def _cmp(a: dict | None, b: dict | None) -> dict | None:
    if a is None or b is None:
        return None
    out = {}
    for f in ("open", "high", "low", "close", "volume"):
        x, y = a.get(f), b.get(f)
        if x is None or y is None:
            if (x is None) != (y is None):
                out[f] = {"a": x, "b": y}
            continue
        if abs(x - y) > (0.5 if f == "volume" else 0.005):
            out[f] = {"a": x, "b": y}
    return out

