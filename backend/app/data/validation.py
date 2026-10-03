"""Bar-by-bar comparison of two candle series (used to validate our 5m-from-1m against Upstox 5m)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))
PRICE_FIELDS = ("open", "high", "low", "close")
PRICE_TOLERANCE = 0.005  # half a paisa: below the 0.05 tick size


@dataclass(frozen=True)
class Mismatch:
    time: int
    field: str
    ours: float
    theirs: float

    @property
    def diff(self) -> float:
        return self.ours - self.theirs


@dataclass
class CompareReport:
    label: str
    compared: int = 0
    only_ours: list[int] = field(default_factory=list)
    only_theirs: list[int] = field(default_factory=list)
    mismatches: list[Mismatch] = field(default_factory=list)
    #: the 09:15 bar of every session, reported separately
    open_bars_compared: int = 0
    open_bar_mismatches: list[Mismatch] = field(default_factory=list)

    @property
    def mismatched_bars(self) -> int:
        return len({m.time for m in self.mismatches})

    @property
    def ok(self) -> bool:
        return not (self.only_ours or self.only_theirs or self.mismatches)

    def by_field(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for m in self.mismatches:
            out[m.field] = out.get(m.field, 0) + 1
        return out


def minute_of_day(t: int) -> int:
    d = datetime.fromtimestamp(t, IST)
    return d.hour * 60 + d.minute


def compare_bars(
    ours: Iterable[Mapping[str, Any]],
    theirs: Iterable[Mapping[str, Any]],
    *,
    label: str,
    compare_volume: bool = True,
    open_minute: int = 9 * 60 + 15,
) -> CompareReport:
    """Compare two candle lists keyed by bar start time (unix s)."""
    a = {int(c["time"]): c for c in ours}
    b = {int(c["time"]): c for c in theirs}
    rep = CompareReport(label=label)
    rep.only_ours = sorted(set(a) - set(b))
    rep.only_theirs = sorted(set(b) - set(a))
    for t in sorted(set(a) & set(b)):
        rep.compared += 1
        is_open = minute_of_day(t) == open_minute
        if is_open:
            rep.open_bars_compared += 1
        fields = PRICE_FIELDS + (("volume",) if compare_volume else ())
        for f in fields:
            x, y = float(a[t][f]), float(b[t][f])
            tol = PRICE_TOLERANCE if f in PRICE_FIELDS else 0.5
            if abs(x - y) > tol:
                m = Mismatch(t, f, x, y)
                rep.mismatches.append(m)
                if is_open:
                    rep.open_bar_mismatches.append(m)
    return rep


def fmt_time(t: int) -> str:
    return datetime.fromtimestamp(t, IST).strftime("%Y-%m-%d %a %H:%M")


def render_report(reports: list[CompareReport], *, examples: int = 8) -> str:
    lines: list[str] = []
    for r in reports:
        lines.append(f"### {r.label}")
        lines.append("")
        lines.append(
            f"- bars compared: **{r.compared}**; only in ours: **{len(r.only_ours)}**; "
            f"only in the other: **{len(r.only_theirs)}**; bars with a field mismatch: **{r.mismatched_bars}** "
            f"(by field: {r.by_field() or 'none'})"
        )
        lines.append(
            f"- 09:15 bars: compared **{r.open_bars_compared}**, mismatching **{len({m.time for m in r.open_bar_mismatches})}**"
        )
        for title, items in (("only in ours", r.only_ours), ("only in the other", r.only_theirs)):
            if items:
                lines.append(f"- {title} (first {examples}): " + ", ".join(fmt_time(t) for t in items[:examples]))
        for m in r.mismatches[:examples]:
            lines.append(f"- {fmt_time(m.time)} {m.field}: ours {m.ours} vs {m.theirs} (diff {m.diff:+.4f})")
        lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------ daily volume sums
@dataclass(frozen=True)
class DayVolume:
    day: date
    ours: int
    theirs: int
    bars_ours: int
    bars_theirs: int

    @property
    def ok(self) -> bool:
        return self.ours == self.theirs and self.bars_ours == self.bars_theirs


def daily_volume_sums(ours: Iterable[Mapping[str, Any]], theirs: Iterable[Mapping[str, Any]]) -> list[DayVolume]:
    """Total volume (and bar count) per IST day for two candle lists: catches a volume that is
    wrong in a way that happens to survive bar-by-bar matching (e.g. a whole missing bar)."""
    tot: dict[date, list[int]] = {}
    for idx, rows in enumerate((ours, theirs)):
        for c in rows:
            d = datetime.fromtimestamp(int(c["time"]), IST).date()
            acc = tot.setdefault(d, [0, 0, 0, 0])
            acc[idx] += int(c["volume"])
            acc[2 + idx] += 1
    return [DayVolume(d, v[0], v[1], v[2], v[3]) for d, v in sorted(tot.items())]


def render_volume_sums(label: str, rows: list[DayVolume], *, examples: int = 8) -> str:
    bad = [r for r in rows if not r.ok]
    total_ours, total_theirs = sum(r.ours for r in rows), sum(r.theirs for r in rows)
    lines = [
        f"### {label}",
        "",
        f"- days compared: **{len(rows)}**; days whose volume sum or bar count differs: **{len(bad)}**",
        f"- total volume: ours **{total_ours:,}** vs Upstox **{total_theirs:,}** (diff {total_ours - total_theirs:+,})",
    ]
    for r in bad[:examples]:
        lines.append(
            f"- {r.day} ({r.day:%a}): volume ours {r.ours:,} vs {r.theirs:,}; bars {r.bars_ours} vs {r.bars_theirs}"
        )
    lines.append("")
    return "\n".join(lines)
