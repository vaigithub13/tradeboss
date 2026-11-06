"""Chart subscriptions stay capped on their own. Option keys are added after them."""

from __future__ import annotations

from collections.abc import Iterable

CHART_CAP = 12
OPTION_CAP = 40  # far below Upstox's 1500 combined full-mode keys per user; still one connection


def order_option_keys(held: Iterable[str], atm: Iterable[str], spread: Iterable[str]) -> list[str]:
    """The option list in priority order: contracts paper holds (never refused), each slot's current ATM call and
    put, then the spread recorder's ATM +-2 set."""
    return list(dict.fromkeys([*sorted(held), *sorted(atm), *spread]))


def compose_keys(chart: list[str], options: list[str], *, enabled: bool) -> list[str]:
    base = list(dict.fromkeys(chart))[:CHART_CAP]
    if not enabled:
        return base
    seen = set(base)
    extra: list[str] = []
    for key in options:
        if key in seen:
            continue
        extra.append(key)
        seen.add(key)
        if len(extra) >= OPTION_CAP:
            break
    return base + extra
