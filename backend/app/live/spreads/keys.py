"""Chart subscriptions stay capped on their own. Option keys are added after them."""

from __future__ import annotations

from collections.abc import Iterable

CHART_CAP = 12
OPTION_CAP = 40  # far below Upstox's 1500 combined full-mode keys per user; still one connection


def _segment_rank(key: str) -> int:
    """Nifty (NSE_FO) before Sensex (BSE_FO) so a full cap never drops a Nifty paper contract for BSE."""
    if key.startswith("NSE_FO|"):
        return 0
    if key.startswith("BSE_FO|"):
        return 1
    return 2


def _sorted_keys(keys: Iterable[str]) -> list[str]:
    return sorted(keys, key=lambda k: (_segment_rank(k), k))


def order_option_keys(held: Iterable[str], atm: Iterable[str], spread: Iterable[str]) -> list[str]:
    """The option list in priority order: contracts paper holds (never refused), each slot's current ATM call and
    put, then the spread recorder's ATM +-2 set. Within held/ATM, NSE_FO ranks before BSE_FO."""
    return list(dict.fromkeys([*_sorted_keys(held), *_sorted_keys(atm), *spread]))


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
