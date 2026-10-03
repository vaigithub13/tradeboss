"""AST allow-list. Generated code is untrusted until this passes."""

from __future__ import annotations

import ast

ALLOWED = {
    "app.backtest.contracts": {"Signal", "Strategy"},
    "app.strategies.pine_common": {"PinePort", "entry_window", "minute_of", "TICK"},
}
_BANNED_CALLS = {
    "open", "exec", "eval", "__import__", "compile", "getattr", "setattr",
    "globals", "locals", "vars", "breakpoint", "input", "help",
}
_STRATEGY_BASES = {"Strategy", "PinePort"}


class SandboxError(ValueError):
    pass


def check_source(source: str) -> None:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise SandboxError(f"syntax error: {exc.msg}") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                raise SandboxError(f"import {alias.name} is not allowed")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            allowed = ALLOWED.get(module)
            if allowed is None:
                raise SandboxError(f"import {module} is not allowed")
            for alias in node.names:
                if alias.name == "*" or alias.name not in allowed:
                    raise SandboxError(f"import {module}.{alias.name} is not allowed")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _BANNED_CALLS:
                raise SandboxError(f"{func.id}() is not allowed")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise SandboxError(f"attribute {node.attr} is not allowed")
    if not any(_is_strategy(node) for node in tree.body if isinstance(node, ast.ClassDef)):
        raise SandboxError("source must define a Strategy subclass")


def _is_strategy(node: ast.ClassDef) -> bool:
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id in _STRATEGY_BASES:
            return True
        if isinstance(base, ast.Attribute) and base.attr in _STRATEGY_BASES:
            return True
    return False
