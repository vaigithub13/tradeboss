"""Queries run in a separate process, not on the live-feed loop.

The pool is created on the first request. Tests call ``answer_candles`` and
``answer_days`` directly and never start this pool.
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

_pool: ProcessPoolExecutor | None = None


def pool() -> ProcessPoolExecutor:
    global _pool
    if _pool is None:
        _pool = ProcessPoolExecutor(max_workers=1)
    return _pool


def candles_job(spec: dict) -> dict:
    from app.data.store import CandleStore
    from app.similar.service import answer_candles

    return answer_candles(
        CandleStore(Path(spec["candles"])),
        Path(spec["cache"]),
        symbol=spec["symbol"],
        timeframe=spec["timeframe"],
        window=int(spec["window"]),
        as_of=spec.get("as_of"),
        method=spec.get("method") or "euclidean",
    )


def days_job(spec: dict) -> dict:
    from app.data.store import CandleStore
    from app.similar.service import answer_days

    return answer_days(
        CandleStore(Path(spec["candles"])),
        Path(spec["cache"]),
        symbol=spec["symbol"],
        at=spec.get("at") or "10:15",
        as_of=spec.get("as_of"),
    )
