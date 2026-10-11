"""C1 (review 2026-10-11): model-written test code is never executed.

A draft is checked only by our own checks: the AST gate, compile, the no-look-ahead and determinism runs,
and the smoke backtest in the worker. A `tests` field in the model's reply is ignored.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.pine.convert import conversion_prompt, convert_draft
from app.pine.scanner import scan

GOOD = '''
from app.backtest.contracts import Signal, Strategy

class AlwaysBuy(Strategy):
    name = "always_buy"

    def on_bar(self, bar, ctx):
        if len(ctx.bars) == 1:
            return [Signal("BUY", 1, tag="LE")]
        return []
'''

SCRIPT = 'strategy("x")\nstrategy.entry("LE", strategy.long)\n'


class _Fake:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


def test_conversion_never_runs_generated_tests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    marker = tmp_path / "pwned"
    tests = (
        "import pathlib\n"
        f"pathlib.Path(r'{marker}').write_text('ran')\n"
        "def test_ok():\n"
        "    assert True\n"
    )

    def no_run(*args: object, **kwargs: object) -> None:
        raise AssertionError("conversion must not start a test process")

    monkeypatch.setattr(subprocess, "run", no_run)
    result = convert_draft(SCRIPT, client=_Fake(json.dumps({"python": GOOD, "tests": tests})),
                           report={"scan": scan(SCRIPT)})
    assert result["ready"] is True
    assert not marker.exists()
    assert "tests" not in result


def test_the_conversion_prompt_asks_for_python_only() -> None:
    prompt = conversion_prompt(SCRIPT, {"scan": scan(SCRIPT)})
    assert "pytest" not in prompt
    assert "keys python and tests" not in prompt
    assert "one key, python" in prompt


def test_a_reply_without_tests_is_ready() -> None:
    result = convert_draft(SCRIPT, client=_Fake(json.dumps({"python": GOOD})), report={"scan": scan(SCRIPT)})
    assert result["ready"] is True
    assert result["errors"] == []
