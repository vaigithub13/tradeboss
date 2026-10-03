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
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.functions.append("<lambda>")
        self.generic_visit(node)
        self.functions.pop()

    def visit_Import(self, node: ast.Import) -> None:
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
        allowed = ALLOWED.get(module)
        if allowed is None or any(alias.name == "*" or alias.name not in allowed for alias in node.names):
            raise SandboxError(_remove(self.source, node))

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name) and func.id in _BANNED_CALLS:
            raise SandboxError(_remove(self.source, node))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
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
