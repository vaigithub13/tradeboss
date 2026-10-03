"""Raw feed recorder and replayer.

File: `data/feed-recordings/YYYY-MM-DD.bin` (IST date of the receive time)

    header   b"TBFEED1\\n"
    records  struct ">BBQI" = direction (0 received, 1 sent), kind (0 binary, 1 text),
             recv_wall_ms, payload length ; then the payload bytes

Recording gate (K7): only between `window_start` and `window_end` IST (default 09:00-16:05) AND
only when the feed's own market_info says a segment is open-ish (holidays are therefore not
recorded, special sessions are). A non-snapshot in-session trade is accepted as evidence too (in
case a market_info frame was missed). Once active, the recorder keeps going to the end of the
window. When it activates it first writes the last market_info frame it buffered, and any
subscription frames that were sent before.

The wall clock here only decides file names and the gate; bars never use it.
"""

from __future__ import annotations

import logging
import struct
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, time as dtime
from pathlib import Path
from typing import BinaryIO

from app.live.frames import OPENISH_STATUSES, FrameError, decode_frame
from app.live.model import IST, ist_ms_of_day

log = logging.getLogger("tradeboss.live.recorder")

MAGIC = b"TBFEED1\n"
_REC = struct.Struct(">BBQI")
MAX_PAYLOAD = 16 * 1024 * 1024
RECV, SENT = 0, 1
BINARY, TEXT = 0, 1


class RecordingError(RuntimeError):
    pass


@dataclass(frozen=True)
class Record:
    direction: int
    kind: int
    wall_ms: int
    payload: bytes


def recording_path(directory: Path, day: date) -> Path:
    return directory / f"{day.isoformat()}.bin"


# ---------------------------------------------------------------- reading
def iter_records(path: Path) -> Iterator[Record]:
    """Yield the records of a recording. A truncated tail (crash mid-write) stops cleanly; a corrupt
    header / length raises RecordingError."""
    with path.open("rb") as fh:
        head = fh.read(len(MAGIC))
        if head != MAGIC:
            raise RecordingError(f"{path.name}: not a TradeBoss feed recording")
        while True:
            raw = fh.read(_REC.size)
            if len(raw) < _REC.size:
                return
            direction, kind, wall_ms, n = _REC.unpack(raw)
            if direction not in (RECV, SENT) or kind not in (BINARY, TEXT) or n > MAX_PAYLOAD:
                raise RecordingError(f"{path.name}: corrupt record header at byte {fh.tell() - _REC.size}")
            payload = fh.read(n)
            if len(payload) < n:
                return
            yield Record(direction, kind, wall_ms, payload)


def replay(
    path: Path,
    on_frame: Callable[[bytes, int, int], None],
    *,
    speed: float = 0.0,
    sleeper: Callable[[float], None] = time.sleep,
    after_frame: Callable[[], None] | None = None,
) -> int:
    """Feed the received frames of a recording to `on_frame(raw, wall_ms, index)`.

    `speed` 1/10/100 spaces the frames by the recorded wall-clock gaps divided by `speed`;
    0 means as fast as possible. `sleeper` is injectable so tests never wait. Returns the number
    of frames replayed."""
    n = 0
    prev: int | None = None
    for rec in iter_records(path):
        if rec.direction != RECV or rec.kind != BINARY:
            continue
        if speed > 0 and prev is not None and rec.wall_ms > prev:
            sleeper((rec.wall_ms - prev) / 1000.0 / speed)
        prev = rec.wall_ms
        n += 1
        on_frame(rec.payload, rec.wall_ms, n)
        if after_frame is not None:
            after_frame()
    return n


# ---------------------------------------------------------------- writing
def _parse_hhmm(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


class Recorder:
    def __init__(self, directory: Path, *, window_start: str = "09:00", window_end: str = "16:05") -> None:
        self.dir = directory
        self.start = _parse_hhmm(window_start)
        self.end = _parse_hhmm(window_end)
        self._fh: BinaryIO | None = None
        self._day: date | None = None
        self._active = False
        self._status_open = False
        self._last_info: Record | None = None
        self._pre_sent: list[Record] = []
        self.written = 0
        self.skipped = 0

    @property
    def active(self) -> bool:
        return self._active

    def write(self, direction: int, kind: int, payload: bytes, wall_ms: int) -> bool:
        """Offer one message. Returns True when it was written to disk."""
        when = datetime.fromtimestamp(wall_ms / 1000, IST)
        day = when.date()
        if self._day != day:
            self._rollover(day)
        rec = Record(direction, kind, wall_ms, payload)
        in_window = self.start <= when.time() < self.end
        if not in_window:
            if self._active:
                self.close()
                self._active = False
            if direction == RECV and kind == BINARY:
                self._watch(rec, when)  # keep the last market_info for when the window opens
            self.skipped += 1
            return False
        if not self._active:
            if direction == SENT:
                self._pre_sent = (self._pre_sent + [rec])[-50:]
                self.skipped += 1
                return False
            self._watch(rec, when)
            if not (self._status_open or self._is_evidence(rec, when)):
                self.skipped += 1
                return False
            self._activate(day, rec)
        self._append(rec)
        if direction == RECV and kind == BINARY:
            self._watch(rec, when)
        return True

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    # -------------------------------------------------------------- internals
    def _rollover(self, day: date) -> None:
        self.close()
        self._day = day
        self._active = False
        self._status_open = False
        self._last_info = None
        self._pre_sent = []

    def _watch(self, rec: Record, when: datetime) -> None:  # noqa: ARG002
        try:
            f = decode_frame(rec.payload)
        except FrameError:
            return
        if f.kind == "market_info":
            self._last_info = rec
            self._status_open = any(s in OPENISH_STATUSES for s in f.segments.values())

    def _is_evidence(self, rec: Record, when: datetime) -> bool:  # noqa: ARG002
        if rec.direction != RECV or rec.kind != BINARY:
            return False
        try:
            f = decode_frame(rec.payload)
        except FrameError:
            return False
        if f.kind != "live_feed":
            return False
        return any(
            it.ltt > 0 and datetime.fromtimestamp(it.ltt / 1000, IST).date() == when.date()
            and 9 * 3_600_000 + 15 * 60_000 <= ist_ms_of_day(it.ltt) < 15 * 3_600_000 + 30 * 60_000
            for it in f.items
        )

    def _activate(self, day: date, current: Record) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = recording_path(self.dir, day)
        new = not path.exists() or path.stat().st_size == 0
        self._fh = path.open("ab")
        if new:
            self._fh.write(MAGIC)
        self._active = True
        log.info("feed recording %s (market open per market_info)", path)
        for rec in self._pre_sent:
            self._append(rec)
        self._pre_sent = []
        if self._last_info is not None and self._last_info is not current:
            self._append(self._last_info)
        self._last_info = None

    def _append(self, rec: Record) -> None:
        assert self._fh is not None
        self._fh.write(_REC.pack(rec.direction, rec.kind, rec.wall_ms, len(rec.payload)) + rec.payload)
        self._fh.flush()
        self.written += 1
