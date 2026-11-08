"""Persist morning (09:10) and EOD Options bias snapshots under data/options/bias/."""

from __future__ import annotations

import json
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Literal

from app.options.time import IST

Slot = Literal["morning", "eod"]

MORNING_FROM = time(9, 10)
MORNING_TO = time(9, 20)
EOD_FROM = time(15, 25)
EOD_TO = time(15, 35)


def bias_dir(options_dir: Path) -> Path:
    return options_dir / "bias"


def bias_path(options_dir: Path, day: date) -> Path:
    return bias_dir(options_dir) / f"{day.isoformat()}.json"


def load_day(options_dir: Path, day: date) -> dict[str, Any] | None:
    path = bias_path(options_dir, day)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def slot_for(now: datetime) -> Slot | None:
    local = now.astimezone(IST)
    clock = local.time()
    if MORNING_FROM <= clock <= MORNING_TO:
        return "morning"
    if EOD_FROM <= clock <= EOD_TO:
        return "eod"
    return None


def save_slot(
    options_dir: Path,
    *,
    day: date,
    slot: Slot,
    bias: dict[str, Any],
    overview: dict[str, Any] | None = None,
    levels: dict[str, Any] | None = None,
    overwrite: bool = False,
) -> Path | None:
    """Write morning/eod block. Skip if slot already present unless overwrite."""
    d = bias_dir(options_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = bias_path(options_dir, day)
    existing = load_day(options_dir, day) or {"day": day.isoformat()}
    if slot in existing and not overwrite:
        return None
    existing[slot] = {
        "saved_at": datetime.now(IST).isoformat(timespec="seconds"),
        "bias": bias,
        "overview": overview,
        "levels": levels,
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(existing, indent=1), encoding="utf-8")
    tmp.replace(path)
    return path


def maybe_save_from_view(
    options_dir: Path,
    view: dict[str, Any],
    *,
    now: datetime | None = None,
    overwrite: bool = False,
) -> Slot | None:
    """If clock is in a save window and view has bias, persist that slot."""
    local = now or datetime.now(IST)
    slot = slot_for(local)
    if slot is None:
        return None
    bias = view.get("bias")
    if not isinstance(bias, dict):
        return None
    day = local.date()
    levels = {
        "spot": view.get("spot"),
        "resistance": view.get("resistance"),
        "support": view.get("support"),
        "pivot": view.get("pivot"),
        "max_pain": view.get("max_pain"),
    }
    path = save_slot(
        options_dir,
        day=day,
        slot=slot,
        bias=bias,
        overview=view.get("overview"),
        levels=levels,
        overwrite=overwrite,
    )
    return slot if path is not None else None
