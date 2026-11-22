"""Text for a desktop notification. The browser plays the sound; this side only writes the words."""

from __future__ import annotations


def signal_message(row: dict) -> str:
    return (
        f"{row.get('symbol')} {row.get('side')} {row.get('setup')}  "
        f"entry { _num(row.get('entry')) }  SL { _num(row.get('stop')) }  "
        f"target { _num(row.get('target')) }  qty { row.get('qty') }  "
        f"target { _num(row.get('target_pct')) }% / stop { _num(row.get('stop_pct')) }%"
    )


def trade_message(trade: dict, *, kind: str) -> str:
    verb = "entered" if kind == "entry" else "exited"
    return (
        f"Slot 8 {verb} {trade.get('symbol')} {trade.get('side')}  "
        f"entry { _num(trade.get('entry')) }  SL { _num(trade.get('stop')) }  "
        f"target { _num(trade.get('target')) }  qty { trade.get('qty') }  "
        f"target { _num(trade.get('tm_target_pct')) }% / stop { _num(trade.get('tm_stop_pct')) }%"
    )


def cutoff_message() -> str:
    return "14:30 IST. Stock Scanner will not take a new trade today."


def flat_message(net: float) -> str:
    return f"15:15 IST. Slot 8 is flat. Day P&L ₹{net:.0f}."


def _num(value: object) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)
