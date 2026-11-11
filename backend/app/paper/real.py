"""Manual "real trades" log: the fills you took by hand against a paper trade (slot 3 by default).

Stored apart from the paper day files under `data/paper/real/`. Never opens or closes a paper position.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))
DEFAULT_ACCOUNT_STOP = 5000.0
#: sell-stop limit sits this many points below the stop trigger (option tick)
SL_LIMIT_BUFFER = 0.05
EXIT_REASONS = ("target", "stop", "reversal", "square-off", "other")


def paper_trade_key(*, slot: str, day: str, fill_time: int) -> str:
    """Stable id of a paper trade: slot, IST day, fill unix seconds."""
    return f"{slot}:{day}:{int(fill_time)}"


def parse_paper_trade_key(key: str) -> tuple[str, str, int]:
    slot, day, fill = key.split(":", 2)
    return slot, day, int(fill)


def real_gross(*, entry_price: float, exit_price: float, units: int) -> float:
    """Bought option: (exit − entry) × units."""
    return round((float(exit_price) - float(entry_price)) * int(units), 2)


def real_net(*, entry_price: float, exit_price: float, units: int, charges: float) -> float:
    return round(real_gross(entry_price=entry_price, exit_price=exit_price, units=units) - float(charges), 2)


def compare_trade(paper: dict[str, Any], real: dict[str, Any]) -> dict[str, Any]:
    """Side-by-side diffs of one paper trade and its logged real fill."""
    units = int(real.get("units") or paper.get("units") or 0)
    entry_price = float(real["entry_price"])
    exit_price = float(real["exit_price"])
    charges = float(real["charges"])
    net = real_net(entry_price=entry_price, exit_price=exit_price, units=units, charges=charges)
    gross = real_gross(entry_price=entry_price, exit_price=exit_price, units=units)
    paper_entry = float(paper["entry_premium"])
    paper_exit = float(paper["exit_premium"])
    paper_net = float(paper["net"])
    paper_fill = int(paper.get("fill_time") or paper.get("entry_time") or 0)
    paper_exit_t = int(paper["exit_time"]) if paper.get("exit_time") is not None else None
    real_entry_t = int(real["entry_time"])
    real_exit_t = int(real["exit_time"])
    return {
        "entry_price_diff": round(entry_price - paper_entry, 2),
        "exit_price_diff": round(exit_price - paper_exit, 2),
        "entry_delay_s": real_entry_t - paper_fill,
        "exit_delay_s": None if paper_exit_t is None else real_exit_t - paper_exit_t,
        "net_diff": round(net - paper_net, 2),
        "gross": gross,
        "net": net,
        "charges": charges,
    }


def order_helper(open_position: dict[str, Any] | None, *, tick: float = SL_LIMIT_BUFFER) -> dict[str, Any] | None:
    """Broker order fields from slot 3's open paper position (bought option + premium 1:2 levels)."""
    if not open_position:
        return None
    levels = open_position.get("levels") or {}
    stop = levels.get("premium_stop")
    target = levels.get("premium_target")
    if stop is None or target is None:
        return None
    stop_f, target_f = float(stop), float(target)
    qty = int(open_position.get("units") or 0)
    if qty < 1:
        return None
    return {
        "contract": open_position.get("symbol") or open_position.get("contract"),
        "quantity": qty,
        "lots": int(open_position.get("lots") or 1),
        "lot_size": int(open_position.get("lot_size") or qty),
        "side": "BUY",  # paper always buys the option
        "entry_premium": float((open_position.get("entry") or {}).get("price")
                               or open_position.get("entry_price") or 0),
        "stop_trigger": stop_f,
        "stop_limit": round(stop_f - float(tick), 2),
        "target_limit": target_f,
    }


def account_stop_progress(*, real_net_total: float, account_stop: float) -> dict[str, Any]:
    """How much of the loss budget is used. `account_stop` is a positive rupee loss limit."""
    stop = abs(float(account_stop)) or DEFAULT_ACCOUNT_STOP
    loss = max(0.0, -float(real_net_total))
    used = min(1.0, loss / stop) if stop else 0.0
    return {
        "account_stop": stop,
        "real_net": round(float(real_net_total), 2),
        "loss_used": round(loss, 2),
        "fraction": round(used, 4),
        "reached": loss >= stop - 1e-9,
    }


def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


