"""Seeded event list + reaction_day rule (approved 2026-10-03)."""

from __future__ import annotations

from datetime import date

from app.options.events import EventCalendar, EventDay, load_default_events, next_trading_session

D = date.fromisoformat
EV = load_default_events()


def test_the_seeded_list_has_the_approved_dates() -> None:
    dates = [e.date for e in EV.days]
    assert D("2024-07-23") in dates and D("2024-07-22") not in dates
    assert D("2024-06-01") in dates
    kinds = {e.date: e.kind for e in EV.days}
    assert kinds[D("2024-06-01")] == "exit_poll"
    assert kinds[D("2024-07-23")] == "budget"
    assert all(e.source for e in EV.days)


def test_a_non_trading_event_also_flags_the_next_session_as_reaction_day() -> None:
    # Karnataka results: Saturday 13 May 2023 → Monday 15 May
    assert EV.is_event(D("2023-05-13"))
    assert not EV.is_event(D("2023-05-15"))
    assert EV.reaction_of(D("2023-05-13")) == D("2023-05-15")
    assert EV.is_reaction(D("2023-05-15"))
    assert EV.spans_reaction(D("2023-05-15"), D("2023-05-15"))
    assert not EV.spans(D("2023-05-15"), D("2023-05-15"))
    # Exit polls: Saturday 1 June 2024 → Monday 3 June
    assert EV.reaction_of(D("2024-06-01")) == D("2024-06-03")
    assert EV.is_reaction(D("2024-06-03"))
    assert not EV.is_reaction(D("2024-06-04"))  # counting day is its own event, not a reaction


def test_a_weekend_that_was_a_trading_session_flags_on_the_day_itself() -> None:
    for day in ("2025-02-01", "2026-02-01"):
        d = D(day)
        assert EV.is_event(d)
        assert EV.reaction_of(d) is None
        assert not EV.is_reaction(d)
        # the following Monday is not a reaction day
        nxt = next_trading_session(d, lambda x: x.weekday() < 5)
        assert not EV.is_reaction(nxt)
        assert EV.spans(d, d) and not EV.spans_reaction(d, d)


def test_reaction_mapping_is_driven_by_the_trading_session_predicate() -> None:
    ev = EventCalendar(
        [EventDay(D("2024-06-01"), "exit_poll", "polls")],
        is_trading_session=lambda d: d.weekday() < 5,
        extra_sessions=frozenset(),
    )
    assert ev.reaction_of(D("2024-06-01")) == D("2024-06-03")
    # treat Saturday as a session: no reaction
    traded = EventCalendar(
        [EventDay(D("2024-06-01"), "exit_poll", "polls")],
        is_trading_session=lambda d: True,
        extra_sessions=frozenset(),
    )
    assert traded.reaction_of(D("2024-06-01")) is None
