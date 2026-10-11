"""C2 (review 2026-10-11): defence in depth for user strategies.

Four layers, each tested on its own:
1. the AST gate bans dangerous names anywhere (not only direct calls), string annotations, the typing names
   that evaluate strings, and the attributes that reach frames;
2. the worker executes the module with a restricted builtins dict;
3. the worker's import hook allows only contracts, pine_common, typing, __future__ and math, and app.config
   cannot be imported in the worker at all;
4. the worker runs under a macOS sandbox-exec profile: no network, no process, no file reads or writes outside
   its own temp dir (plus the read-only Python runtime and app code).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from app.config import ROOT_DIR
from app.pine import isolated
from app.pine.isolated import IsolatedStrategy, WorkerError, sandbox_profile, sandboxed_command
from app.pine.sandbox import SandboxError, check_source
from app.pine.worker import exec_strategy_module

BASE = "from __future__ import annotations\nfrom app.backtest.contracts import Signal, Strategy\n"
GOOD = BASE + (
    "class AlwaysBuy(Strategy):\n"
    "    name = 'always_buy'\n"
    "    def on_bar(self, bar, ctx):\n"
    "        if len(ctx.bars) == 1:\n"
    "            return [Signal('BUY', 1, tag='LE')]\n"
    "        return []\n"
)
darwin_only = pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec is macOS only")


def _strategy(body: str, head: str = "") -> str:
    return BASE + head + "class S(Strategy):\n    def on_bar(self, bar, ctx):\n" + body + "        return []\n"


# ---------------------------------------------------------------- 1. AST gate
THE_REVIEW_BYPASSES = {
    "aliased open": BASE + "f = open\n" + "class S(Strategy):\n    def on_bar(self, bar, ctx):\n        f('x')\n        return []\n",
    "aliased getattr": BASE + "g = getattr\n" + "class S(Strategy):\n    def on_bar(self, bar, ctx):\n        return g(ctx, '_' + '_class__')\n",
    "get_type_hints on a string annotation": BASE + "from typing import get_type_hints\n"
    "def h(x: 'SOMETHING') -> None: ...\n"
    "class S(Strategy):\n    def on_bar(self, bar, ctx):\n        get_type_hints(h)\n        return []\n",
}


@pytest.mark.parametrize("name", list(THE_REVIEW_BYPASSES))
def test_the_three_bypasses_from_the_review_are_refused(name: str) -> None:
    with pytest.raises(SandboxError):
        check_source(THE_REVIEW_BYPASSES[name])


@pytest.mark.parametrize("banned", ["open", "getattr", "setattr", "delattr", "eval", "exec", "compile",
                                    "globals", "locals", "vars", "breakpoint", "input"])
def test_a_dangerous_name_is_refused_anywhere_not_only_when_called(banned: str) -> None:
    with pytest.raises(SandboxError):
        check_source(_strategy(f"        x = {banned}\n"))
    with pytest.raises(SandboxError):
        check_source(BASE + f"keep = [{banned}]\n" + _strategy("").removeprefix(BASE))


def test_string_annotations_and_the_typing_names_that_evaluate_them_are_refused() -> None:
    for source in (
        BASE + "def h(x: 'int') -> None: ...\n" + _strategy("").removeprefix(BASE),
        BASE + "def h(x) -> 'int': ...\n" + _strategy("").removeprefix(BASE),
        BASE + "y: 'int' = 1\n" + _strategy("").removeprefix(BASE),
        _strategy("", head="from typing import ForwardRef\n"),
        _strategy("", head="from typing import get_type_hints\n"),
        _strategy("", head="from typing import sys\n"),  # typing re-exports its own imports
    ):
        with pytest.raises(SandboxError):
            check_source(source)


def test_frame_and_format_attributes_are_refused() -> None:
    for attr in ("gi_frame", "f_back", "f_globals", "tb_frame", "cr_frame", "co_code", "format", "format_map", "mro"):
        with pytest.raises(SandboxError):
            check_source(_strategy(f"        x = ctx.{attr}\n"))


def test_normal_strategy_code_still_passes() -> None:
    check_source(GOOD)
    check_source(_strategy("        x = ctx.bars.open\n        y = ctx.bars.close[-1]\n", head="import math\n"))
    check_source(_strategy("        x = sqrt(4.0)\n", head="from math import sqrt\n"))
    check_source(_strategy("        x: Optional[int] = None\n", head="from typing import Any, Optional\n"))
    check_source((ROOT_DIR / "backend/app/strategies/user/price_channel_strategy.py").read_text())


# ---------------------------------------------------------------- 2 and 3. builtins and imports in the worker
def _run(source: str) -> object:
    return exec_strategy_module(source, filename="<test>", module_name="user_test")


@pytest.mark.parametrize("name", ["open", "getattr", "setattr", "eval", "exec", "compile", "globals", "vars",
                                  "type", "breakpoint", "input"])
def test_the_worker_builtins_do_not_hold_dangerous_names(name: str) -> None:
    module = _run(f"def probe():\n    return {name}\n")
    with pytest.raises(NameError):
        module.probe()  # type: ignore[attr-defined]


def test_the_worker_dunder_import_is_the_allow_list_hook() -> None:
    module = _run("def probe():\n    return __import__('os')\n")
    with pytest.raises(ImportError):
        module.probe()  # type: ignore[attr-defined]


@pytest.mark.parametrize("statement", [
    "import os",
    "import sys",
    "import app.config",
    "from app.config import settings",
    "from app.backtest.contracts import LookAheadError",
    "from app.backtest import engine",
    "from typing import sys",
    "from typing import get_type_hints",
    "import typing",
    "from . import x",
])
def test_the_worker_import_hook_refuses_everything_off_the_list(statement: str) -> None:
    with pytest.raises(ImportError):
        _run(statement + "\n")


def test_the_c_level_import_form_returns_no_module() -> None:
    """numpy's C code imports through the running frame's __import__ with fromlist=[]; nothing comes back."""
    module = _run("def probe(name):\n    return __import__(name, None, None, [], 0)\n")
    assert module.probe("os") is None  # type: ignore[attr-defined]
    with pytest.raises(ImportError):
        module.probe("app.config")  # type: ignore[attr-defined]


def test_numpy_works_inside_a_strategy_call() -> None:
    import numpy as np

    module = _run("def probe(values):\n    return float(values.mean()) + float(max(values[-2:]))\n")
    assert module.probe(np.array([1.0, 2.0, 3.0])) == 5.0  # type: ignore[attr-defined]


def test_the_worker_import_hook_allows_the_list() -> None:
    module = _run(
        "from __future__ import annotations\n"
        "import math\n"
        "from math import sqrt\n"
        "from typing import Any, Optional\n"
        "from app.backtest.contracts import Signal, Strategy\n"
        "from app.strategies.pine_common import PinePort, entry_window, minute_of, TICK, timeframe_minutes\n"
        "class S(Strategy):\n"
        "    def __init__(self, n: int = 1) -> None:\n"
        "        super().__init__(n=n)\n"
        "    def on_bar(self, bar: Any, ctx: Any) -> list:\n"
        "        return [Signal('BUY', 1)] if math.isfinite(sqrt(4.0)) else []\n"
    )
    strategy = module.S(n=2)  # type: ignore[attr-defined]
    assert strategy.params == {"n": 2}
    assert strategy.on_bar({}, None)[0].side == "BUY"


def test_the_worker_process_cannot_import_app_config(tmp_path: Path) -> None:
    path = tmp_path / "always_buy.py"
    path.write_text(GOOD)
    strategy = IsolatedStrategy(path)
    try:
        assert strategy.worker_info["config_loaded"] is False
        assert strategy.worker_info["config_importable"] is False
    finally:
        strategy.close()


def test_the_worker_environment_is_an_allow_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("SOME_OTHER_SECRET", "x")
    env = isolated.worker_env(tmp_path)
    assert "OPENAI_API_KEY" not in env and "SOME_OTHER_SECRET" not in env
    assert set(env) <= {"PATH", "PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "PYTHONNOUSERSITE", "PYTHONSAFEPATH",
                        "HOME", "TMPDIR", "LANG", "LC_ALL"}


# ---------------------------------------------------------------- 4. sandbox-exec
PROBE = r'''
import socket, subprocess, sys
outside, project = sys.argv[1], sys.argv[2]
def attempt(name, fn):
    try:
        fn()
        print(name, "ALLOWED")
    except PermissionError:
        print(name, "denied")
    except Exception as exc:
        print(name, "other", type(exc).__name__)
attempt("read_outside", lambda: open(outside + "/.env").read())
attempt("read_project", lambda: open(project).read())
attempt("write_outside", lambda: open(outside + "/written.txt", "w").write("x"))
attempt("network", lambda: socket.create_connection(("127.0.0.1", 9), timeout=2))
attempt("process", lambda: subprocess.run(["/bin/echo", "hi"], capture_output=True))
attempt("write_inside", lambda: open("inside.txt", "w").write("x"))
'''


@darwin_only
def test_the_sandbox_denies_network_processes_and_files_outside_its_temp_dir(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / ".env").write_text("NOT_A_REAL_SECRET=1\n")
    done = subprocess.run(
        sandboxed_command(work, ["-c", PROBE, str(outside), str(ROOT_DIR / "backend" / "pyproject.toml")]),
        cwd=work, env=isolated.worker_env(work), capture_output=True, text=True, timeout=60,
    )
    seen = dict(line.split(" ", 1) for line in done.stdout.split("\n") if line)
    assert seen == {
        "read_outside": "denied",
        "read_project": "denied",
        "write_outside": "denied",
        "network": "denied",  # not "refused": the connection is never attempted
        "process": "denied",
        "write_inside": "ALLOWED",
    }, done.stderr[-2000:]
    assert not (outside / "written.txt").exists()


@darwin_only
def test_the_profile_never_allows_the_repo_root_env_or_data(tmp_path: Path) -> None:
    profile = sandbox_profile(tmp_path)
    root = str(ROOT_DIR.resolve())
    assert "(deny default)" in profile
    assert "network" not in profile.replace("(deny default)", "")
    for forbidden in (f'(subpath "{root}")', f'"{root}/.env"', f'"{root}/data'):
        assert forbidden not in profile


@darwin_only
def test_the_worker_runs_under_sandbox_exec(tmp_path: Path) -> None:
    path = tmp_path / "always_buy.py"
    path.write_text(GOOD)
    strategy = IsolatedStrategy(path)
    try:
        assert strategy.command[0] == "/usr/bin/sandbox-exec"
        assert strategy.worker_info["sandboxed"] is True
    finally:
        strategy.close()


def test_without_sandbox_exec_a_user_strategy_does_not_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "always_buy.py"
    path.write_text(GOOD)
    monkeypatch.setattr(isolated, "SANDBOX_EXEC", tmp_path / "missing-sandbox-exec")
    with pytest.raises(WorkerError, match="sandbox"):
        IsolatedStrategy(path)
