"""The capped option list: paper's held contracts first, then each slot's ATM pair, then the recorder's set.
A key the cap refuses is logged (once per refused set)."""

from __future__ import annotations

import logging
from types import SimpleNamespace

from app.data.store import CandleStore
from app.live.service import LiveConfig, LiveService
from app.live.spreads.keys import OPTION_CAP, compose_keys, order_option_keys
from app.upstox.instruments import NIFTY_INDEX_KEY, VIX_KEY


def test_the_option_cap_is_40() -> None:
    assert OPTION_CAP == 40


def test_held_contracts_come_first_then_the_atm_pairs_then_the_recorder() -> None:
    held = {"NSE_FO|9"}
    atm = {"NSE_FO|5", "NSE_FO|9", "NSE_FO|1"}
    spread = ["NSE_FO|7", "NSE_FO|1", "NSE_FO|3"]
    assert order_option_keys(held, atm, spread) == ["NSE_FO|9", "NSE_FO|1", "NSE_FO|5", "NSE_FO|7", "NSE_FO|3"]


def test_the_cap_refuses_the_recorder_before_any_paper_contract() -> None:
    held = {f"NSE_FO|H{i}" for i in range(4)}
    atm = {f"NSE_FO|A{i}" for i in range(30)}
    spread = [f"NSE_FO|S{i}" for i in range(10)]
    keys = compose_keys([NIFTY_INDEX_KEY], order_option_keys(held, atm, spread), enabled=True)
    assert held <= set(keys) and atm <= set(keys)
    assert [k for k in keys if k.startswith("NSE_FO|S")] == spread[:6]


def service(tmp_path) -> LiveService:
    cfg = LiveConfig(candles_dir=tmp_path / "candles", recordings_dir=tmp_path / "rec", state_dir=tmp_path / "state",
                     instruments_dir=tmp_path / "inst", spread_recorder_enabled=False, spreads_dir=tmp_path / "spreads")
    return LiveService(cfg, CandleStore(tmp_path / "candles"), client_factory=lambda: None,
                       authorize=lambda: "wss://example.invalid")


def test_the_service_orders_paper_tiers_and_warns_once_per_refused_set(tmp_path, caplog) -> None:
    svc = service(tmp_path)
    tiers = {"held": {"NSE_FO|H0"}, "atm": {f"NSE_FO|A{i:02d}" for i in range(45)}}
    svc.paper = SimpleNamespace(held_keys=lambda: tiers["held"], atm_keys=lambda: tiers["atm"])
    with caplog.at_level(logging.WARNING, logger="tradeboss.live.service"):
        keys = svc.subscription_keys()
        assert keys[:2] == [NIFTY_INDEX_KEY, VIX_KEY] and keys[2] == "NSE_FO|H0" and len(keys) == 2 + OPTION_CAP
        refused = [r for r in caplog.records if "refused" in r.getMessage()]
        assert len(refused) == 1 and "6 option keys" in refused[0].getMessage()
        assert "NSE_FO|A44" in refused[0].getMessage()
        svc.subscription_keys()
        assert len([r for r in caplog.records if "refused" in r.getMessage()]) == 1  # the same set: not again
        tiers["atm"] = {f"NSE_FO|A{i:02d}" for i in range(46)}
        svc.subscription_keys()
        assert len([r for r in caplog.records if "refused" in r.getMessage()]) == 2  # a new set is logged
