"""Tests must never mutate the production data/ tree."""

from __future__ import annotations

import pytest

from app.backtest.holdout import load_holdout
from app.backtest.runs import RunStore
from app.config import settings
from tests.conftest import RealDataWriteError


def test_runstore_refuses_to_write_the_real_backtests_sqlite() -> None:
    store = RunStore(settings.data_dir / "backtests.sqlite")  # open/read still allowed
    start, end = load_holdout()
    with pytest.raises(RealDataWriteError, match="must not write to the real data"):
        store.accept_holdout(start.isoformat(), end.isoformat(), note="should fail")


def test_runstore_allows_writes_on_tmp_path(tmp_path) -> None:  # noqa: ANN001
    store = RunStore(tmp_path / "backtests.sqlite")
    start, end = load_holdout()
    assert store.accept_holdout(start.isoformat(), end.isoformat(), note="fixture") == 1
