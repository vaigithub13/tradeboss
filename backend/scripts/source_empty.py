"""List every candle range and option contract the store recorded as an empty Upstox answer.

An empty file marked covered, or an option contract marked done with no candles, is
reclassified as source_empty first, so a hole from before this rule still appears.
Read-only apart from that reclassification.

    uv run python -m scripts.source_empty
"""

from __future__ import annotations

from app.config import settings
from app.data.history import source_empty_report
from app.options.history import option_source_empty_report


def main() -> None:
    rows = source_empty_report(settings.data_dir / "candles")
    rows += option_source_empty_report(settings.data_dir / "option_history")
    if not rows:
        print("no source_empty ranges")
        return
    print(f"{'instrument':<32} {'from':<12} {'to':<12} {'tried':<12} name")
    for row in rows:
        key = row["instrument_key"] or row["symbol"]
        print(f"{key:<32} {row['from']:<12} {row['to']:<12} {row['tried']:<12} {row['name']}")


if __name__ == "__main__":
    main()
