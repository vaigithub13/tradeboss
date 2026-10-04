"""Per-symbol drawings in SQLite. Anchors are stored as time + price."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

TOOLS = (
    "trend",
    "ray",
    "extended",
    "horizontal",
    "horizontal_ray",
    "vertical",
    "rectangle",
    "fib",
    "text",
    "measure",
)
_LINE = ("solid", "dashed", "dotted")
_TIMEFRAMES = ("1m", "3m", "5m", "15m", "30m", "1h", "1D", "1W")


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    return float(value)


def _style(raw: Any) -> dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    line = src.get("lineStyle", "solid")
    if line not in _LINE:
        line = "solid"
    fill = src.get("fill")
    return {
        "color": src.get("color") if isinstance(src.get("color"), str) else "#2962ff",
        "width": int(_number(src.get("width", 1), "width")),
        "lineStyle": line,
        "extendLeft": bool(src.get("extendLeft", False)),
        "extendRight": bool(src.get("extendRight", False)),
        "fill": fill if isinstance(fill, str) else None,
    }


def _drawing(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("drawing must be an object")
    tool = raw.get("tool")
    if tool not in TOOLS:
        raise ValueError(f"unknown tool {tool!r}")
    anchors = raw.get("anchors")
    if not isinstance(anchors, list) or len(anchors) == 0:
        raise ValueError("anchors required")
    clean: list[dict[str, Any]] = []
    for anchor in anchors:
        if not isinstance(anchor, dict) or "time" not in anchor or "price" not in anchor:
            raise ValueError("anchor needs time and price")
        time = anchor["time"]
        if isinstance(time, bool) or not isinstance(time, int):
            raise ValueError("anchor time must be a whole number of seconds")
        clean.append({"time": time, "price": _number(anchor["price"], "price")})
    known = raw.get("knownAt")
    if isinstance(known, bool) or not isinstance(known, int):
        raise ValueError("knownAt must be a whole number of seconds")
    drawing_id = raw.get("id")
    if not isinstance(drawing_id, str) or drawing_id == "":
        raise ValueError("drawing id is required")
    text = raw.get("text")
    return {
        "id": drawing_id,
        "tool": tool,
        "anchors": clean,
        "knownAt": known,
        "drawnOn": _drawn_on(raw),
        "showOn": _show_on(raw),
        "hidden": bool(raw.get("hidden", False)),
        "locked": bool(raw.get("locked", False)),
        "text": text if isinstance(text, str) else "",
        "style": _style(raw.get("style")),
    }


def _drawn_on(raw: dict[str, Any]) -> str:
    drawn = raw.get("drawnOn", "")
    if drawn is None:
        return ""
    if not isinstance(drawn, str) or (drawn != "" and drawn not in _TIMEFRAMES):
        raise ValueError("drawnOn must be a timeframe or empty")
    return drawn


def _show_on(raw: dict[str, Any]) -> list[str] | None:
    if "showOn" not in raw or raw.get("showOn") is None:
        return None
    show = raw["showOn"]
    if not isinstance(show, list):
        raise ValueError("showOn must be a list of timeframes or null")
    clean: list[str] = []
    for tf in show:
        if tf not in _TIMEFRAMES:
            raise ValueError(f"unknown timeframe {tf!r}")
        if tf not in clean:
            clean.append(tf)
    return clean


class DrawingStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS drawings (symbol TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def replace(self, symbol: str, drawings: list[Any]) -> None:
        if not isinstance(symbol, str) or symbol == "":
            raise ValueError("symbol is required")
        clean = [_drawing(item) for item in drawings]
        payload = json.dumps(clean)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO drawings (symbol, payload) VALUES (?, ?)
                ON CONFLICT(symbol) DO UPDATE SET payload = excluded.payload
                """,
                (symbol, payload),
            )

    def load(self, symbol: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT payload FROM drawings WHERE symbol = ?", (symbol,)).fetchone()
        if row is None:
            return []
        data = json.loads(row[0])
        return data if isinstance(data, list) else []

    def export_payload(self, symbol: str) -> dict[str, Any]:
        return {"symbol": symbol, "drawings": self.load(symbol)}

    def import_payload(self, payload: Any) -> str:
        if not isinstance(payload, dict) or "symbol" not in payload:
            raise ValueError("import needs a symbol")
        symbol = payload["symbol"]
        drawings = payload.get("drawings") or []
        if not isinstance(drawings, list):
            raise ValueError("drawings must be a list")
        self.replace(symbol, drawings)
        return str(symbol)
