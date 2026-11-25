"""Equity delivery charges for swing research.

This is not the options table and not the intraday table. Those charge STT on
the sell only. Delivery STT is charged on both sides, and a flat DP charge is
added on the sell. Rates come from a dated file. Nothing here places an order.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.backtest.costs import CostConfigError, NoRatesForDate, UnknownRate

CENT = Decimal("0.01")
CRORE = Decimal(10_000_000)
DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_FILE = DATA_DIR / "cost_rates_equity_delivery.json"
RATE_KEYS = (
    "brokerage_flat",
    "brokerage_pct",
    "stt_pct",
    "exchange_pct",
    "sebi_per_crore",
    "stamp_buy_pct",
    "gst_pct",
    "dp_sell",
    "ipft_pct",
)
GST_ON = ("brokerage", "exchange", "sebi", "dp", "ipft")


def _dec(name: str, value: Any) -> Decimal:
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise CostConfigError(f"{name}: {value!r} is not a number") from exc
    if not number.is_finite() or number < 0:
        raise CostConfigError(f"{name}: must be a finite number >= 0, got {value!r}")
    return number


def _q(amount: Decimal) -> Decimal:
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def _money(price: Decimal | float | int) -> Decimal:
    if isinstance(price, Decimal):
        return price
    return Decimal(str(price))


@dataclass(frozen=True)
class DeliveryRow:
    effective_from: date
    rates: dict[str, Decimal]
    verification: str
    note: str
    sources: dict[str, str]


@dataclass(frozen=True)
class DeliveryLeg:
    side: str
    components: dict[str, Decimal]
    total: Decimal

    def __getitem__(self, key: str) -> Decimal:
        return self.components[key]


class DeliveryTable:
    """The latest row on or before the trade date. Dates before the first row fail."""

    def __init__(self, rows: list[DeliveryRow], gst_on: tuple[str, ...] = GST_ON) -> None:
        if not rows:
            raise CostConfigError("delivery cost table has no rows")
        self.rows = tuple(sorted(rows, key=lambda row: row.effective_from))
        self.gst_on = gst_on

    @classmethod
    def load(cls, path: Path | None = None) -> DeliveryTable:
        file = path or DEFAULT_FILE
        payload = json.loads(file.read_text())
        gst_on = tuple(payload.get("gst_on") or GST_ON)
        rows = []
        for raw in payload["rows"]:
            rates = {_key: _dec(_key, raw["rates"][_key]) for _key in RATE_KEYS}
            rows.append(
                DeliveryRow(
                    effective_from=date.fromisoformat(raw["effective_from"]),
                    rates=rates,
                    verification=str(raw.get("verification") or "unverified"),
                    note=str(raw.get("note") or ""),
                    sources={str(k): str(v) for k, v in (raw.get("sources") or {}).items()},
                )
            )
        return cls(rows, gst_on)

    def row_for(self, on: date) -> DeliveryRow:
        found: DeliveryRow | None = None
        for row in self.rows:
            if row.effective_from <= on:
                found = row
        if found is None:
            first = self.rows[0].effective_from.isoformat()
            raise NoRatesForDate(f"no delivery rates for {on.isoformat()} (first row {first})")
        return found

    def leg(
        self,
        side: str,
        price: Decimal | float | int,
        qty: int,
        on: date,
        flat_override: Decimal | None = None,
    ) -> DeliveryLeg:
        if side not in ("BUY", "SELL"):
            raise ValueError(f"side must be BUY or SELL, got {side!r}")
        if qty < 0:
            raise ValueError("qty must be >= 0")
        row = self.row_for(on)
        rates = row.rates
        turnover = _money(price) * Decimal(qty)

        def need(key: str) -> Decimal:
            if key not in rates:
                raise UnknownRate(f"rate {key!r} is missing on the row effective {row.effective_from}")
            return rates[key]

        flat = rates.get("brokerage_flat") if flat_override is None else flat_override
        pct = rates.get("brokerage_pct")
        options = [item for item in (flat, None if pct is None else turnover * pct / 100) if item is not None]
        if not options:
            need("brokerage_flat")
        brokerage = _q(min(options))
        stt = _q(turnover * need("stt_pct") / 100)
        exchange = _q(turnover * need("exchange_pct") / 100)
        sebi = _q(turnover * need("sebi_per_crore") / CRORE)
        stamp = _q(turnover * need("stamp_buy_pct") / 100) if side == "BUY" else Decimal("0.00")
        dp = _q(need("dp_sell")) if side == "SELL" and qty else Decimal("0.00")
        ipft = _q(turnover * need("ipft_pct") / 100)
        parts = {
            "brokerage": brokerage,
            "stt": stt,
            "exchange": exchange,
            "sebi": sebi,
            "stamp": stamp,
            "dp": dp,
            "ipft": ipft,
        }
        base = sum((parts[name] for name in self.gst_on), Decimal("0.00"))
        parts["gst"] = _q(base * need("gst_pct") / 100)
        total = sum(parts.values(), Decimal("0.00"))
        return DeliveryLeg(side, parts, total)

    def schedule(self) -> list[dict[str, str]]:
        """One line per dated row, for the research report."""
        out = []
        for row in self.rows:
            rates = row.rates
            out.append(
                {
                    "effective_from": row.effective_from.isoformat(),
                    "brokerage": f"min(₹{rates['brokerage_flat']}, {rates['brokerage_pct']}%)",
                    "stt_both": f"{rates['stt_pct']}%",
                    "exchange": f"{rates['exchange_pct']}%",
                    "sebi": f"₹{rates['sebi_per_crore']}/crore",
                    "stamp_buy": f"{rates['stamp_buy_pct']}%",
                    "gst": f"{rates['gst_pct']}%",
                    "dp_sell": f"₹{rates['dp_sell']}",
                    "ipft": f"{rates['ipft_pct']}%",
                    "verification": row.verification,
                    "note": row.note,
                }
            )
        return out
