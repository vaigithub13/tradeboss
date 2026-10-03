"""The commit that produced a run. A dirty tree cannot be reproduced."""

from __future__ import annotations

import subprocess

from app.backtest.runs import GitState
from app.config import ROOT_DIR


def read_git_state() -> GitState:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT_DIR, text=True, stderr=subprocess.DEVNULL,
        ).strip()
        porcelain = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=ROOT_DIR, text=True, stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return GitState(commit="unknown", dirty=True)
    return GitState(commit=commit or "unknown", dirty=bool(porcelain.strip()))
