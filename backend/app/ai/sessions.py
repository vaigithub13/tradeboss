"""Which dates are NSE sessions, for the auto-analyse timer.

The timer itself lives in the frontend. This is the calendar it is given:
weekday holidays from ``nse_holidays.json``, the known full weekend sessions
(budget days and special Saturdays), and Muhurat dates. Muhurat days are also
holidays, because there is no regular session; they run only when market_info
says the market is open.
"""

from __future__ import annotations

from app.backtest.expiry import DEFAULT_HOLIDAYS_FILE, HolidayCalendar
from app.data.sessions import MUHURAT_DATES
from app.options.events import WEEKEND_TRADING_SESSIONS


def session_calendar() -> dict[str, list[str]]:
    holidays = HolidayCalendar.from_json(DEFAULT_HOLIDAYS_FILE).holidays
    return {
        "holidays": sorted(d.isoformat() for d in holidays),
        "weekend_sessions": sorted(d.isoformat() for d in WEEKEND_TRADING_SESSIONS),
        "muhurat": sorted(d.isoformat() for d in MUHURAT_DATES),
    }
