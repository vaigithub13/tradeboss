"""AST allow-list. Generated code is untrusted until this passes.

This is the first of four layers (see app/pine/worker.py and app/pine/isolated.py for the others): the worker
also runs the module with restricted builtins and an import allow-list, under a macOS sandbox profile.
"""

from __future__ import annotations

import ast

ALLOWED = {
    "app.backtest.contracts": ("Signal", "Strategy"),
    "app.strategies.pine_common": ("PinePort", "entry_window", "minute_of", "TICK", "timeframe_minutes"),
}
#: typing names a strategy may import. Not get_type_hints / ForwardRef (they evaluate strings), and not the
#: modules typing itself imports (`from typing import sys` would hand out sys).
TYPING_NAMES = (
    "Any", "Callable", "ClassVar", "Dict", "Final", "FrozenSet", "Iterable", "Iterator", "List", "Literal",
    "Mapping", "Optional", "Sequence", "Set", "Tuple", "Union", "cast",
)
#: Names refused anywhere they appear (as a call, an alias, an argument, an assignment): not only `open(...)`
#: but `f = open`, which the old call-only check let through.
_BANNED_NAMES = {
    "open", "exec", "eval", "__import__", "compile", "getattr", "setattr", "delattr",
    "globals", "locals", "vars", "breakpoint", "input", "help", "memoryview",
    "get_type_hints", "ForwardRef", "__builtins__",
}
#: Attributes refused on any object: the same escape hatches reached through an object, the frame and code
#: objects behind generators and tracebacks, and str.format (whose fields can walk attributes).
_BANNED_ATTRS = {
    "getattr", "setattr", "delattr", "eval", "exec", "compile", "globals", "locals", "vars",
    "get_type_hints", "ForwardRef", "mro", "format", "format_map",
    "gi_frame", "gi_code", "gi_yieldfrom", "cr_frame", "cr_code", "cr_await", "ag_frame", "ag_code",
    "f_back", "f_globals", "f_locals", "f_builtins", "f_code", "tb_frame", "tb_next", "co_code",
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


def allowed_import_lines() -> list[str]:
    """The only imports a generated strategy may use, in prompt order."""
    lines = [
        "from __future__ import annotations",
        f"from typing import <any of: {', '.join(TYPING_NAMES)}>",
        "import math (or from math import <names>)",
    ]
    for module, names in ALLOWED.items():
        lines.append(f"from {module} import {', '.join(names)}")
    return lines


def _super_init_call(node: ast.Attribute) -> bool:
    """The attribute of exactly super().__init__(...). super() takes no arguments."""
    if node.attr != "__init__":
        return False
    parent = getattr(node, "_parent", None)
    if not isinstance(parent, ast.Call) or parent.func is not node:
        return False
    value = node.value
    if not isinstance(value, ast.Call):
        return False
    func = value.func
    if not isinstance(func, ast.Name) or func.id != "super":
        return False
    return not value.args and not value.keywords


class _Gate(ast.NodeVisitor):
    """One check for Convert and Approve. Defining __init__ is allowed.

    super().__init__(...) is allowed only with no arguments to super(), and
    only inside a method named __init__. Every other dunder attribute is banned.
    """

    def __init__(self, source: str) -> None:
        self.source = source
        self.functions: list[str] = []

    def visit(self, node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            setattr(child, "_parent", node)
        super().visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._annotation(node.returns)
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.functions.append("<lambda>")
        self.generic_visit(node)
        self.functions.pop()

    def visit_Import(self, node: ast.Import) -> None:
        if [(alias.name, alias.asname) for alias in node.names] == [("math", None)]:
            return
        raise SandboxError(_remove(self.source, node))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        names = [alias.name for alias in node.names]
        if module == "__future__":
            plain = names == ["annotations"] and all(alias.asname is None for alias in node.names)
            if plain:
                return
            text = _text(self.source, node.lineno)
            if "annotations" in names:
                raise SandboxError(
                    f"line {node.lineno}: change {text!r} to 'from __future__ import annotations'"
                )
            raise SandboxError(f"remove line {node.lineno}: {text}")
        if node.level:
            raise SandboxError(_remove(self.source, node))
        if module == "typing":
            if names and all(alias.name in TYPING_NAMES for alias in node.names):
                return
            raise SandboxError(_remove(self.source, node))
        if module == "math":
            if names and all(alias.name != "*" and alias.name.isidentifier() for alias in node.names):
                return
            raise SandboxError(_remove(self.source, node))
        allowed = ALLOWED.get(module)
        if allowed is None or any(alias.name == "*" or alias.name not in allowed for alias in node.names):
            raise SandboxError(_remove(self.source, node))

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in _BANNED_NAMES:
            raise SandboxError(_remove(self.source, node))
        self.generic_visit(node)

    def visit_arg(self, node: ast.arg) -> None:
        if node.arg in _BANNED_NAMES:
            raise SandboxError(_remove(self.source, node))
        self._annotation(node.annotation)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._annotation(node.annotation)
        self.generic_visit(node)

    def _annotation(self, node: ast.expr | None) -> None:
        """A type hint written as a string is code that typing can evaluate later: refused."""
        if node is None:
            return
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                raise SandboxError(f"line {sub.lineno}: write the type hint without quotes: {_text(self.source, sub.lineno)}")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in _BANNED_ATTRS:
            raise SandboxError(_remove(self.source, node))
        if node.attr.startswith("__"):
            inside_init = bool(self.functions) and self.functions[-1] == "__init__"
            if not (inside_init and _super_init_call(node)):
                raise SandboxError(_remove(self.source, node))
        self.generic_visit(node)


def check_source(source: str) -> None:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise SandboxError(f"line {exc.lineno}: syntax error: {exc.msg}") from exc
    _Gate(source).visit(tree)
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
