"""Backend log file: data/logs/backend-YYYY-MM-DD.log (IST date), next to the terminal output.

Everything the app logs (root logger) and uvicorn's own lines (`uvicorn`, `uvicorn.access`, which do not
propagate to the root) go to the file. Uncaught exceptions, also in threads, are logged there, and
`faulthandler` writes a Python traceback to the same file on a hard crash (segfault, abort).
A SIGKILL leaves no trace anywhere; the last lines before it are still in the file.
"""

from __future__ import annotations

import faulthandler
import logging
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import IO

IST = timezone(timedelta(hours=5, minutes=30))
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_UVICORN = ("uvicorn", "uvicorn.access")


class DailyFileHandler(logging.Handler):
    """Appends each record to backend-<IST date of the record>.log in `directory`."""

    def __init__(self, directory: Path) -> None:
        super().__init__()
        self.directory = Path(directory)
        self._day: str | None = None
        self._fh: IO[str] | None = None

    def _file_for(self, created: float) -> IO[str]:
        day = datetime.fromtimestamp(created, IST).date().isoformat()
        if day != self._day or self._fh is None:
            if self._fh is not None:
                self._fh.close()
            self.directory.mkdir(parents=True, exist_ok=True)
            self._fh = (self.directory / f"backend-{day}.log").open("a", encoding="utf-8")
            self._day = day
        return self._fh

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            with self.lock or threading.RLock():
                fh = self._file_for(record.created)
                fh.write(msg + "\n")
                fh.flush()
        except Exception:  # noqa: BLE001 - logging must never take the app down
            self.handleError(record)

    def current_stream(self) -> IO[str]:
        """The open file for now (faulthandler writes a crash trace into it)."""
        return self._file_for(datetime.now(IST).timestamp())

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        super().close()


def configure_logging(directory: Path, *, crash_trace: bool = True) -> DailyFileHandler:
    """Attach the file handler (replacing one from an earlier call) and a terminal handler for app records."""
    handler = DailyFileHandler(directory)
    handler.setFormatter(logging.Formatter(FORMAT))
    root = logging.getLogger()
    for lg in (root, *(logging.getLogger(n) for n in _UVICORN)):
        for old in [h for h in lg.handlers if isinstance(h, DailyFileHandler)]:
            lg.removeHandler(old)
            old.close()
        if lg is root or not lg.propagate:  # uvicorn's loggers stop at themselves; otherwise the root has it
            lg.addHandler(handler)
    if not any(getattr(h, "_tradeboss_terminal", False) for h in root.handlers):
        terminal = logging.StreamHandler(sys.stderr)
        terminal.setFormatter(logging.Formatter(FORMAT))
        terminal._tradeboss_terminal = True  # type: ignore[attr-defined]
        root.addHandler(terminal)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)

    log = logging.getLogger("app.crash")

    def excepthook(exc_type, exc, tb) -> None:  # noqa: ANN001
        log.critical("uncaught exception", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    def thread_excepthook(args: threading.ExceptHookArgs) -> None:
        log.critical("uncaught exception in thread %s", getattr(args.thread, "name", "?"),
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))  # type: ignore[arg-type]

    sys.excepthook = excepthook
    threading.excepthook = thread_excepthook
    if crash_trace:
        faulthandler.enable(handler.current_stream(), all_threads=True)
    return handler
