"""List every 1m range the store recorded as an empty Upstox answer.

An empty file that was marked covered is reclassified as source_empty first, so a hole
from before this rule still appears. Read-only apart from that reclassification.

    uv run python -m scripts.source_empty
"""

from __future__ import annotations

from app.config import settings
from app.data.history import source_empty_report


def main() -> None:
    rows = source_empty_report(settings.data_dir / "candles")
    if not rows:
        print("no source_empty ranges")
        return
    print(f"{'instrument':<32} {'from':<12} {'to':<12} {'tried':<12} name")
    for row in rows:
        key = row["instrument_key"] or row["symbol"]
        print(f"{key:<32} {row['from']:<12} {row['to']:<12} {row['tried']:<12} {row['name']}")


if __name__ == "__main__":
    main()
