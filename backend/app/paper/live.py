"""The live paper runner the feed service drives: one strategy, one day at a time.

Everything runs on the feed's event loop (frames, bars, the API routes are async), so the state needs no
lock. The runner never places an order: it only records signals and paper fills.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from app.backtest.catalog import build_strategy
from app.backtest.costs import load_default_cost_table
from app.backtest.expiry import load_default_calendar
from app.backtest.lots import load_default_lot_table
from app.live.model import ist_date
from app.live.spreads.decode import depth_quotes
from app.options.contract import OptionContract, choose_contract
from app.options.strikes import load_default_step_table
from app.data.store import CandleStore
from app.paper.check import end_of_day_check
from app.paper.history import stored_warmup
from app.paper.pricing import VixSeries, model_price_for
from app.paper.quotes import QuoteBook
from app.paper.session import PaperSession
from app.paper.store import load_day, save_day, weekly_summary
from app.upstox.instruments import current_index

MakeSession = Callable[[date, str, dict[str, Any]], PaperSession]


class PaperError(RuntimeError):
    pass


class AlreadyRunning(PaperError):
    pass


class NotRunning(PaperError):
    pass


class UnsupportedSettings(PaperError):
    """Settings paper trading cannot honour (a strategy's own target or stop)."""


class SettingsMismatch(PaperError):
    """Today's file was written by another strategy or other parameters; starting would mix them."""


def choose_nearest_weekly_atm(direction: str, spot: float, on: date) -> OptionContract:
    return choose_contract(direction, spot, on, calendar=load_default_calendar(),
                           lots=load_default_lot_table(), steps=load_default_step_table())


def session_factory(instruments_dir: Path, vix: VixSeries, store: CandleStore, symbol: str = "NIFTY50") -> MakeSession:
    """Builds a PaperSession for the live feed: the instrument master for option keys, the model for
    fallback prices, the feed's VIX, and the strategy warmed on the stored bars before the day."""

    def make(day: date, strategy: str, params: dict[str, Any]) -> PaperSession:
        index = current_index(instruments_dir)
        if index is None:
            raise PaperError("no instrument snapshot yet, so option contracts cannot be chosen")
        key_of = {i.symbol: i.key for i in index.instruments}
        session = PaperSession(
            day=day,
            strategy=build_strategy({"strategy": strategy, "params": params}),
            choose=choose_nearest_weekly_atm,
            key_for=key_of.get,
            quotes=QuoteBook(),
            cost_table=load_default_cost_table(),
            model_price=model_price_for(vix.at),
            symbol=symbol,
        )
        session.warm(stored_warmup(store, symbol, day))
        return session

    return make


class PaperRunner:
    def __init__(self, directory: Path, make_session: MakeSession,
                 on_wanted: Callable[[], None] | None = None,
                 catch_up: Callable[[PaperSession, date], None] | None = None) -> None:
        self.directory = directory
        self._make = make_session
        self._on_wanted = on_wanted
        self._catch_up = catch_up  # replays the day's recorded feed into a session: trades what it has not decided
        self.state = "stopped"  # running | stopped
        self.ended_by: str | None = None
        self.day: date | None = None
        self.strategy: str | None = None
        self.params: dict[str, Any] = {}
        self.session: PaperSession | None = None
        self.eod_check: dict[str, Any] | None = None
        self._wanted_sent: frozenset[str] = frozenset()

    # ------------------------------------------------------------------ control
    def start(self, day: date, strategy: str, params: dict[str, Any]) -> None:
        """Start (or resume) today's strategy. The session warms on the stored bars, catches up on the day's
        recorded feed, and resumes from today's file if there is one, so the signals do not depend on when
        this was called."""
        if self.state == "running":
            raise AlreadyRunning("a strategy is already running")
        saved = load_day(self.directory, day)
        if saved is not None and (saved.get("requested_strategy") != strategy
                                  or saved.get("requested_params") != dict(params)):
            raise SettingsMismatch(
                f"today already ran {saved.get('requested_strategy')} with other settings; "
                "start the same strategy and settings, or move the file aside"
            )
        session = self._make(day, strategy, params)  # bad strategy names raise here, before any state changes
        if getattr(session.strategy, "use_target", False) or getattr(session.strategy, "use_stop", False):
            raise UnsupportedSettings("paper trading does not apply a strategy's target or stop yet (it acts on BUY/SELL "
                             "signals only); run it without them, or test them in a backtest")
        if saved is not None:
            session.restore(saved)
        self.session = session
        self.day, self.strategy, self.params = day, strategy, dict(params)
        self.eod_check = None
        if self._catch_up is not None:
            self._catch_up(session, day)
        self.state, self.ended_by = "running", None
        self._save()
        self._sync_wanted()

    def resume_if_running(self, day: date) -> bool:
        """After a restart: if today's strategy was running when the backend stopped, start it again.
        A day that had already ended is loaded as it was, so the panel still shows it."""
        saved = load_day(self.directory, day)
        if saved is None or self.state == "running":
            return False
        if saved.get("state") != "running":
            self._load_ended(day)
            return False
        self.start(day, saved["requested_strategy"], saved["requested_params"])
        return True

    def stop(self, now_ms: int, reason: str = "stopped") -> None:
        if self.state != "running" or self.session is None:
            raise NotRunning("no strategy is running")
        self.session.close_now(now_ms, reason="stop")
        self._end(reason)

    def finish_day(self) -> None:
        """The session has ended: close the book's bars and keep the day's file. Nothing is traded after this."""
        if self.session is None or self.state != "running":
            return
        self.session.end_of_day()
        self._end("session ended")

    def reconcile_check(self, store: CandleStore, symbol: str, day: date | None = None) -> dict[str, Any] | None:
        """After the reconcile: rerun the normal backtest for the day on the official bars and keep the
        differences with their reasons in the day's file. Only once the day has ended. With `day`, a runner
        that lost the ended session to a restart rebuilds it from that day's file first."""
        if day is not None and self.state != "running" and (self.session is None or self.day != day):
            self._load_ended(day)
        if self.session is None or self.day is None or self.state == "running":
            return None
        s = self.session
        entries = [{"time": r["time"], "side": r["side"]} for r in s.signals
                   if r["side"] in ("BUY", "SELL") and r["time"] is not None]
        strategy, params = self.strategy, self.params
        self.eod_check = end_of_day_check(
            day=self.day, live_entries=entries, live_bars=s.live_bars(), incomplete=s.bars.incomplete,
            store=store, symbol=symbol,
            make_strategy=lambda: build_strategy({"strategy": strategy, "params": params}),
        )
        self._save()
        return self.eod_check

    def _load_ended(self, day: date) -> None:
        """Take an ended day from its file, without running the strategy: the check needs its signals and bars."""
        saved = load_day(self.directory, day)
        if saved is None or saved.get("state") == "running" or "requested_strategy" not in saved:
            return
        strategy, params = saved["requested_strategy"], dict(saved.get("requested_params") or {})
        session = self._make(day, strategy, params)
        session.restore(saved)
        self.session, self.day, self.strategy, self.params = session, day, strategy, params
        self.state, self.ended_by = "stopped", saved.get("ended_by")
        self.eod_check = saved.get("eod_check")

    def flush(self) -> None:
        """Write the day's file now (backend shutdown). A running strategy stays `running` in the file, so a
        restart resumes it."""
        self._save()

    def _end(self, reason: str) -> None:
        self.state, self.ended_by = "stopped", reason
        self._save()
        self._sync_wanted()

    # ------------------------------------------------------------------ feed input
    def on_depth(self, raw: bytes) -> None:
        if self.state == "running" and self.session is not None:
            self.session.on_depth(depth_quotes(raw))

    def on_clock(self, now_ms: int) -> None:
        """The feed's exchange time after each frame: squares off at 15:15:00 at that moment's quote."""
        if self.state != "running" or self.session is None or self.day is None or ist_date(now_ms) != self.day:
            return
        if self.session.on_clock(now_ms):
            self._save()
            self._sync_wanted()

    def on_index_bar(self, bar: dict[str, Any], *, now_ms: int) -> None:
        if self.state != "running" or self.session is None or self.day is None:
            return
        if ist_date(now_ms) != self.day:
            return  # a stale bar from another day never reaches the strategy
        records = self.session.on_index_minute(bar, now_ms=now_ms)
        self.session.prepare(float(bar["close"]))
        if records:
            self._save()
        self._sync_wanted()

    def wanted_keys(self) -> frozenset[str]:
        if self.state != "running" or self.session is None:
            return frozenset()
        return frozenset(self.session.wanted)

    def _sync_wanted(self) -> None:
        wanted = self.wanted_keys()
        if wanted != self._wanted_sent:
            self._wanted_sent = wanted
            if self._on_wanted is not None:
                self._on_wanted()

    # ------------------------------------------------------------------ output
    def status(self, now_ms: int) -> dict[str, Any]:
        out: dict[str, Any] = {
            "state": self.state,
            "ended_by": self.ended_by,
            "day": None if self.day is None else self.day.isoformat(),
            "strategy": self.strategy,
            "params": self.params,
            "wanted_keys": sorted(self.wanted_keys()),
        }
        if self.session is not None:
            snap = self.session.snapshot()
            out.update(signals=snap["signals"], trades=snap["trades"], summary=snap["summary"],
                       position=snap["open"], mark=self.session.mark(now_ms))
        return out

    def day_file(self, day: date) -> dict[str, Any] | None:
        return load_day(self.directory, day)

    def week(self, any_day: date) -> dict[str, Any]:
        return weekly_summary(self.directory, any_day)

    def _save(self) -> None:
        if self.session is None or self.day is None:
            return
        extra = {"eod_check": self.eod_check} if self.eod_check is not None else {}
        save_day(self.directory, self.day, {
            "state": self.state,
            "ended_by": self.ended_by,
            "requested_strategy": self.strategy,
            "requested_params": self.params,
            **self.session.snapshot(),
            **extra,
        })
