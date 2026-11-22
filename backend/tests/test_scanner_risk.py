"""Each BLOCK reason, and the news line that does not block by itself."""

from app.scanner.config import ScannerSettings
from app.scanner.models import Plan
from app.scanner.risk import assess

SETTINGS = ScannerSettings()


def _plan(**overrides) -> Plan:
    base = dict(
        instrument="equity_intraday",
        symbol="RELIANCE",
        setup="orb15",
        side="long",
        status="triggered",
        entry=100.0,
        stop=99.0,
        target=102.0,
        qty=10,
        risk_per_share=1.0,
        rupee_risk=10.0,
        rupee_reward=20.0,
        capital=1000.0,
        rr=2.0,
        resistance=None,
        support=None,
        entry_time=1_736_000_000,  # overwritten per test when the clock matters
        target_r=2.0,
    )
    base.update(overrides)
    return Plan(**base)


def _reasons(**kwargs) -> list[str]:
    result = assess(_plan(**kwargs.get("plan", {})), verdict=kwargs.get("verdict", "GOOD"), settings=SETTINGS, daily_loss=0, trades_today=0, open_symbols=set())
    extra = {k: v for k, v in kwargs.items() if k not in ("plan", "verdict")}
    if extra or "plan" in kwargs:
        result = assess(
            _plan(**kwargs.get("plan", {})),
            verdict=kwargs.get("verdict", "GOOD"),
            settings=SETTINGS,
            daily_loss=kwargs.get("daily_loss", 0),
            trades_today=kwargs.get("trades_today", 0),
            open_symbols=kwargs.get("open_symbols", set()),
            event_today=kwargs.get("event_today"),
        )
    return list(result["reasons"])


def test_each_block_reason() -> None:
    assert any("quantity" in r for r in _reasons(plan={"qty": 0}))
    assert any("max loss" in r for r in _reasons(plan={"rupee_risk": 800}))
    assert any("1:2" in r for r in _reasons(plan={"rr": 1.5}))
    assert any("daily loss" in r for r in _reasons(daily_loss=-1500))
    assert any("max trades" in r for r in _reasons(trades_today=3))
    late = 14 * 60 + 30
    # minute_of reads the timestamp; build one at 14:30 IST
    from datetime import datetime
    from app.scanner.bars import IST

    at_cutoff = int(datetime(2026, 1, 5, 14, 30, tzinfo=IST).timestamp())
    assert any("14:30" in r for r in _reasons(plan={"entry_time": at_cutoff}))
    assert late >= 14 * 60 + 30
    assert any("capital" in r for r in _reasons(plan={"capital": 60_000}))
    assert any("already" in r for r in _reasons(open_symbols={"RELIANCE"}))
    assert any("time machine" in r for r in _reasons(verdict="COIN_FLIP"))
    assert any("event" in r for r in _reasons(event_today=True))


def test_missing_news_does_not_block() -> None:
    result = assess(_plan(entry_time=int(__import__("datetime").datetime(2026, 1, 5, 10, 0, tzinfo=__import__("app.scanner.bars", fromlist=["IST"]).IST).timestamp())), verdict="GOOD", settings=SETTINGS, daily_loss=0, trades_today=0, open_symbols=set(), event_today=None)
    assert result["decision"] == "PASS"
    assert result["news"] == "check news"
