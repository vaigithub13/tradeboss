"""The pre-registered options candidates (fixed before any run; report every row, do not tune).

Shared by scripts/compare_candidates.py and the end-of-day GEX study (scripts/gex_report.py)."""

from __future__ import annotations

from typing import Any

CANDIDATES: list[tuple[str, str, dict[str, Any]]] = [
    ("C1", "opening_range_breakout", {"range_minutes": 15}),
    ("C2", "orb_plus", {"rr": 2.0}),
    ("C3", "orb_plus", {"rr": 3.0}),
    ("C4", "orb_plus", {"rr": 2.0, "prior_day": "nr7"}),
    ("C5", "orb_plus", {"rr": 2.0, "prior_day": "nr4"}),
    ("C6", "orb_plus", {"rr": 2.0, "cpr": "narrow"}),
    ("C7", "orb_plus", {"rr": 2.0, "bias": "cpr"}),
    ("C8", "orb_plus", {"rr": 2.0, "cpr": "narrow", "bias": "cpr"}),
    ("C9", "orb_plus", {"rr": 2.0, "range_minutes": 30}),
    ("C10", "orb_plus", {"rr": 2.0, "range_atr_max": 0.35}),
    ("C11", "pdh_pdl_breakout", {"rr": 2.0, "stop_atr": 0.2}),
    ("C12", "pdh_pdl_breakout", {"rr": 3.0, "stop_atr": 0.2}),
    ("C13", "pdh_pdl_breakout", {"rr": 2.0, "stop_atr": 0.2, "bias": "cpr"}),
]
