"""Learn signal files stay under data/learn/signals and do not wipe history."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.learn_signals import (
    apply_rows,
    load_summary,
    review_due,
    review_wanted,
    run_book,
    save_summary,
    signals_dir,
)

IST = ZoneInfo("Asia/Kolkata")


def test_review_is_due_at_1600_on_a_weekday():
    assert review_due(datetime(2026, 11, 27, 16, 0, tzinfo=IST))
    assert not review_due(datetime(2026, 11, 27, 15, 59, tzinfo=IST))
    assert not review_due(datetime(2026, 11, 28, 16, 30, tzinfo=IST))
    assert not review_wanted()


def test_a_later_review_refreshes_the_row_and_keeps_history(tmp_path):
    save_summary(tmp_path, {
        "live": {"since": None, "bySymbol": {}},
        "history": {"label": "history (no costs)", "from": "2022-01-01", "to": "2026-11-27", "bySymbol": {}},
    })
    row = {
        "id": "SWP-NIFTY50-1", "symbol": "NIFTY50", "setup": "SWP", "side": "buy",
        "time": int(datetime(2026, 11, 27, 10, 25, tzinfo=IST).timestamp()),
        "entry": 100, "sl": 90, "tp": 120, "risk": 10,
        "tpNote": "2R", "why": "Sweep of PDL", "status": "open", "exitTime": None, "exit": None, "r": None,
    }
    apply_rows(tmp_path, [row], datetime(2026, 11, 27, tzinfo=IST).date())
    row["status"] = "SL"
    row["r"] = -1
    summary = apply_rows(tmp_path, [row], datetime(2026, 11, 27, tzinfo=IST).date())
    saved = load_summary(tmp_path)
    assert saved["history"]["label"] == "history (no costs)"
    assert summary["live"]["bySymbol"]["NIFTY50"]["all"]["sl"] == 1
    assert summary["live"]["bySymbol"]["NIFTY50"]["all"]["signals"] == 1
    text = (signals_dir(tmp_path) / "signals.csv").read_text(encoding="utf-8")
    assert text.count("SWP-NIFTY50-1") == 1
    note = (signals_dir(tmp_path) / "daily" / "2026-11-27.md").read_text(encoding="utf-8")
    assert "Watch only" in note
    assert "Sweep of PDL" in note


def test_engine_reads_closed_bars_only():
    start = int(datetime(2026, 11, 26, 10, 0, tzinfo=IST).timestamp())
    bars = [
        {"time": start + i * 300, "open": 100, "high": 101, "low": 99, "close": 100}
        for i in range(3)
    ]
    book = run_book(bars, bars[-1]["time"] + 300, "NIFTY50")
    assert book["signals"] == []
    assert "plans" in book


def _row(setup: str, status: str, r: float | None) -> dict[str, object]:
    return {"id": f"{setup}-{status}", "symbol": "NIFTY50", "setup": setup, "status": status, "r": r, "star": ""}


def test_stats_book_splits_crt_75m_and_daily():
    from app.learn_signals import stats_book

    book = stats_book([_row("CRT75", "TP", 2.5), _row("CRT75", "SL", -1), _row("CRTD", "TP", 3), _row("SWP", "SL", -1)])
    assert book["CRT75"]["signals"] == 2
    assert book["CRT75"]["winRate"] == 50
    assert book["CRTD"]["signals"] == 1
    assert book["CRT"]["signals"] == 3
    assert book["CRT"]["totalR"] == 4.5
    assert book["all"]["signals"] == 4


def test_history_keeps_the_crt_rows_from_the_engine(tmp_path, monkeypatch):
    import app.learn_signals as ls

    start = int(datetime(2026, 11, 26, 9, 15, tzinfo=IST).timestamp())
    monkeypatch.setattr(ls, "load_m5", lambda *_: [{"time": start + i * 300, "open": 1, "high": 1, "low": 1, "close": 1} for i in range(5)])
    stats = {"signals": 7, "tp": 3, "sl": 4, "expired": 0, "winRate": 42.86, "totalR": 1.2, "avgR": 0.17, "avgRNet": 0.1}
    monkeypatch.setattr(ls, "run_book", lambda *_: {"stats": {"CRT75": stats, "CRTD": stats, "CRT": stats}, "breakdown": []})
    summary = ls.write_history(tmp_path, tmp_path, datetime(2022, 1, 1).date(), datetime(2026, 11, 27).date())
    nifty = summary["history"]["bySymbol"]["NIFTY50"]
    for key in ("CRT75", "CRTD", "CRT"):
        assert nifty[key]["signals"] == 7
        assert nifty[key]["avgRNet"] == 0.1
