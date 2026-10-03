"""Compare the Black-Scholes overlay to real option 1m opens. Never hits the network."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from app.backtest.expiry import ExpiryCalendar
from app.options.bs import bs_price_array
from app.options.history import OptionBar
from app.options.strikes import atm_strike
from app.options.time import years_to_expiry
from app.backtest.sources import ist_date

IST = timezone(timedelta(hours=5, minutes=30))
OPEN_MIN = 9 * 60 + 15
PHI = (math.sqrt(5.0) - 1.0) / 2.0
DTE_BUCKETS = ("0", "1", "2", "3-4", "5+")
TOD_BUCKETS = ("morning", "midday", "afternoon")


@dataclass(frozen=True)
class Point:
    time: int
    expiry: date
    strike: float
    kind: str
    real: float
    spot: float
    vix: float
    volume: float
    T_trading: float
    T_calendar: float
    dte: int
    moneyness: str
    tod: str


@dataclass
class FilterCounts:
    input: int = 0
    zero_volume: int = 0
    cheap: int = 0
    open_minutes: int = 0
    kept: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def _tod_bucket(t: int) -> str:
    local = datetime.fromtimestamp(int(t), IST)
    m = local.hour * 60 + local.minute
    if m < 11 * 60:
        return "morning"
    if m < 14 * 60:
        return "midday"
    return "afternoon"


def _dte(day: date, expiry: date, is_trading_day: Callable[[date], bool]) -> int:
    n = 0
    d = day
    while d < expiry:
        d += timedelta(days=1)
        if is_trading_day(d):
            n += 1
    return n


def _dte_bucket(dte: int) -> str:
    if dte <= 0:
        return "0"
    if dte == 1:
        return "1"
    if dte == 2:
        return "2"
    if dte <= 4:
        return "3-4"
    return "5+"


def _moneyness(kind: str, strike: float, spot: float, step: int) -> str:
    atm = atm_strike(spot, step)
    steps = int(round((strike - atm) / step))
    if steps == 0:
        return "ATM"
    otm = steps > 0 if kind == "CE" else steps < 0
    if abs(steps) == 1:
        return "OTM1" if otm else "ITM1"
    return "other"


def pair_points(
    option_bars: Sequence[OptionBar],
    index_open: dict[int, float],
    vix_open: dict[int, float],
    *,
    calendar: ExpiryCalendar,
    step: int,
) -> list[Point]:
    """One point per option minute that has an index open and a VIX open at the same unix time."""
    out: list[Point] = []
    for b in option_bars:
        spot = index_open.get(b.time)
        vix = vix_open.get(b.time)
        if spot is None or vix is None:
            continue
        day = ist_date(b.time)
        out.append(
            Point(
                time=b.time,
                expiry=b.expiry,
                strike=b.strike,
                kind=b.kind,
                real=b.open,
                spot=spot,
                vix=vix,
                volume=b.volume,
                T_trading=years_to_expiry(b.time, b.expiry, calendar.is_trading_day, "trading"),
                T_calendar=years_to_expiry(b.time, b.expiry, calendar.is_trading_day, "calendar"),
                dte=_dte(day, b.expiry, calendar.is_trading_day),
                moneyness=_moneyness(b.kind, b.strike, spot, step),
                tod=_tod_bucket(b.time),
            )
        )
    return out


def filter_points(
    points: Sequence[Point], *, min_premium: float = 5.0, skip_open: int = 3
) -> tuple[list[Point], FilterCounts]:
    counts = FilterCounts(input=len(points))
    kept: list[Point] = []
    for p in points:
        local = datetime.fromtimestamp(p.time, IST)
        minute = local.hour * 60 + local.minute
        if p.volume <= 0:
            counts.zero_volume += 1
            continue
        if p.real < min_premium:
            counts.cheap += 1
            continue
        if OPEN_MIN <= minute < OPEN_MIN + skip_open:
            counts.open_minutes += 1
            continue
        kept.append(p)
    counts.kept = len(kept)
    return kept, counts


def model_prices(points: Sequence[Point], scale: float, basis: str = "trading", r: float = 0.0, q: float = 0.0) -> np.ndarray:
    if not points:
        return np.array([], dtype=float)
    kinds = np.array([p.kind for p in points])
    S = np.array([p.spot for p in points], dtype=float)
    K = np.array([p.strike for p in points], dtype=float)
    T = np.array([p.T_trading if basis == "trading" else p.T_calendar for p in points], dtype=float)
    sig = scale * np.array([p.vix for p in points], dtype=float) / 100.0
    return bs_price_array(kinds, S, K, T, sig, r, q)


def error_stats(real: np.ndarray, model: np.ndarray) -> dict[str, float]:
    """bias / MAPE / median APE / RMSE of the relative error (model − real) / real."""
    real = np.asarray(real, dtype=float)
    model = np.asarray(model, dtype=float)
    ok = np.isfinite(real) & np.isfinite(model) & (real != 0)
    if not np.any(ok):
        return {"n": 0, "bias": math.nan, "mape": math.nan, "median_ape": math.nan, "rmse": math.nan}
    rel = (model[ok] - real[ok]) / real[ok]
    ape = np.abs(rel)
    return {
        "n": int(ok.sum()),
        "bias": float(np.mean(rel)),
        "mape": float(np.mean(ape)),
        "median_ape": float(np.median(ape)),
        "rmse": float(np.sqrt(np.mean(rel ** 2))),
    }


def _huber(real: np.ndarray, model: np.ndarray, delta: float) -> float:
    ok = np.isfinite(real) & np.isfinite(model) & (real != 0)
    if not np.any(ok):
        return float("inf")
    rel = (model[ok] - real[ok]) / real[ok]
    a = np.abs(rel)
    loss = np.where(a <= delta, 0.5 * rel ** 2, delta * (a - 0.5 * delta))
    return float(np.mean(loss))


def fit_scale(
    points: Sequence[Point],
    *,
    basis: str = "trading",
    r: float = 0.0,
    q: float = 0.0,
    lo: float = 0.3,
    hi: float = 3.0,
    delta: float = 0.15,
    tol: float = 1e-4,
) -> float:
    """Huber-loss scale on relative error. Golden-section search; deterministic."""
    reals = np.array([p.real for p in points], dtype=float)

    def loss(k: float) -> float:
        return _huber(reals, model_prices(points, k, basis, r, q), delta)

    a, b = lo, hi
    c = b - PHI * (b - a)
    d = a + PHI * (b - a)
    fc, fd = loss(c), loss(d)
    while (b - a) > tol:
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - PHI * (b - a)
            fc = loss(c)
        else:
            a, c, fc = c, d, fd
            d = a + PHI * (b - a)
            fd = loss(d)
    return (a + b) / 2.0


def bootstrap_scale_ci(
    points: Sequence[Point],
    *,
    basis: str = "trading",
    n: int = 100,
    seed: int = 1,
    lo: float = 2.5,
    hi: float = 97.5,
    max_points: int = 20_000,
) -> dict[str, float]:
    """Day-block bootstrap of the fitted scale. Seeded, so the report is repeatable.

    Each resample is capped at `max_points` (seeded) so a full-history run stays tractable.
    """
    if not points:
        return {"n": 0, "lo": math.nan, "hi": math.nan, "median": math.nan}
    by_day: dict[date, list[Point]] = {}
    for p in points:
        by_day.setdefault(ist_date(p.time), []).append(p)
    days = sorted(by_day)
    rng = np.random.RandomState(seed)
    samples: list[float] = []
    for i in range(n):
        pick = rng.choice(len(days), size=len(days), replace=True)
        block = [q for j in pick for q in by_day[days[int(j)]]]
        if len(block) > max_points:
            idx = rng.choice(len(block), size=max_points, replace=False)
            block = [block[int(k)] for k in idx]
        samples.append(fit_scale(block, basis=basis))
    arr = np.array(samples, dtype=float)
    return {
        "n": n,
        "lo": float(np.percentile(arr, lo)),
        "hi": float(np.percentile(arr, hi)),
        "median": float(np.median(arr)),
    }


def compare_bases(points: Sequence[Point], *, r: float = 0.0, q: float = 0.0) -> dict[str, Any]:
    """Which T convention fits real premiums better, overall and per days-to-expiry bucket."""
    out: dict[str, Any] = {}
    for bucket in ("all",) + DTE_BUCKETS:
        subset = list(points) if bucket == "all" else [p for p in points if _dte_bucket(p.dte) == bucket]
        row: dict[str, Any] = {"n": len(subset)}
        if len(subset) < 8:
            row["winner"] = None
            out[bucket] = row
            continue
        for basis in ("trading", "calendar"):
            k = fit_scale(subset, basis=basis, r=r, q=q)
            stats = error_stats(np.array([p.real for p in subset]), model_prices(subset, k, basis, r, q))
            row[basis] = {"scale": k, **stats}
        t_m, c_m = row["trading"]["median_ape"], row["calendar"]["median_ape"]
        if t_m < c_m:
            row["winner"] = "trading"
        elif c_m < t_m:
            row["winner"] = "calendar"
        else:
            row["winner"] = "tie"
        out[bucket] = row
    return out


def implied_carry(points: Sequence[Point], *, min_dte: int = 1) -> dict[str, Any]:
    """Median annualised net carry from put-call parity, assuming q=0 and calendar T.

    C − P = S − K e^{−rT}  ⇒  r = −ln((S − C + P) / K) / T.
    Paired at the same (time, expiry, strike). Not applied automatically.
    """
    by: dict[tuple[int, date, float], dict[str, Point]] = {}
    for p in points:
        if p.dte < min_dte:
            continue
        by.setdefault((p.time, p.expiry, p.strike), {})[p.kind] = p
    rs: list[float] = []
    for pair in by.values():
        if "CE" not in pair or "PE" not in pair:
            continue
        ce, pe = pair["CE"], pair["PE"]
        if ce.T_calendar <= 0 or ce.strike <= 0:
            continue
        fwd = ce.spot - ce.real + pe.real
        if fwd <= 0:
            continue
        ratio = fwd / ce.strike
        if ratio <= 0:
            continue
        rs.append(-math.log(ratio) / ce.T_calendar)
    if not rs:
        return {"n": 0, "median": None, "q25": None, "q75": None, "applied": False}
    arr = np.array(rs, dtype=float)
    return {
        "n": int(arr.size),
        "median": float(np.median(arr)),
        "q25": float(np.percentile(arr, 25)),
        "q75": float(np.percentile(arr, 75)),
        "applied": False,
        "note": "q=0; calendar T; not written into the model",
    }


def bucket_errors(points: Sequence[Point], scale: float, basis: str = "trading") -> dict[str, Any]:
    real = np.array([p.real for p in points], dtype=float)
    model = model_prices(points, scale, basis)
    out: dict[str, Any] = {"all": error_stats(real, model)}
    for key, getter in (
        ("moneyness", lambda p: f"{p.kind}:{p.moneyness}"),
        ("tod", lambda p: p.tod),
        ("dte", lambda p: _dte_bucket(p.dte)),
    ):
        groups: dict[str, list[int]] = {}
        for i, p in enumerate(points):
            groups.setdefault(getter(p), []).append(i)
        out[key] = {g: error_stats(real[idx], model[idx]) for g, idx in sorted(groups.items())}
    return out


def build_report(
    points: Sequence[Point],
    filters: FilterCounts,
    *,
    as_of: date,
    sources: dict[str, Any],
    step: int,
    r: float = 0.0,
    q: float = 0.0,
    bootstrap_n: int = 100,
    seed: int = 1,
) -> dict[str, Any]:
    scale = fit_scale(points, basis="trading", r=r, q=q) if points else float("nan")
    ci = bootstrap_scale_ci(points, basis="trading", n=bootstrap_n, seed=seed) if points else {}
    return {
        "as_of": as_of.isoformat(),
        "n_points": len(points),
        "filters": filters.to_dict(),
        "sources": sources,
        "step": step,
        "r": r,
        "q": q,
        "scale_trading": scale,
        "scale_ci": ci,
        "errors": bucket_errors(points, scale, "trading") if points else {},
        "time_basis": compare_bases(points, r=r, q=q),
        "implied_carry": implied_carry(points),
    }


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_jsonable(v) for v in obj]
    return obj


def write_report(report: dict[str, Any], dest_dir: Path, as_of: date) -> tuple[Path, Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = f"option_model_calibration_{as_of.isoformat()}"
    json_path = dest_dir / f"{stem}.json"
    md_path = dest_dir / f"{stem}.md"
    clean = _jsonable(report)
    payload = json.dumps(clean, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)
    json_path.write_text(payload + "\n")
    md_path.write_text(_markdown(clean))
    return md_path, json_path


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Option model calibration ({report['as_of']})",
        "",
        f"Points kept: {report['n_points']}. Filters: `{json.dumps(report['filters'], sort_keys=True)}`.",
        f"Fitted scale (trading T): **{report['scale_trading']}**. CI: `{json.dumps(report['scale_ci'], sort_keys=True)}`.",
        f"r={report['r']} q={report['q']} (not applied from implied carry).",
        "",
        "## Time basis (trading minutes vs calendar / India VIX)",
        "",
    ]
    for bucket, row in report["time_basis"].items():
        lines.append(f"- `{bucket}` n={row.get('n')} winner={row.get('winner')} { {k: row[k] for k in row if k in ('trading', 'calendar')} }")
    lines += ["", "## Implied net carry (q=0, calendar T)", "", json.dumps(report["implied_carry"], sort_keys=True), ""]
    return "\n".join(lines) + "\n"


def opens_from_candles(bars: Sequence[dict[str, Any]]) -> dict[int, float]:
    return {int(b["time"]): float(b["open"]) for b in bars}
