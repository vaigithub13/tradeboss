"""Build today's pre-market view, or score a day's view after the close.

    cd backend && uv run python -m scripts.market_view --build
    cd backend && uv run python -m scripts.market_view --score [--day 2026-11-09]
"""

from __future__ import annotations

import argparse
import json
from datetime import date

from app.config import settings
from app.data.store import CandleStore
from app.backtest.expiry import load_default_calendar
from app.market_view import service


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Pre-market view")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--score", action="store_true")
    parser.add_argument("--day", default=date.today().isoformat())
    args = parser.parse_args(argv)
    day = date.fromisoformat(args.day)
    store = CandleStore(settings.candles_dir)
    if args.build:
        view = service.build_for(day, data_dir=settings.data_dir, store=store, is_trading_day=load_default_calendar().is_trading_day)
        print(f"{view['bias']} (score {view['score']}), {view['day_type']} day, range {view['expected_range']}")
        for f in view["factors"]:
            print(f"  {f['label']:<10} {f['score'] if f['available'] else '-':>3}  {f['value']}  ({f['reason']})")
        print(f"  plan: {view['plan']}")
    if args.score:
        out = service.score_day(day, data_dir=settings.data_dir, store=store)
        print(json.dumps(out) if out else f"no view or no candles for {day}")


if __name__ == "__main__":
    main()
