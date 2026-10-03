"""Approved strategies land in app/strategies/user/."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from app.pine.sandbox import check_source

USER_DIR = Path(__file__).resolve().parents[1] / "strategies" / "user"
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _strategy_class(node: ast.ClassDef) -> bool:
    for base in node.bases:
        base_name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", None)
        if base_name in ("Strategy", "PinePort"):
            return True
    return False


def module_name(source: str) -> str:
    """The file stem: the class's `name` string, or the class name in snake case."""
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or not _strategy_class(node):
            continue
        for stmt in node.body:
            value = None
            targets: list[ast.expr] = []
            if isinstance(stmt, ast.Assign):
                targets = list(stmt.targets)
                value = stmt.value
            elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                targets = [stmt.target]
                value = stmt.value
            if not any(isinstance(target, ast.Name) and target.id == "name" for target in targets):
                continue
            if isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value.isidentifier():
                return value.value
        snake = _CAMEL.sub("_", node.name).lower()
        if snake.isidentifier():
            return snake
    raise ValueError("source must define a Strategy subclass")


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
