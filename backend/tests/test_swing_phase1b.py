"""Swing Phase 1b: bhavcopy parse, lineages, adjustments, point-in-time filters."""

from __future__ import annotations

import io
import zipfile
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from app.swing.bhav_adjust import (
    apply_adjustments, assign_lineages, capital_change, compare_closes,
    one_row_per_day, parse_actions, price_factor,
)
from app.swing.bhav_pit import LISTED_SESSIONS, apply_pit, market_hours_block, summarize_years
from app.swing.bhavcopy import archive_urls, classify_body, collapse_series, coverage_complete, parse_bhav_zip
from scripts.swing_phase1b import refuse_if_live

IST = timezone(timedelta(hours=5, minutes=30))


def _zip(csv: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("bhav.csv", csv)
    return buffer.getvalue()


def _row(day: date, symbol: str, isin: str, close: float, prev: float, turnover: float = 1e8, series: str = "EQ") -> dict:
    return {
        "date": day, "symbol": symbol, "series": series, "isin": isin,
        "open": close, "high": close, "low": close, "close": close,
        "prev_close": prev, "volume": 1000.0, "turnover": turnover,
    }


def _finish(rows: list[dict], actions: list[dict] | None = None, covered: set | None = None):
    frame = one_row_per_day(assign_lineages(collapse_series(pd.DataFrame(rows))))
    adjusted, ledger = apply_adjustments(frame, actions or [], covered or set())
    return apply_pit(adjusted), ledger


def test_archive_urls_switch_on_the_udiff_date():
    early = archive_urls(date(2015, 1, 2))
    assert len(early) == 1
    assert early[0].endswith("/2015/JAN/cm02JAN2015bhav.csv.zip")
    both = archive_urls(date(2024, 7, 5))
    assert both[0].endswith("cm05JUL2024bhav.csv.zip")
    assert "BhavCopy_NSE_CM_0_0_0_20240705" in both[1]
    opened = archive_urls(date(2024, 7, 8))
    assert opened[0].endswith("BhavCopy_NSE_CM_0_0_0_20240708_F_0000.csv.zip")
    assert "cm08JUL2024bhav.csv.zip" in opened[1]
    assert len(archive_urls(date(2024, 7, 12))) == 1


def test_parser_keeps_eq_and_be_from_both_schemas():
    legacy = _zip(
        "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN\n"
        "RELIANCE,EQ,823,832.5,815,818.1,816.6,1645.4,10,6093238000,07-SEP-2017,1,INE002A01018\n"
        "FOO,SM,1,1,1,1,1,1,1,1,07-SEP-2017,1,INE000000099\n"
        "BAR,BE,10,11,9,10,10,10,5,1000,07-SEP-2017,1,INE000000098\n"
    )
    parsed = parse_bhav_zip(legacy)
    assert set(parsed["symbol"]) == {"RELIANCE", "BAR"}
    assert parsed.loc[parsed["symbol"] == "RELIANCE", "date"].iloc[0] == date(2017, 9, 7)
    assert parsed.loc[parsed["symbol"] == "RELIANCE", "turnover"].iloc[0] == 6093238000
    udiff = _zip(
        "TradDt,TckrSymb,SctySrs,ISIN,OpnPric,HghPric,LwPric,ClsPric,PrvsClsgPric,TtlTradgVol,TtlTrfVal\n"
        "2026-01-14,KOTAKBANK,EQ,INE237A01036,426,427,418,421,2132.6,100,4500000000\n"
        "2026-01-14,OTHER,SM,INE000000097,1,1,1,1,1,1,1\n"
    )
    got = parse_bhav_zip(udiff)
    assert list(got["symbol"]) == ["KOTAKBANK"]
    assert got.iloc[0]["prev_close"] == 2132.6


def test_classify_and_series_collapse():
    assert classify_body(403, b"no") == "blocked"
    assert classify_body(404, b"<html>") == "absent"
    assert classify_body(200, b"PK...") == "zip"
    assert classify_body(200, b"<html>missing") == "absent"
    assert classify_body(200, b"Access Denied") == "blocked"
    day = date(2017, 9, 7)
    rows = pd.DataFrame([
        _row(day, "AAA", "INE1", 10, 10, 1, "EQ"),
        _row(day, "AAA", "INE1", 99, 99, 50, "BE"),
    ])
    collapsed = collapse_series(rows)
    assert len(collapsed) == 1
    assert collapsed.iloc[0]["close"] == 10
    assert collapsed.iloc[0]["turnover"] == 51
    assert collapsed.iloc[0]["series"] == "EQ"


def test_price_factors_from_nse_subjects():
    assert price_factor(" Bonus 1:1") == 0.5
    assert price_factor("Face Value Split (Sub-Division) - From Rs 5/- Per Share To Re 1/- Per Share") == 0.2
    combined = price_factor("Bonus 1:1 / Face Value Split From Rs 10/- Per Share To Rs 2/- Per Share")
    assert combined == 0.1
    assert abs(price_factor("Bonus 1 : 1250") - 1250 / 1251) < 1e-9
    assert price_factor("Face Valus Split (Sub-Division) - From Rs 10/- Per To Rs 2/- Per Share") == 0.2
    assert price_factor("Face Value Split Rs 10 To Rs 1") == 0.1
    assert price_factor("Interim Dividend - Rs 1.50/- Per Share") is None
    assert capital_change("Scheme Of Arrangement - Bonus Debentures 1:1") is False
    assert capital_change("Bonus 1:1") is True


def test_rename_stays_one_lineage_and_a_split_changes_isin():
    rows = [
        _row(date(2026, 1, 13), "KOTAKBANK", "INEOLD", 2132.6, 2100),
        _row(date(2026, 1, 14), "KOTAKBANK", "INENEW", 421.0, 2132.6),
        _row(date(2017, 9, 6), "OLDNAME", "INE002A01018", 1645.4, 1600),
        _row(date(2017, 9, 7), "RELIANCE", "INE002A01018", 818.1, 1645.4),
    ]
    actions = parse_actions([
        {"symbol": "KOTAKBANK", "exDate": "14-Jan-2026", "isin": "INEOLD", "subject": "Face Value Split (Sub-Division) - From Rs 5/- Per Share To Re 1/- Per Share"},
        {"symbol": "RELIANCE", "exDate": "07-Sep-2017", "isin": "INE002A01018", "subject": "Bonus 1:1"},
    ])
    covered = {(2017, 9), (2026, 1)}
    pit, ledger = _finish(rows, actions, covered)
    assert pit["lineage"].nunique() == 2
    kotak = pit.loc[pit["symbol"] == "KOTAKBANK"].sort_values("date")
    assert kotak["lineage"].nunique() == 1
    assert abs(kotak.iloc[0]["adj_close"] - 2132.6 * 0.2) < 1e-6
    assert kotak.iloc[1]["adj_close"] == 421.0
    reliance = pit.loc[pit["isin"] == "INE002A01018"].sort_values("date")
    assert abs(reliance.iloc[0]["adj_close"] - 1645.4 * 0.5) < 1e-6
    applied = [row for row in ledger if row["applied"]]
    assert {row["symbol"] for row in applied} == {"KOTAKBANK", "RELIANCE"}


def test_a_crash_is_not_adjusted_when_the_month_has_no_corporate_action():
    day = date(2018, 1, 2)
    rows = [
        _row(date(2018, 1, 1), "CRASH", "INECRASH", 100, 100),
        _row(day, "CRASH", "INECRASH", 50, 100),
    ]
    pit, ledger = _finish(rows, [], {(2018, 1)})
    assert list(pit["adj_close"]) == [100, 50]
    assert ledger[0]["applied"] is False
    assert ledger[0]["reason"] == "no_corporate_action"
    unconfirmed, other = _finish(rows, [], set())
    assert abs(unconfirmed.iloc[0]["adj_close"] - 50) < 1e-6
    assert other[0]["reason"] == "ca_feed_missing"


def test_filters_do_not_see_the_next_day_and_a_split_is_not_the_proxy():
    start = date(2015, 1, 1)
    rows = []
    for offset in range(LISTED_SESSIONS + 6):
        day = start + timedelta(days=offset)
        rows.append(_row(day, "AAA", "INEAAA", 100.0, 100.0, 1e8))
    pit, _ = _finish(rows)
    target = pit.iloc[LISTED_SESSIONS - 1]
    assert target["sessions"] == LISTED_SESSIONS
    assert bool(target["passes"]) is True
    bumped = rows.copy()
    bumped[-1] = _row(bumped[-1]["date"], "AAA", "INEAAA", 100.0, 100.0, 1e15)
    again, _ = _finish(bumped)
    assert again.iloc[LISTED_SESSIONS - 1]["turnover_20d"] == target["turnover_20d"]

    jump = rows.copy()
    jump_day = jump[LISTED_SESSIONS]["date"]
    jump[LISTED_SESSIONS] = _row(jump_day, "AAA", "INEAAA", 130.0, 100.0, 1e8)
    flagged, _ = _finish(jump)
    assert bool(flagged.loc[flagged["date"] == jump_day, "big_move"].iloc[0]) is True
    assert bool(flagged.loc[flagged["date"] == jump_day, "passes"].iloc[0]) is False
    clear = jump_day + timedelta(days=5)
    assert bool(flagged.loc[flagged["date"] == clear, "big_move"].iloc[0]) is False

    split_rows = rows.copy()
    ex = split_rows[LISTED_SESSIONS]["date"]
    split_rows[LISTED_SESSIONS] = _row(ex, "AAA", "INEAAA", 50.0, 100.0, 1e8)
    from app.swing.bhavcopy import MONTHS
    actions = parse_actions([{
        "symbol": "AAA",
        "exDate": f"{ex.day:02d}-{MONTHS[ex.month - 1].title()}-{ex.year}",
        "subject": "Bonus 1:1",
    }])
    quiet, ledger = _finish(split_rows, actions, {(ex.year, ex.month)})
    assert ledger[0]["applied"] is True
    assert bool(quiet.loc[quiet["date"] == ex, "big_move"].iloc[0]) is False
    assert bool(quiet.loc[quiet["date"] == ex, "passes"].iloc[0]) is True


def test_same_day_split_and_bonus_use_the_product_and_a_rename_matches_by_isin():
    rows = [
        _row(date(2022, 9, 12), "BAJAJFINSV", "INE756I01012", 1000, 1000),
        _row(date(2022, 9, 13), "BAJAJFINSV", "INE756I01012", 104, 1000),
        _row(date(2024, 10, 17), "HEG", "INE545A01024", 2570.4, 2500),
        _row(date(2024, 10, 18), "HEG", "INE545A01024", 496.35, 2570.4),
    ]
    actions = parse_actions([
        {"symbol": "BAJAJFINSV", "exDate": "13-Sep-2022", "isin": "INE756I01012", "subject": "Face Value Split (Sub-Division) - From Rs 5/- Per Share To Re 1/- Per Share"},
        {"symbol": "BAJAJFINSV", "exDate": "13-Sep-2022", "isin": "INE756I01012", "subject": "Bonus 1:1"},
        {"symbol": "HEGAM", "exDate": "18-Oct-2024", "isin": "INE545A01024", "subject": "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share"},
    ])
    pit, ledger = _finish(rows, actions, {(2022, 9), (2024, 10)})
    bajaj = pit.loc[pit["symbol"] == "BAJAJFINSV"].sort_values("date")
    assert abs(bajaj.iloc[0]["adj_close"] - 100) < 1e-6
    heg = pit.loc[pit["isin"] == "INE545A01024"].sort_values("date")
    assert abs(heg.iloc[0]["adj_close"] - 2570.4 * 0.2) < 1e-4
    applied = [row["factor"] for row in ledger if row["applied"]]
    assert any(abs(factor - 0.1) < 1e-9 for factor in applied)
    assert any(abs(factor - 0.2) < 1e-9 for factor in applied)


def test_a_debenture_bonus_does_not_change_the_share_price():
    rows = [
        _row(date(2019, 8, 21), "BRITANNIA", "INE216A01030", 100, 100),
        _row(date(2019, 8, 22), "BRITANNIA", "INE216A01030", 102, 100),
    ]
    actions = parse_actions([{
        "symbol": "BRITANNIA",
        "exDate": "22-Aug-2019",
        "isin": "INE216A01030",
        "subject": "Scheme Of Arangement- Bonus - 1 Debenture For 1 Equity Share Held",
    }])
    pit, ledger = _finish(rows, actions, {(2019, 8)})
    assert list(pit["adj_close"]) == [100, 102]
    assert not any(row["applied"] for row in ledger)


def test_size_rank_and_the_year_end_gap():
    day = date(2020, 1, 31)
    rows = []
    for offset in range(20):
        session = day - timedelta(days=19 - offset)
        for rank in range(101):
            rows.append(_row(session, f"S{rank}", f"INE{rank:06d}", 100, 100, turnover=(101 - rank) * 1e8))
    pit, _ = _finish(rows)
    last = pit.loc[pit["date"] == day]
    assert last.loc[last["symbol"] == "S0", "size"].iloc[0] == "large"
    assert last.loc[last["symbol"] == "S99", "size"].iloc[0] == "large"
    assert last.loc[last["symbol"] == "S100", "size"].iloc[0] == "mid"

    summary_frame = pd.DataFrame([
        {"date": date(2015, 12, 31), "lineage": "OLD", "isin": "INEOLD", "symbol": "GONE", "size": "small", "passes": True, "proxy_only": False},
        {"date": date(2015, 12, 31), "lineage": "STAY", "isin": "INEKEEP", "symbol": "KEEP", "size": "large", "passes": True, "proxy_only": False},
        {"date": date(2016, 6, 1), "lineage": "STAY", "isin": "INEKEEP", "symbol": "KEEP", "size": "large", "passes": True, "proxy_only": False},
    ])
    years = summarize_years(summary_frame, {"INEKEEP"}, {"KEEP"})
    year_2015 = years[0]
    assert year_2015["outside_nifty500"] == 1
    assert year_2015["later_delisted"] == 1
    assert year_2015["large"] == 1
    assert year_2015["small"] == 1
    assert years[1]["later_delisted"] == 0


def test_compare_calendar_and_the_worker_refuses_a_live_session():
    left = pd.Series([100.0, 100.5], index=[date(2024, 1, 2), date(2024, 1, 3)])
    right = pd.Series([100.0, 110.0], index=[date(2024, 1, 2), date(2024, 1, 3)])
    stats = compare_closes(left, right)
    assert stats["overlap"] == 2
    assert stats["over_tol"] == 1
    assert coverage_complete(date(2015, 1, 2), date(2026, 11, 23), date(2015, 1, 1), date(2026, 11, 25))
    assert not coverage_complete(date(2016, 1, 1), date(2026, 11, 23), date(2015, 1, 1), date(2026, 11, 25))
    assert market_hours_block(datetime(2026, 11, 25, 10, 0, tzinfo=IST))
    assert market_hours_block(datetime(2026, 11, 25, 16, 10, tzinfo=IST))
    assert market_hours_block(datetime(2026, 11, 25, 16, 11, tzinfo=IST)) is None
    assert market_hours_block(datetime(2026, 11, 28, 10, 0, tzinfo=IST)) is None
    assert refuse_if_live() is None
