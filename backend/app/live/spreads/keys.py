"""Chart subscriptions stay capped on their own. Option keys are added after them."""

from __future__ import annotations

CHART_CAP = 12
OPTION_CAP = 20


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
