"""Deterministic Pine checks. These own the trap checklist; the model does not."""

from __future__ import annotations

import re

SESSION_OPEN = 9 * 60 + 15
SESSION_CLOSE = 15 * 60 + 30
TIMEFRAMES: tuple[tuple[str, int], ...] = (
    ("1m", 1), ("3m", 3), ("5m", 5), ("15m", 15), ("30m", 30), ("1h", 60),
)
UNRESOLVED = "unresolved: check by hand"
_TIME_ARG = re.compile(r"time\s*\(\s*timeframe\.period\s*,\s*([^)]+?)\s*\)")
_ASSIGN = re.compile(r"(?m)^([A-Za-z_]\w*)\s*=(?!=)\s*(.+)$")
_IDENT = re.compile(r"[A-Za-z_]\w*")
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


def _session_literal(text: str) -> str | None:
    match = re.fullmatch(r'"(\d{4}-\d{4})"', text.strip())
    return match.group(1) if match else None


def _input_session(rhs: str) -> str | None:
    """The session string on an input() default, when it is a constant."""
    if not rhs.lstrip().startswith("input"):
        return None
    match = re.search(r'defval\s*=\s*"(\d{4}-\d{4})"', rhs)
    if match:
        return match.group(1)
    match = re.match(r'input\s*\(\s*"(\d{4}-\d{4})"', rhs.strip())
    return match.group(1) if match else None


def _constants(source: str) -> dict[str, str | None]:
    """Names bound to a session string. None means the assignment is not a constant session."""
    rows = [(name, rhs.strip()) for name, rhs in _ASSIGN.findall(source) if not rhs.strip().startswith("time")]
    env: dict[str, str | None] = {}
    for _ in range(len(rows) + 1):
        for name, rhs in rows:
            literal = _session_literal(rhs)
            if literal:
                env[name] = literal
                continue
            if rhs.lstrip().startswith("input"):
                env[name] = _input_session(rhs)
                continue
            if _IDENT.fullmatch(rhs) and rhs in env:
                env[name] = env[rhs]
                continue
            if name not in env and not (_IDENT.fullmatch(rhs) and rhs not in env):
                env[name] = None
    return env


def _resolve_session(arg: str, env: dict[str, str | None]) -> str | None:
    literal = _session_literal(arg)
    if literal:
        return literal
    name = arg.strip()
    if _IDENT.fullmatch(name) and env.get(name):
        return env[name]
    return None


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


def _session_trap(source: str) -> tuple[dict[str, str], list[str], str]:
    """Per-timeframe hit/clear/unresolved, the windows that resolved, and the trap status.

    A session passed through an input() default or a simple assignment is followed.
    A time() argument that does not resolve is unresolved, never clear.
    """
    env = _constants(source)
    args = _TIME_ARG.findall(source)
    names = [name for name, _ in TIMEFRAMES]
    if not args:
        return {name: "clear" for name in names}, [], "clear"
    windows: list[str] = []
    unresolved = False
    for arg in args:
        window = _resolve_session(arg, env)
        if window is None:
            unresolved = True
        elif window not in windows:
            windows.append(window)
    per_tf = {name: "clear" for name in names}
    for window in windows:
        for name, status in session_timeframes(window).items():
            if status == "hit":
                per_tf[name] = "hit"
    if unresolved:
        for name in names:
            if per_tf[name] != "hit":
                per_tf[name] = UNRESOLVED
    if any(status == "hit" for status in per_tf.values()):
        return per_tf, windows, "hit"
    if unresolved:
        return per_tf, windows, UNRESOLVED
    return per_tf, windows, "clear"


def scan(source: str) -> dict:
    per_tf, windows, session_status = _session_trap(source)
    session_hit = session_status == "hit"

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
    if session_hit or (kind == "strategy" and not has_close):
        overnight = "hit"
    elif session_status == UNRESOLVED:
        overnight = UNRESOLVED
    else:
        overnight = "clear"
    return {
        "kind": kind,
        "windows": windows,
        "traps": {
            "session": {"status": session_status, "timeframes": per_tf},
            "stop_na": {"status": "hit" if stop_hit else "clear"},
            "pivot": {"status": "hit" if pivot_delay is not None or _PIVOT.search(source) else "clear", "delay": pivot_delay},
            "lookahead": {"status": lookahead},
            "overnight": {"status": overnight},
            "true_range": {"calls": calls},
        },
    }
