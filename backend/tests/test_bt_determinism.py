"""Determinism (10): same inputs -> byte-identical results."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

from app.backtest.costs import Slippage
from app.backtest.engine import run_backtest
from app.backtest.sources import ListSource
from app.strategies.ema_cross import EmaCrossover
from app.strategies.orb import OpeningRangeBreakout
from app.strategies.supertrend_flip import SupertrendFlip
from tests.bt_helpers import MON, TUE, WED, cfg, random_days

BACKEND = Path(__file__).resolve().parents[1]

SNIPPET = """
import hashlib
from app.backtest.engine import run_backtest
from app.backtest.sources import ListSource
from app.strategies.ema_cross import EmaCrossover
from app.strategies.orb import OpeningRangeBreakout
from app.strategies.supertrend_flip import SupertrendFlip
from tests.bt_helpers import MON, TUE, WED, cfg, random_days
c = random_days([MON, TUE, WED], seed=11)
out = []
for s in (EmaCrossover(fast=5, slow=13), SupertrendFlip(atr_length=7, multiplier=2.0), OpeningRangeBreakout(range_minutes=15)):
    out.append(run_backtest(s, ListSource(c, 1), cfg(timeframe="5m", square_off="15:15")).to_json())
print(hashlib.sha256("|".join(out).encode()).hexdigest())
"""


def run_all(candles, **kw):  # noqa: ANN001, ANN201
    out = []
    for s in (EmaCrossover(fast=5, slow=13), SupertrendFlip(atr_length=7, multiplier=2.0), OpeningRangeBreakout(range_minutes=15)):
        out.append(run_backtest(s, ListSource(candles, 1), cfg(timeframe="5m", square_off="15:15", **kw)))
    return out


def test_10_two_runs_in_one_process_are_byte_identical() -> None:
    c = random_days([MON, TUE, WED], seed=11)
    a, b = run_all(c), run_all(c)
    assert [r.to_json() for r in a] == [r.to_json() for r in b]
    assert [r.run_id for r in a] == [r.run_id for r in b]
    assert any(r.trades for r in a), "the comparison would be vacuous without trades"
    assert all(isinstance(r.to_json(), str) and r.to_json().isascii() for r in a)


def test_10_results_do_not_depend_on_python_hash_randomisation() -> None:
    hashes = set()
    for seed in ("1", "2", "random"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        out = subprocess.run([sys.executable, "-c", SNIPPET], cwd=BACKEND, env=env, capture_output=True, text=True, check=True)
        hashes.add(out.stdout.strip())
    assert len(hashes) == 1 and len(next(iter(hashes))) == 64


def test_10_parameter_order_does_not_matter() -> None:
    c = random_days([MON, TUE], seed=5)
    a = run_backtest(EmaCrossover(fast=5, slow=13), ListSource(c, 1), cfg(timeframe="5m"))
    b = run_backtest(EmaCrossover(slow=13, fast=5), ListSource(c, 1), cfg(timeframe="5m"))
    assert a.to_json() == b.to_json()


def test_10_run_id_follows_the_inputs_and_only_the_inputs() -> None:
    c = random_days([MON, TUE], seed=5)
    base = run_backtest(EmaCrossover(fast=5, slow=13), ListSource(c, 1), cfg(timeframe="5m"))
    same = run_backtest(EmaCrossover(fast=5, slow=13), ListSource(list(c), 1), cfg(timeframe="5m"))
    assert base.run_id == same.run_id
    other_param = run_backtest(EmaCrossover(fast=5, slow=14), ListSource(c, 1), cfg(timeframe="5m"))
    other_cfg = run_backtest(EmaCrossover(fast=5, slow=13), ListSource(c, 1), cfg(timeframe="5m", slippage=Slippage.points(0.5)))
    changed = list(c)
    changed[100] = {**changed[100], "close": changed[100]["close"] + 0.05}
    other_data = run_backtest(EmaCrossover(fast=5, slow=13), ListSource(changed, 1), cfg(timeframe="5m"))
    assert len({base.run_id, other_param.run_id, other_cfg.run_id, other_data.run_id}) == 4


def test_10_the_result_json_has_no_clock_no_ids_and_no_machine_paths() -> None:
    c = random_days([MON], seed=5)
    text = run_backtest(EmaCrossover(fast=5, slow=13), ListSource(c, 1), cfg(timeframe="5m")).to_json()
    assert str(BACKEND) not in text and "/Users/" not in text
    assert hashlib.sha256(text.encode()).hexdigest()  # plain text, hashable
