"""Write one day's spread report from data/spreads. No network."""

from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path

from app.config import settings
from app.data.history import read_parquet
from app.live.model import IST
from app.live.spreads.report import write_report_files


def nifty_bars(day: date, candles_dir: Path) -> list[dict]:
    path = candles_dir / "NIFTY50" / "1m.parquet"
    if not path.is_file():
        return []
    frame = read_parquet(path)
    start = int(datetime(day.year, day.month, day.day, tzinfo=IST).timestamp())
    end = start + 86_400
    bars = []
    for rec in frame.itertuples(index=False):
        ts = int(rec.time)
        if start <= ts < end:
            bars.append({"time": ts, "high": float(rec.high), "low": float(rec.low)})
    return bars


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Daily option-spread report")
    parser.add_argument("--date", required=True, help="IST day, YYYY-MM-DD")
    parser.add_argument("--spreads", type=Path, default=None)
    args = parser.parse_args(argv)
    day = date.fromisoformat(args.date)
    directory = args.spreads or (settings.data_dir / "spreads")
    write_report_files(directory, day, nifty_bars(day, settings.candles_dir))


if __name__ == "__main__":
    main()
