"""Switchable setup modules. Add a file and register it in SETUP_FUNCS."""

from __future__ import annotations

from app.scanner.models import Hit
from app.scanner.setups.flag import find_flags
from app.scanner.setups.gap import find_gaps
from app.scanner.setups.orb15 import find_orb
from app.scanner.setups.rsi_extreme import find_rsi_extremes
from app.scanner.setups.vwap_cross import find_vwap_crosses

SETUP_FUNCS = {
    "orb15": find_orb,
    "flag": find_flags,
    "rsi_extreme": find_rsi_extremes,
    "vwap_cross": find_vwap_crosses,
    "gap_go": find_gaps,
    "gap_fill": find_gaps,
}


def run_setups(
    bars: list[dict],
    *,
    enabled: dict[str, bool],
    prev_close: float | None,
    as_of: int | None = None,
) -> list[Hit]:
    hits: list[Hit] = []
    if enabled.get("orb15", True):
        hits.extend(find_orb(bars, as_of=as_of))
    if enabled.get("flag", True):
        hits.extend(find_flags(bars, as_of=as_of))
    if enabled.get("rsi_extreme", True):
        hits.extend(find_rsi_extremes(bars, as_of=as_of))
    if enabled.get("vwap_cross", True):
        hits.extend(find_vwap_crosses(bars, as_of=as_of))
    if enabled.get("gap_go", True) or enabled.get("gap_fill", True):
        for hit in find_gaps(bars, prev_close=prev_close, as_of=as_of):
            if hit.setup == "gap_go" and not enabled.get("gap_go", True):
                continue
            if hit.setup == "gap_fill" and not enabled.get("gap_fill", True):
                continue
            hits.append(hit)
    return hits
