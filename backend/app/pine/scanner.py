"""Deterministic Pine checks. These own the trap checklist; the model does not."""

from __future__ import annotations

import re

SESSION_OPEN = 9 * 60 + 15
SESSION_CLOSE = 15 * 60 + 30
TIMEFRAMES: tuple[tuple[str, int], ...] = (
    ("1m", 1), ("3m", 3), ("5m", 5), ("15m", 15), ("30m", 30), ("1h", 60),
)
_TIME = re.compile(r'time\s*\(\s*timeframe\.period\s*,\s*"(\d{4})-(\d{4})"\s*\)')
_PIVOT = re.compile(r"(?:ta\.)?pivot(?:high|low)\s*\(([^)]*)\)")
_TR = re.compile(r"\bta\.(?:atr|tr)\b")


def _hhmm(text: str) -> int:
    return int(text[:2]) * 60 + int(text[2:])


def bar_fits(window_start: int, window_end: int, bar_minutes: int) -> bool:
    """True when a chart bar lies fully inside the window.

    Bars are aligned from 09:15 and stop at the 15:30 session end. A bar that
    ends exactly on the window end does not fit.
    """
    if window_end <= window_start or bar_minutes < 1:
        return False
    start = SESSION_OPEN
    while start + bar_minutes <= SESSION_CLOSE:
        end = start + bar_minutes
        if start >= window_start and end < window_end:
            return True
        start += bar_minutes
    return False


def session_timeframes(window: str) -> dict[str, str]:
    """`HHMM-HHMM` → hit/clear for each chart timeframe."""
    left, right = window.split("-")
    w0, w1 = _hhmm(left), _hhmm(right)
    return {name: "clear" if bar_fits(w0, w1, minutes) else "hit" for name, minutes in TIMEFRAMES}


def _calls(source: str, name: str) -> list[str]:
    found: list[str] = []
    start = 0
    while True:
        at = source.find(name, start)
        if at < 0:
            return found
        cursor = at + len(name)
        while cursor < len(source) and source[cursor].isspace():
            cursor += 1
        if cursor >= len(source) or source[cursor] != "(":
            start = at + len(name)
            continue
        depth = 0
        for end in range(cursor, len(source)):
            if source[end] == "(":
                depth += 1
            elif source[end] == ")":
                depth -= 1
                if depth == 0:
                    found.append(source[cursor + 1:end])
                    start = end + 1
                    break
        else:
            return found


def _kind(source: str) -> str:
    if re.search(r"\bstrategy\s*\(", source) or "strategy.entry" in source or "strategy.close" in source:
        return "strategy"
    return "indicator"


def scan(source: str) -> dict:
    windows = [f"{a}-{b}" for a, b in _TIME.findall(source)]
    per_tf: dict[str, str] = {name: "clear" for name, _ in TIMEFRAMES}
    for window in windows:
        for name, status in session_timeframes(window).items():
            if status == "hit":
                per_tf[name] = "hit"
    session_hit = any(status == "hit" for status in per_tf.values())

    stop_hit = False
    for args in _calls(source, "strategy.entry"):
        if re.search(r"stop\s*=\s*.*\bna\b", args):
            stop_hit = True
    pivot_delay = None
    for args in _PIVOT.findall(source):
        numbers = [int(n) for n in re.findall(r"\d+", args)]
        if len(numbers) >= 2:
            pivot_delay = numbers[-1]
    lookahead = "clear"
    if "barmerge.lookahead_on" in source:
        lookahead = "hit"
    calls = []
    for match in _TR.finditer(source):
        name = match.group(0)
        if name not in calls:
            calls.append(name)
    kind = _kind(source)
    has_close = "strategy.close" in source
    overnight = "hit" if session_hit or (kind == "strategy" and not has_close) else "clear"
    return {
        "kind": kind,
        "windows": windows,
        "traps": {
            "session": {"status": "hit" if session_hit else "clear", "timeframes": per_tf},
            "stop_na": {"status": "hit" if stop_hit else "clear"},
            "pivot": {"status": "hit" if pivot_delay is not None or _PIVOT.search(source) else "clear", "delay": pivot_delay},
            "lookahead": {"status": lookahead},
            "overnight": {"status": overnight},
            "true_range": {"calls": calls},
        },
    }