class RealTradeStore:
    """JSON files under `directory` (default data/paper/real): settings.json and trades.json."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @property
    def settings_path(self) -> Path:
        return self.directory / "settings.json"

    @property
    def trades_path(self) -> Path:
        return self.directory / "trades.json"

    def load_settings(self) -> dict[str, Any]:
        if not self.settings_path.exists():
            return {"account_stop": DEFAULT_ACCOUNT_STOP}
        data = json.loads(self.settings_path.read_text(encoding="utf-8"))
        stop = data.get("account_stop", DEFAULT_ACCOUNT_STOP)
        try:
            stop_f = float(stop)
        except (TypeError, ValueError):
            stop_f = DEFAULT_ACCOUNT_STOP
        if stop_f <= 0:
            stop_f = DEFAULT_ACCOUNT_STOP
        return {"account_stop": stop_f}

    def save_settings(self, *, account_stop: float) -> dict[str, Any]:
        stop = float(account_stop)
        if stop <= 0:
            raise ValueError("account_stop must be a positive rupee loss limit")
        body = {"account_stop": stop}
        _atomic_write(self.settings_path, body)
        return body

    def load_trades(self) -> list[dict[str, Any]]:
        if not self.trades_path.exists():
            return []
        data = json.loads(self.trades_path.read_text(encoding="utf-8"))
        return list(data.get("trades") or [])

    def _save_trades(self, trades: list[dict[str, Any]]) -> None:
        _atomic_write(self.trades_path, {"trades": trades})

    def upsert(self, record: dict[str, Any]) -> dict[str, Any]:
        """Insert or replace by `key`. Validates required fields; computes gross/net."""
        required = ("key", "slot", "day", "fill_time", "entry_time", "entry_price",
                    "exit_time", "exit_price", "exit_reason", "charges", "units")
        missing = [k for k in required if k not in record or record[k] is None]
        if missing:
            raise ValueError(f"missing field(s) {missing}")
        if record["exit_reason"] not in EXIT_REASONS:
            raise ValueError(f"exit_reason must be one of {EXIT_REASONS}")
        units = int(record["units"])
        if units < 1:
            raise ValueError("units must be >= 1")
        entry_price = float(record["entry_price"])
        exit_price = float(record["exit_price"])
        charges = float(record["charges"])
        if charges < 0:
            raise ValueError("charges must be >= 0")
        key = str(record["key"])
        if key != paper_trade_key(slot=str(record["slot"]), day=str(record["day"]),
                                  fill_time=int(record["fill_time"])):
            raise ValueError("key does not match slot/day/fill_time")
        if int(record["exit_time"]) < int(record["entry_time"]):
            raise ValueError("exit_time must be at or after entry_time")
        saved = {
            "key": key,
            "slot": str(record["slot"]),
            "day": str(record["day"]),
            "fill_time": int(record["fill_time"]),
            "contract": record.get("contract"),
            "entry_time": int(record["entry_time"]),
            "entry_price": entry_price,
            "exit_time": int(record["exit_time"]),
            "exit_price": exit_price,
            "exit_reason": record["exit_reason"],
            "charges": charges,
            "units": units,
            "gross": real_gross(entry_price=entry_price, exit_price=exit_price, units=units),
            "net": real_net(entry_price=entry_price, exit_price=exit_price, units=units, charges=charges),
            "logged_at": datetime.now(tz=IST).isoformat(timespec="seconds"),
        }
        trades = [t for t in self.load_trades() if t.get("key") != key]
        trades.append(saved)
        trades.sort(key=lambda t: (t["day"], t["fill_time"], t["slot"]))
        self._save_trades(trades)
        return saved

    def delete(self, key: str) -> bool:
        trades = self.load_trades()
        kept = [t for t in trades if t.get("key") != key]
        if len(kept) == len(trades):
            return False
        self._save_trades(kept)
        return True


def join_real_rows(
    paper_rows: list[dict[str, Any]],
    real_trades: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Each paper row plus optional `real` and `real_vs_paper` when a log exists for that trade."""
    by_key = {t["key"]: t for t in real_trades}
    out = []
    for row in paper_rows:
        key = paper_trade_key(slot=str(row["slot"]), day=str(row["date"]), fill_time=int(row["fill_time"]))
        real = by_key.get(key)
        item = {**row, "paper_key": key, "real": real, "real_vs_paper": None}
        if real is not None:
            item["real_vs_paper"] = compare_trade(row, real)
        out.append(item)
    return out


def real_totals(
    joined: list[dict[str, Any]],
    *,
    today: str | None = None,
    account_stop: float = DEFAULT_ACCOUNT_STOP,
) -> dict[str, Any]:
    """Daily and running real/paper nets for rows that have a real log, plus the account-stop bar."""
    logged = [r for r in joined if r.get("real") is not None]
    def _sum(rows: list[dict[str, Any]], field: str) -> float:
        return round(sum(float((r.get("real_vs_paper") or {}).get(field)
                               or (r.get("real") or {}).get(field) or 0) for r in rows), 2)

    running_real = _sum(logged, "net")
    running_paper = round(sum(float(r["net"]) for r in logged), 2)
    day_rows = [r for r in logged if today and r.get("date") == today]
    day_real = _sum(day_rows, "net")
    day_paper = round(sum(float(r["net"]) for r in day_rows), 2)
    return {
        "logged": len(logged),
        "day": {
            "date": today,
            "trades": len(day_rows),
            "real_net": day_real,
            "paper_net": day_paper,
            "net_diff": round(day_real - day_paper, 2),
        },
        "running": {
            "trades": len(logged),
            "real_net": running_real,
            "paper_net": running_paper,
            "net_diff": round(running_real - running_paper, 2),
        },
        "stop": account_stop_progress(real_net_total=running_real, account_stop=account_stop),
    }
