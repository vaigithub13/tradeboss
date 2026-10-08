"""Feeding a paper session from a recorded feed: the same path the live service takes, one frame at a time.

Used to catch a session up to now (a Start in the middle of the day, or a restart), and by the replay tests.
A frame's depth quotes reach the session before the bars the frame closes, as they do live.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.live.engine import EngineConfig, LiveEngine
from app.live.model import BarEvent
from app.live.recorder import replay
from app.live.spreads.decode import depth_quotes
from app.paper.session import PaperSession
from app.upstox.instruments import NIFTY_INDEX_KEY, VIX_KEY

#: exchange-final minutes only (and the intraday API's completed minutes fetched for a feed gap): the paper
#: strategy never sees a tick-built minute
PAPER_BAR_SOURCES = ("i1", "session_end", "official", "backfill")


class _Stop(Exception):
    pass


def bar_minute(ev: BarEvent) -> dict[str, Any]:
    b = ev.bar
    return {"time": b.time_s, "open": b.open, "high": b.high, "low": b.low, "close": b.close,
            "volume": b.volume or 0, "source": b.source}


def replay_into(
    session: PaperSession,
    path: Path,
    *,
    on_vix: Callable[[int, float], None] | None = None,
    open_volume_baseline: str = "pre_open_inclusive",
    until_ms: int | None = None,
) -> None:
    """Run the recording through a fresh live engine into `session`. With `until_ms`, stop before the first
    frame that is past that exchange time."""
    engine = LiveEngine(EngineConfig(open_volume_baseline=open_volume_baseline))  # type: ignore[arg-type]

    def on_frame(raw: bytes, wall: int, idx: int) -> None:
        if until_ms is not None and engine.current_ts >= until_ms:
            raise _Stop
        engine.on_frame(raw, wall, idx)
        session.on_depth(depth_quotes(raw))
        for ev in engine.take_events():
            if ev.key == VIX_KEY and on_vix is not None:
                on_vix(ev.bar.time_s, ev.bar.close)
            elif ev.key == NIFTY_INDEX_KEY and ev.bar.source in PAPER_BAR_SOURCES:
                session.on_index_minute(bar_minute(ev), now_ms=engine.current_ts)
        if engine.current_ts > 0:
            if NIFTY_INDEX_KEY in engine.ticked:
                session.on_index_tick(engine.ticked[NIFTY_INDEX_KEY], now_ms=engine.current_ts)
            session.on_clock(engine.current_ts)

    try:
        replay(path, on_frame)
    except _Stop:
        pass
