"""A thin stock borrows the universe's history for the same setup."""

from datetime import date

from app.scanner.config import SETUP_IDS, ScannerSettings
from app.scanner.history import history_for
from app.scanner.judge import judge
from app.scanner.runner import evaluate_symbol, fresh_state
from tests.scanner_bars import at, bar, flat_session

SIGNAL = date(2026, 1, 8)  # Thursday
FIRE_DAYS = (date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7))
QUIET = date(2026, 1, 2)


def _settings() -> ScannerSettings:
    return ScannerSettings(setups={name: name == "orb15" for name in SETUP_IDS}, tm_paths=20, tm_seed=1)


def _orb_day(day: date) -> list[dict]:
    bars = [
        bar(day, 9, 15, 100.2, 101.0, 100.0, 100.4, 1000),
        bar(day, 9, 20, 100.4, 100.8, 100.1, 100.5, 1000),
    ]
    price = 101.6
    hour, minute = 9, 30
    first = True
    while (hour, minute) <= (15, 10):
        if first:
            bars.append(bar(day, hour, minute, 100.8, 101.8, 100.6, 101.6, 4000))
            first = False
        else:
            price = min(price + 0.5, 110.0)
            bars.append(bar(day, hour, minute, price - 0.1, price + 0.2, price - 0.2, price, 1000))
        minute += 5
        if minute >= 60:
            hour += 1
            minute -= 60
    return bars


def _tape(days: tuple[date, ...], *, include_signal: bool) -> list[dict]:
    bars = flat_session(QUIET)
    for day in days:
        bars.extend(_orb_day(day))
    if include_signal:
        bars.extend(_orb_day(SIGNAL))
    return bars


def _good_tm(_spec: dict) -> dict:
    return {
        "target_pct": 70.0,
        "stop_pct": 20.0,
        "open_pct": 10.0,
        "avg_rupees": 100.0,
        "paths": 20,
        "seed": 1,
        "histogram": {"target": 14, "stop": 4, "open": 2},
    }


def test_three_fires_borrow_the_universe_and_can_be_good() -> None:
    settings = _settings()
    universe = {
        "THIN": _tape(FIRE_DAYS, include_signal=True),
        "PEER1": _tape(FIRE_DAYS, include_signal=False),
        "PEER2": _tape(FIRE_DAYS, include_signal=False),
    }
    own, _source = history_for("THIN", {"THIN": universe["THIN"]}, setup="orb15", up_to=SIGNAL, settings=settings)
    assert own.fires == 3

    pooled, source = history_for("THIN", universe, setup="orb15", up_to=SIGNAL, settings=settings)
    assert source == "pooled"
    assert pooled.fires >= 8
    verdict = judge(
        target_pct=70,
        stop_pct=20,
        win_rate=pooled.win_rate,
        fires=pooled.fires,
        settings=settings,
    )
    assert verdict == "GOOD"

    rows = evaluate_symbol(
        symbol="THIN",
        sector="Test",
        bars=universe["THIN"],
        day=SIGNAL,
        as_of=at(SIGNAL, 10, 0),
        settings=settings,
        nifty_pct=None,
        proven=None,
        open_symbols=set(),
        open_count=0,
        daily_loss=0,
        trades_today=0,
        universe=universe,
        tm=_good_tm,
    )
    live = [row for row in rows if row["setup"] == "orb15" and not row["forming"]]
    assert live
    assert live[0]["history_source"] == "pooled"
    assert live[0]["fires"] >= 8
    assert live[0]["verdict"] == "GOOD"
    assert fresh_state(SIGNAL)["open"] == []
