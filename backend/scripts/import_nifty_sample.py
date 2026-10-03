"""One-off import of sample Nifty 50 5m history into TradeBoss as Parquet.

Reads (READ-ONLY) the tape.json files of the "My Trading Desk" project and writes:

    <repo>/data/candles/NIFTY50/5m.parquet

Cleaning and session labeling rules live in app/data/importer.py.

Usage (from backend/):  uv run python -m scripts.import_nifty_sample [SOURCE_DIR]
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from app.data.importer import build_frame, write_parquet

DEFAULT_SOURCE = Path("/Users/vai/INVEST/My Trading Desk/data/research-datasets")
TAPES = [
    "upstox-nifty50-5m-2022-2024/tape.json",
    "upstox-nifty50-5m/tape.json",
]
OUT = Path(__file__).resolve().parents[2] / "data" / "candles" / "NIFTY50" / "5m.parquet"


def raw_bars(source: Path) -> Iterator[dict[str, Any]]:
    for rel in TAPES:
        tape = json.loads((source / rel).read_text())
        for session in tape["sessions"]:
            yield from session["candles"]


def main() -> None:
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOURCE
    df, report = build_frame(raw_bars(source))
    write_parquet(df, OUT)
    print(report)
    print(df["session_type"].value_counts().to_string())
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
