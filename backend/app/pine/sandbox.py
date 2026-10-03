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


def _text(source: str, lineno: int | None) -> str:
    if lineno is None:
        return ""
    lines = source.splitlines()
    if lineno < 1 or lineno > len(lines):
        return ""
    return lines[lineno - 1].strip()


def _remove(source: str, node: ast.AST) -> str:
    return f"remove line {node.lineno}: {_text(source, node.lineno)}"


def check_source(source: str) -> None:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise SandboxError(f"line {exc.lineno}: syntax error: {exc.msg}") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            raise SandboxError(_remove(source, node))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = [alias.name for alias in node.names]
            if module == "__future__":
                plain = names == ["annotations"] and all(alias.asname is None for alias in node.names)
                if plain:
                    continue
                text = _text(source, node.lineno)
                if "annotations" in names:
                    raise SandboxError(
                        f"line {node.lineno}: change {text!r} to 'from __future__ import annotations'"
                    )
                raise SandboxError(f"remove line {node.lineno}: {text}")
            allowed = ALLOWED.get(module)
            if allowed is None or any(alias.name == "*" or alias.name not in allowed for alias in node.names):
                raise SandboxError(_remove(source, node))
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _BANNED_CALLS:
                raise SandboxError(_remove(source, node))
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise SandboxError(_remove(source, node))
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    if not any(_is_strategy(node) for node in classes):
        if classes:
            text = _text(source, classes[0].lineno)
            raise SandboxError(f"line {classes[0].lineno}: change {text!r} to a Strategy subclass")
        raise SandboxError("source must define a Strategy subclass")


def _is_strategy(node: ast.ClassDef) -> bool:
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id in _STRATEGY_BASES:
            return True
        if isinstance(base, ast.Attribute) and base.attr in _STRATEGY_BASES:
            return True
    return False
