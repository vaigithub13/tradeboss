"""Importer spec: clean raw tape bars, label sessions by type, write Parquet."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from app.data.importer import COLUMNS, build_frame, write_parquet

IST = timezone(timedelta(hours=5, minutes=30))


def raw(y: int, mo: int, d: int, h: int, mi: int, price: float = 100.0) -> dict[str, Any]:
    t_ms = int(datetime(y, mo, d, h, mi, tzinfo=IST).timestamp()) * 1000
    return {"t": t_ms, "open": price, "high": price + 1, "low": price - 1,
            "close": price + 0.5, "volume": 0}  # fmt: skip


def raw_range(y: int, mo: int, d: int, start: tuple[int, int], bars: int) -> list[dict[str, Any]]:
    """`bars` five-minute raw bars starting at `start` (h, m)."""
    t0 = int(datetime(y, mo, d, *start, tzinfo=IST).timestamp())
    return [{"t": (t0 + 300 * i) * 1000, "open": 1, "high": 2, "low": 0, "close": 1, "volume": 0}
            for i in range(bars)]  # fmt: skip


def full_day(y: int, mo: int, d: int) -> list[dict[str, Any]]:
    return raw_range(y, mo, d, (9, 15), 75)


def labels_by_date(df: pd.DataFrame) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for t, label in zip(df["time"], df["session_type"], strict=True):
        day = datetime.fromtimestamp(int(t), IST).strftime("%Y-%m-%d")
        out.setdefault(day, set()).add(label)
    return out


def test_columns_contract() -> None:
    assert COLUMNS == ["time", "open", "high", "low", "close", "volume", "oi", "session_type"]


def test_normal_session_and_off_session_prints_dropped() -> None:
    bars = full_day(2024, 1, 1) + [
        raw(2024, 1, 1, 9, 10),  # pre-open print -> dropped
        raw(2024, 1, 1, 15, 30),  # closing print  -> dropped
        raw(2024, 1, 1, 15, 35),  # post-close     -> dropped
    ]
    df, report = build_frame(bars)
    assert len(df) == 75
    assert labels_by_date(df) == {"2024-01-01": {"normal"}}
    assert (report.read, report.kept, report.dropped_out_of_session) == (78, 75, 3)


def test_stray_prints_do_not_make_a_session_look_short_or_full() -> None:
    # a 21-bar broken Saturday + a stray 15:35 print must still be special_short
    bars = raw_range(2024, 3, 2, (9, 15), 10) + raw_range(2024, 3, 2, (11, 30), 11)
    bars.append(raw(2024, 3, 2, 15, 35))
    df, report = build_frame(bars)
    assert labels_by_date(df) == {"2024-03-02": {"special_short"}}
    assert report.dropped_out_of_session == 1


def test_labels_are_by_session_type_not_weekday() -> None:
    bars = (
        full_day(2024, 1, 1)  # Monday, full            -> normal
        + full_day(2025, 2, 1)  # Saturday, full         -> weekend_full
        + full_day(2026, 2, 1)  # Sunday, full           -> weekend_full
        + raw_range(2024, 3, 2, (9, 15), 10) + raw_range(2024, 3, 2, (11, 30), 11)  # broken Sat
        + raw_range(2024, 5, 18, (9, 15), 10) + raw_range(2024, 5, 18, (11, 30), 11)
        + raw_range(2024, 12, 3, (9, 15), 30)  # weekday early close -> special_short
    )
    df, _ = build_frame(bars)
    assert labels_by_date(df) == {
        "2024-01-01": {"normal"},
        "2025-02-01": {"weekend_full"},
        "2026-02-01": {"weekend_full"},
        "2024-03-02": {"special_short"},
        "2024-05-18": {"special_short"},
        "2024-12-03": {"special_short"},
    }


def test_all_muhurat_sessions_are_kept_and_labeled_consistently() -> None:
    bars = (
        raw_range(2022, 10, 24, (18, 15), 12)  # evening Monday
        + raw_range(2023, 11, 12, (18, 15), 12)  # evening SUNDAY
        + raw_range(2024, 11, 1, (18, 0), 12)  # evening Friday
        + raw_range(2025, 10, 21, (13, 45), 12)  # afternoon Tuesday
    )
    df, report = build_frame(bars)
    assert len(df) == 48 and report.dropped_out_of_session == 0
    assert labels_by_date(df) == {
        "2022-10-24": {"muhurat"},
        "2023-11-12": {"muhurat"},
        "2024-11-01": {"muhurat"},
        "2025-10-21": {"muhurat"},
    }


def test_sorted_deduplicated_oi_null() -> None:
    a = raw(2024, 1, 1, 9, 20, 200)
    b = raw(2024, 1, 1, 9, 15, 100)
    df, report = build_frame([a, b, dict(b)])
    assert list(df["time"]) == sorted(df["time"])
    assert len(df) == 2
    assert report.duplicates == 1
    assert df["oi"].isna().all()
    assert list(df.columns) == COLUMNS


def test_report_counts_sessions_by_type() -> None:
    df, report = build_frame(
        full_day(2024, 1, 1) + full_day(2025, 2, 1) + raw_range(2024, 11, 1, (18, 0), 12)
    )
    assert report.sessions_by_type == {"normal": 1, "weekend_full": 1, "muhurat": 1}
    assert len(df) == 75 + 75 + 12


def test_write_parquet_roundtrip_via_duckdb(tmp_path: Path) -> None:
    df, _ = build_frame([raw(2024, 1, 1, 9, 15), raw(2024, 11, 1, 18, 0)])
    out = tmp_path / "candles" / "TEST" / "5m.parquet"
    write_parquet(df, out)
    rows = duckdb.connect().execute(
        f"SELECT session_type, oi FROM read_parquet('{out}') ORDER BY time"
    ).fetchall()
    assert rows == [("special_short", None), ("muhurat", None)]
