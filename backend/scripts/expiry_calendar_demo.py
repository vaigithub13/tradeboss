"""Print the Nifty expiry calendar around the rule changes (the shipped rules and holidays).

    uv run python -m scripts.expiry_calendar_demo
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from app.backtest.expiry import load_default_calendar

ROOT = Path(__file__).resolve().parents[1]
REAL = {date.fromisoformat(x) for x in json.loads((ROOT / "tests/fixtures/upstox/nifty_expiries_2024-10_to_2026-09.json").read_text())["expiries"]}
CAL = load_default_calendar()

WINDOWS = [
    ("Rule change: Thursday -> Tuesday (expiries on/after 2025-09-01)", "2025-07-14", "2025-11-04"),
    ("Deferred Monday plan (NSE/FAOP/66938 -> deferred by 67338): still Thursdays", "2025-03-03", "2025-05-08"),
    ("Nov 2024: weeklies of BANKNIFTY/FINNIFTY/MIDCPNIFTY end; Nifty unchanged", "2024-10-28", "2025-01-09"),
    ("Holiday shifts, rule-based only (no real list before 2024-10): spring 2022", "2022-03-28", "2022-05-06"),
    ("Holiday shifts, rule-based only: Mar-Apr 2023", "2023-03-20", "2023-04-28"),
    ("Holiday shifts, rule-based only: Mar-Apr 2024", "2024-03-18", "2024-05-03"),
    ("Upcoming (NSE 2026 holidays: 20-Oct, 10-Nov, 24-Nov, 25-Dec)", "2026-10-05", "2026-12-31"),
]


def main() -> None:
    for title, a, b in WINDOWS:
        print(f"\n{title}")
        for e in CAL.expiries(date.fromisoformat(a), date.fromisoformat(b)):
            shift = f"  <- {e.nominal.isoformat()} {e.nominal.strftime('%a')} is a holiday" if e.shifted else ""
            real = "" if e.date < date(2024, 10, 3) or e.date > date(2026, 9, 29) else ("  [real: yes]" if e.date in REAL else "  [real: NO]")
            print(f"  {e.date.isoformat()} {e.date.strftime('%a')}  {e.kind:<7}{shift}{real}")


if __name__ == "__main__":
    main()
