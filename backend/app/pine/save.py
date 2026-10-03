"""Approved strategies land in app/strategies/user/."""

from __future__ import annotations

from pathlib import Path

from app.pine.sandbox import check_source

USER_DIR = Path(__file__).resolve().parents[1] / "strategies" / "user"


def save_user_strategy(name: str, source: str, *, directory: Path | None = None, replace: bool = False) -> Path:
    if not name.isidentifier():
        raise ValueError(f"strategy name {name!r} is not an identifier")
    check_source(source)
    folder = USER_DIR if directory is None else directory
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.py"
    if path.exists() and not replace:
        raise FileExistsError(name)
    path.write_text(source)
    return path
