"""Nifty expiry calendar: which dates weekly / monthly options expire on, by date (Phase 3b input).

Rules (with their circulars: data/expiry_rules.json; holidays: data/nse_holidays.json):

* a rule says "expiries are on WEEKDAY": weekly = that weekday of each week, monthly (also
  quarterly / half-yearly) = the LAST such weekday of the month;
* rules are dated by expiry: a rule applies to an expiry whose nominal date, computed with that
  rule, is on/after `first_expiry_on_or_after` (NSE: contracts expiring on/after 1 Sep 2025 are on
  Tuesdays, those on/before 31 Aug 2025 stay on Thursdays);
* if the nominal day is not a trading day, the expiry moves to the PREVIOUS trading day (never the
  next one). A Muhurat-only day is not a trading day for this purpose (Diwali 2025: the Tuesday
  21 Oct weekly expired on Monday 20 Oct).

The calendar is a pure function of (rules, trading-day predicate); it never looks at today's date.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date as Date
from datetime import timedelta
from pathlib import Path
from typing import Any

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
KINDS = ("weekly", "monthly")


class ExpiryConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ExpiryRule:
    weekday: int  # 0 = Monday
    first_expiry_on_or_after: Date  # Date.min = since forever
    source: str = ""


@dataclass(frozen=True)
class Expiry:
    date: Date
    kind: str  # "weekly" | "monthly" (monthly also covers quarterly / half-yearly)
    nominal: Date  # the day the rule names; differs from `date` when a holiday shifted it
    weekday_rule: str  # name of the weekday the rule in force used (Thursday / Tuesday)

    @property
    def shifted(self) -> bool:
        return self.date != self.nominal


def rules_from_dict(data: dict[str, Any]) -> list[ExpiryRule]:
    rules: list[ExpiryRule] = []
    for raw in data.get("rules", []):
        name = str(raw.get("weekday"))
        if name not in WEEKDAYS:
            raise ExpiryConfigError(f"weekday {name!r}: expected one of {list(WEEKDAYS)}")
        first = raw.get("first_expiry_on_or_after")
        try:
            d = Date.min if first is None else Date.fromisoformat(str(first))
        except ValueError as exc:
            raise ExpiryConfigError(f"first_expiry_on_or_after {first!r} is not YYYY-MM-DD") from exc
        rules.append(ExpiryRule(WEEKDAYS.index(name), d, str(raw.get("source", ""))))
    if not rules:
        raise ExpiryConfigError("no expiry rules")
    rules.sort(key=lambda r: r.first_expiry_on_or_after)
    if rules[0].first_expiry_on_or_after != Date.min:
        raise ExpiryConfigError("the first rule must hold from the beginning (first_expiry_on_or_after: null)")
    if len({r.first_expiry_on_or_after for r in rules}) != len(rules):
        raise ExpiryConfigError("two rules start on the same date")
    return rules


def load_rules(path: Path) -> list[ExpiryRule]:
    return rules_from_dict(json.loads(path.read_text()))


class HolidayCalendar:
    """Trading day = Monday-Friday that is not in `holidays` (weekend sessions never matter here)."""

    def __init__(self, holidays: Iterable[Date]) -> None:
        self.holidays = frozenset(holidays)

    @classmethod
    def from_json(cls, path: Path) -> HolidayCalendar:
        return cls(Date.fromisoformat(x) for x in json.loads(path.read_text())["holidays"])

    def __call__(self, d: Date) -> bool:
        return d.weekday() < 5 and d not in self.holidays


DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_RULES_FILE = DATA_DIR / "expiry_rules.json"
DEFAULT_HOLIDAYS_FILE = DATA_DIR / "nse_holidays.json"


def load_default_calendar() -> ExpiryCalendar:
    """The shipped Nifty calendar: `data/expiry_rules.json` + `data/nse_holidays.json`."""
    return ExpiryCalendar(load_rules(DEFAULT_RULES_FILE), HolidayCalendar.from_json(DEFAULT_HOLIDAYS_FILE))


def _last_weekday_of_month(year: int, month: int, weekday: int) -> Date:
    nxt = Date(year + (month == 12), month % 12 + 1, 1)
    d = nxt - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


class ExpiryCalendar:
    def __init__(self, rules: list[ExpiryRule], is_trading_day: Callable[[Date], bool]) -> None:
        self.rules = sorted(rules, key=lambda r: r.first_expiry_on_or_after)
        self.is_trading_day = is_trading_day

    # ------------------------------------------------------------------ one expiry
    def _shift(self, d: Date) -> Date:
        for _ in range(15):  # no real run of holidays is longer
            if self.is_trading_day(d):
                return d
            d -= timedelta(days=1)
        raise ExpiryConfigError(f"no trading day found before {d}: is the trading-day predicate empty?")

    def _pick(self, nominal_of: Callable[[int], Date]) -> tuple[Date, ExpiryRule]:
        chosen: tuple[Date, ExpiryRule] | None = None
        for r in self.rules:
            nominal = nominal_of(r.weekday)
            if nominal >= r.first_expiry_on_or_after:
                chosen = (nominal, r)
        assert chosen is not None  # the first rule holds from the beginning
        return chosen

    def weekly_in_week_of(self, d: Date) -> Expiry:
        monday = d - timedelta(days=d.weekday())
        nominal, rule = self._pick(lambda wd: monday + timedelta(days=wd))
        return Expiry(self._shift(nominal), "weekly", nominal, WEEKDAYS[rule.weekday])

    def monthly_of(self, year: int, month: int) -> Expiry:
        nominal, rule = self._pick(lambda wd: _last_weekday_of_month(year, month, wd))
        return Expiry(self._shift(nominal), "monthly", nominal, WEEKDAYS[rule.weekday])

    # ------------------------------------------------------------------ ranges
    def expiries(self, start: Date, end: Date) -> list[Expiry]:
        """Every expiry date in [start, end], oldest first. A date that is both is reported "monthly"."""
        found: dict[Date, Expiry] = {}
        monday = start - timedelta(days=start.weekday())
        while monday <= end + timedelta(days=7):
            e = self.weekly_in_week_of(monday)
            if start <= e.date <= end:
                found.setdefault(e.date, e)
            monday += timedelta(days=7)
        y, m = start.year, start.month
        while Date(y, m, 1) <= end + timedelta(days=31):
            e = self.monthly_of(y, m)
            if start <= e.date <= end:
                found[e.date] = e  # monthly wins over a weekly on the same day
            y, m = y + (m == 12), m % 12 + 1
        return [found[d] for d in sorted(found)]

    def next_expiry(self, on: Date, cycle: str = "weekly", *, skip_expiry_day: bool = False) -> Expiry:
        """The nearest expiry of `cycle` on/after `on` (strictly after when `skip_expiry_day` and `on` is one).

        "weekly" = the nearest weekly-or-monthly expiry (the nearest weekly contract is whichever expires
        first, a monthly expiry day is also a weekly one); "monthly" = the nearest monthly."""
        if cycle not in KINDS:
            raise ValueError(f"cycle must be one of {KINDS}")
        horizon = on + timedelta(days=70)
        for e in self.expiries(on, horizon):
            if cycle == "monthly" and e.kind != "monthly":
                continue
            if skip_expiry_day and e.date == on:
                continue
            return e
        raise ExpiryConfigError(f"no {cycle} expiry found within 70 days of {on}")

    def is_expiry_day(self, d: Date, cycle: str = "weekly") -> bool:
        return any(e.date == d and (cycle == "weekly" or e.kind == "monthly") for e in self.expiries(d, d))
