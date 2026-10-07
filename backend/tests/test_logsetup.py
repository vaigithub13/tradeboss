"""The backend's log goes to data/logs/backend-YYYY-MM-DD.log (IST date) as well as the terminal."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.logsetup import DailyFileHandler, configure_logging

IST = timezone(timedelta(hours=5, minutes=30))


def test_records_go_to_the_file_of_their_ist_date(tmp_path) -> None:
    h = DailyFileHandler(tmp_path)
    h.setFormatter(logging.Formatter("%(message)s"))
    late = datetime(2026, 10, 7, 23, 59, tzinfo=IST).timestamp()
    early = datetime(2026, 10, 8, 0, 1, tzinfo=IST).timestamp()
    for ts, msg in ((late, "before midnight IST"), (early, "after midnight IST")):
        rec = logging.LogRecord("t", logging.INFO, __file__, 1, msg, None, None)
        rec.created = ts
        h.emit(rec)
    h.close()
    assert (tmp_path / "backend-2026-10-07.log").read_text() == "before midnight IST\n"
    assert (tmp_path / "backend-2026-10-08.log").read_text() == "after midnight IST\n"


def test_configure_logging_writes_app_and_uvicorn_records_once(tmp_path) -> None:
    configure_logging(tmp_path, crash_trace=False)
    configure_logging(tmp_path, crash_trace=False)  # a second call (reload, tests) does not add a second handler
    logging.getLogger("tradeboss.live").info("app line")
    logging.getLogger("uvicorn.error").info("uvicorn line")
    logging.getLogger("uvicorn.access").info("access line")
    for h in logging.getLogger().handlers + logging.getLogger("uvicorn").handlers:
        h.flush()
    text = "".join(p.read_text() for p in tmp_path.glob("backend-*.log"))
    assert text.count("app line") == 1 and text.count("uvicorn line") == 1 and text.count("access line") == 1
    for lg in (logging.getLogger(), logging.getLogger("uvicorn"), logging.getLogger("uvicorn.access")):
        for h in [h for h in lg.handlers if isinstance(h, DailyFileHandler) or getattr(h, "_tradeboss_terminal", False)]:
            lg.removeHandler(h)
