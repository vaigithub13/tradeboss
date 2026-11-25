"""Build the walk-forward cache once. This is not the web server.

Writes under data/similar/, which stays untracked. Prints the reliability
numbers for NIFTY 5m, NIFTY 15m, and similar days.
"""

from __future__ import annotations

from app.config import settings
from app.data.store import CandleStore
from app.similar.features import TIME_CAP_S
from app.similar.service import compute_days_reliability, compute_reliability


def main() -> None:
    store = CandleStore(settings.data_dir / "candles")
    cache = settings.data_dir / "similar"
    for timeframe in ("5m", "15m"):
        report = compute_reliability(
            store, cache, symbol="NIFTY50", timeframe=timeframe, time_cap_s=TIME_CAP_S,
        )
        print(f"NIFTY50 {timeframe}")
        for horizon, block in report.items():
            if not isinstance(block, dict) or "hit_rate" not in block:
                continue
            print(
                f"  {horizon}: hit {block['hit_rate']} vs base {block['base_rate']} "
                f"n={block['n']} sample={block['sample_size']} evaluated={block['evaluated']} "
                f"runtime={block['runtime_s']}s capped={block['capped']} verdict={block['verdict']}"
            )
    days = compute_days_reliability(store, cache, symbol="NIFTY50", time_cap_s=TIME_CAP_S)
    print(
        "NIFTY50 similar days 10:15 "
        f"hit {days['hit_rate']} vs base {days['base_rate']} n={days['n']} "
        f"sample={days['sample_size']} evaluated={days['evaluated']} "
        f"runtime={days['runtime_s']}s capped={days['capped']} verdict={days['verdict']}"
    )


if __name__ == "__main__":
    main()
