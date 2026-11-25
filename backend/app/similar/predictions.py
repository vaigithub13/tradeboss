"""Append-only similar-pattern snapshots. Record only: no orders, no slots.

A saved prediction line is never rewritten. The end-of-day review is a separate
file, created once. Points are hypothetical index points, with no costs.
"""

from __future__ import annotations

import json
import math
import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from app.data.store import CandleStore
from app.similar.days import build_day_rows, match_days
from app.similar.features import (
    INDEX_SYMBOLS,
    VIX_SYMBOL,
    bar_seconds,
    candle_features,
    ist_day,
    similarity,
    stack_windows,
    wilder_atr,
    window_ends,
)
from app.similar.match import base_returns, euclidean, excursion, forward_slice, pick_matches
from app.similar.service import load_normal

IST = ZoneInfo("Asia/Kolkata")
POINTS_LABEL = "Hypothetical index points. No costs. Not a trade."
RECORD_NOTE = "Record only. Not a signal. No order is sent."
SNAPSHOT_CLOCKS = ("09:30", "10:15", "11:15", "12:15", "13:15", "14:15")
DAY_CLOCKS = ("09:30", "10:15")
CANDLE_TIMEFRAME = "15m"
CANDLE_WINDOW = 8
CANDLE_HORIZON = 4
LIBRARY_VERSION = "2026-11-25.n8"
INSUFFICIENT = "insufficient window"
NONE_LO = 45.0
NONE_HI = 55.0
VERDICT_N = 50
SESSION_LAST_MINUTE = 15 * 60 + 15


def study_settings(*, kind: str) -> dict:
    """The settings a new line is scored under. Candles match the 15m N=8 study."""
    if kind == "candles":
        return {
            "timeframe": CANDLE_TIMEFRAME,
            "window": CANDLE_WINDOW,
            "horizon_bars": CANDLE_HORIZON,
            "library_version": LIBRARY_VERSION,
            "span_sessions": False,
        }
    return {
        "timeframe": "5m",
        "window": None,
        "horizon_bars": None,
        "library_version": LIBRARY_VERSION,
        "span_sessions": False,
    }


def settings_of(record: dict) -> dict:
    """Settings stored on the line, or the pre-version shape for an older line."""
    raw = record.get("settings")
    if isinstance(raw, dict) and raw.get("library_version"):
        return raw
    return {
        "timeframe": record.get("timeframe") or record.get("kind"),
        "window": record.get("window"),
        "horizon_bars": record.get("horizon_bars"),
        "library_version": "unversioned",
        "span_sessions": bool(record.get("spans_gap")),
    }


def settings_key(record: dict) -> str:
    """One bucket per timeframe, window, horizon, and library version."""
    settings = settings_of(record)
    return "|".join([
        str(settings.get("timeframe")),
        f"n={settings.get('window')}",
        f"h={settings.get('horizon_bars')}",
        str(settings.get("library_version")),
        f"gap={int(bool(settings.get('span_sessions')))}",
    ])


def direction_of(pct_up: float | None) -> str:
    """Up or down outside 45–55%. Inside that band, or with no samples, none."""
    if pct_up is None or NONE_LO <= float(pct_up) <= NONE_HI:
        return "none"
    return "up" if float(pct_up) > NONE_HI else "down"


def confidence_of(pct_up: float | None, direction: str) -> float | None:
    if pct_up is None:
        return None
    if direction == "down":
        return round(100.0 - float(pct_up), 1)
    return round(float(pct_up), 1)


def prediction_id(day: str, symbol: str, kind: str, clock: str) -> str:
    return f"{day}|{symbol}|{kind}|{clock}"


def predictions_dir(root: Path) -> Path:
    return root / "predictions"


def log_path(root: Path, day: str) -> Path:
    return predictions_dir(root) / f"{day}.jsonl"


def review_path(root: Path, day: str) -> Path:
    return predictions_dir(root) / "reviews" / f"{day}.json"


def clock_at(day: date, clock: str) -> datetime:
    hour, minute = (int(part) for part in clock.split(":"))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)


def snapshot_cursor(day: date, clock: str) -> int:
    """Last minute of the bar that closes at ``clock``, so that bar is complete."""
    return int(clock_at(day, clock).timestamp()) - 60


def candle_bar_start(day: date, clock: str) -> int:
    return int(clock_at(day, clock).timestamp()) - bar_seconds(CANDLE_TIMEFRAME)


def _round(value: float | None, places: int = 2) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), places)


def _closed_today(times: np.ndarray, q_end: int) -> int:
    days = ist_day(times)
    today = int(days[q_end])
    return int(np.sum((days == today) & (times <= int(times[q_end]))))


def build_candle_record(
    bars: dict[str, np.ndarray],
    *,
    symbol: str,
    day: str,
    clock: str,
    as_of: int,
) -> dict | None:
    """One 15-minute snapshot. The window stays inside today's session.

    Fewer than eight closed 15-minute bars (09:30 has one, 10:15 has four) is
    recorded as insufficient window, not as a call.
    """
    times = bars["times"]
    if len(times) == 0:
        return None
    target = candle_bar_start(date.fromisoformat(day), clock)
    hits = np.flatnonzero(times == target)
    if len(hits) == 0:
        return None
    q_end = int(hits[0])
    if _closed_today(times, q_end) < CANDLE_WINDOW:
        return _insufficient(symbol=symbol, day=day, clock=clock, as_of=as_of, query_end=int(times[q_end]))
    ends = window_ends(times, CANDLE_WINDOW, CANDLE_TIMEFRAME, span_sessions=False)
    if q_end not in set(int(item) for item in ends):
        return _insufficient(symbol=symbol, day=day, clock=clock, as_of=as_of, query_end=int(times[q_end]))
    features = candle_features(bars["open"], bars["high"], bars["low"], bars["close"])
    vectors = stack_windows(features, ends, CANDLE_WINDOW)
    pos = int(np.flatnonzero(ends == q_end)[0])
    query_start_i = q_end - CANDLE_WINDOW + 1
    query_start = int(times[query_start_i])
    dist = euclidean(vectors[pos], vectors)
    chosen = pick_matches(dist, times[ends], query_start=query_start, k=10)
    atr = wilder_atr(bars["high"], bars["low"], bars["close"])
    ups: list[bool] = []
    matches = []
    dim = int(vectors.shape[1])
    for rank in chosen:
        end = int(ends[rank])
        span = forward_slice(times, end, CANDLE_HORIZON, as_of=as_of, intraday=True)
        up = None
        if span is not None:
            _, last = span
            stats = excursion(bars["high"], bars["low"], bars["close"], atr, end, last)
            up = bool(stats["return_pct"] > 0)
            ups.append(up)
        matches.append({
            "start": int(times[end - CANDLE_WINDOW + 1]),
            "end": int(times[end]),
            "similarity": round(similarity(float(dist[rank]), dim), 1),
            "up": up,
        })
    pct = None if not ups else float(np.mean(ups) * 100.0)
    base = base_returns(bars["close"], times, ends, CANDLE_HORIZON, as_of=as_of, intraday=True)
    base_pct = None if len(base) == 0 else float(np.mean(base > 0) * 100.0)
    direction = direction_of(pct)
    return _record(
        symbol=symbol,
        day=day,
        clock=clock,
        kind="candles",
        as_of=as_of,
        query_start=query_start,
        query_end=int(times[q_end]),
        spans_gap=False,
        status="call",
        matches=matches,
        up_count=sum(ups),
        match_n=len(ups),
        direction=direction,
        confidence=confidence_of(pct, direction),
        base_rate=_round(base_pct, 1),
        horizon_label="1h",
        horizon_bars=CANDLE_HORIZON,
    )


def build_day_record(
    bars: dict[str, np.ndarray],
    *,
    symbol: str,
    day: str,
    clock: str,
    as_of: int,
    vix_times: np.ndarray | None = None,
    vix_close: np.ndarray | None = None,
    candle_end: int | None = None,
) -> dict | None:
    if clock not in DAY_CLOCKS:
        return None
    hour, minute = (int(part) for part in clock.split(":"))
    rows = build_day_rows(
        bars["times"], bars["open"], bars["high"], bars["low"], bars["close"],
        clock_minute=hour * 60 + minute,
        vix_times=vix_times,
        vix_close=vix_close,
    )
    query_day = int(ist_day(np.array([as_of], dtype=np.int64))[0])
    found = match_days(rows, query_day=query_day)
    packed = found["matches"]
    if candle_end is None:
        return None
    ups = [bool(row["up_close"]) for row in packed]
    pct = None if not ups else float(np.mean(ups) * 100.0)
    outcome = found["outcomes"].get("up_close") or {}
    base = outcome.get("base_rate")
    base_pct = None if base is None else float(base) * 100.0
    direction = direction_of(pct)
    matches = [
        {"date": row["date"], "end": int(row["end_time"]), "similarity": row["similarity"], "up": bool(row["up_close"])}
        for row in packed
    ]
    return _record(
        symbol=symbol,
        day=day,
        clock=clock,
        kind="days",
        as_of=as_of,
        query_start=candle_end,
        query_end=candle_end,
        spans_gap=False,
        status="call",
        matches=matches,
        up_count=sum(ups),
        match_n=len(ups),
        direction=direction,
        confidence=confidence_of(pct, direction),
        base_rate=_round(base_pct, 1),
        horizon_label="day close",
        horizon_bars=None,
    )


def _insufficient(*, symbol: str, day: str, clock: str, as_of: int, query_end: int) -> dict:
    return _record(
        symbol=symbol,
        day=day,
        clock=clock,
        kind="candles",
        as_of=as_of,
        query_start=query_end,
        query_end=query_end,
        spans_gap=False,
        status=INSUFFICIENT,
        matches=[],
        up_count=0,
        match_n=0,
        direction="none",
        confidence=None,
        base_rate=None,
        horizon_label="1h",
        horizon_bars=CANDLE_HORIZON,
    )


def _record(**fields: object) -> dict:
    day = str(fields["day"])
    symbol = str(fields["symbol"])
    kind = str(fields["kind"])
    clock = str(fields["clock"])
    settings = study_settings(kind=kind)
    return {
        "id": prediction_id(day, symbol, kind, clock),
        "saved_at": datetime.now(IST).isoformat(timespec="seconds"),
        "day": day,
        "time": clock,
        "slot": clock,
        "symbol": symbol,
        "kind": kind,
        "status": fields.get("status") or "call",
        "timeframe": settings["timeframe"],
        "window": settings["window"],
        "horizon_bars": fields["horizon_bars"],
        "horizon_label": fields["horizon_label"],
        "settings": settings,
        "as_of": fields["as_of"],
        "query": {"start": fields["query_start"], "end": fields["query_end"]},
        "spans_gap": fields["spans_gap"],
        "matches": fields["matches"],
        "match_n": fields["match_n"],
        "up_count": fields["up_count"],
        "direction": fields["direction"],
        "confidence": fields["confidence"],
        "base_rate": fields["base_rate"],
        "note": RECORD_NOTE,
        "points_label": POINTS_LABEL,
    }


def read_predictions(root: Path, day: str | None = None) -> list[dict]:
    if day is not None:
        return _read_jsonl(log_path(root, day))
    folder = predictions_dir(root)
    if not folder.exists():
        return []
    rows: list[dict] = []
    for path in sorted(folder.glob("*.jsonl")):
        rows.extend(_read_jsonl(path))
    return rows


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def append_prediction(root: Path, record: dict) -> bool:
    """Append one line. An id that is already in the file is left untouched."""
    path = log_path(root, str(record["day"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    with lock_path.open("a") as lock:
        import fcntl

        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if any(row.get("id") == record["id"] for row in _read_jsonl(path)):
                return False
            line = json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n"
            with path.open("a") as out:
                out.write(line)
            return True
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _index_at(times: np.ndarray, stamp: int) -> int | None:
    hits = np.flatnonzero(times == int(stamp))
    if len(hits) == 0:
        return None
    return int(hits[0])


def _closed(times: np.ndarray, index: int, closed_through: int | None) -> bool:
    if closed_through is None:
        return True
    return int(times[index]) + bar_seconds(CANDLE_TIMEFRAME) <= int(closed_through)


def _scored(record: dict, *, hit: bool | None, points: float | None, raw_points: float | None, adverse_1h: float | None) -> dict:
    return {
        "id": record["id"],
        "day": record["day"],
        "symbol": record["symbol"],
        "kind": record["kind"],
        "slot": record["slot"],
        "direction": str(record["direction"]),
        "status": record.get("status") or "call",
        "settings": settings_of(record),
        "base_rate": record.get("base_rate"),
        "hit": hit,
        "points": points,
        "raw_points": raw_points,
        "adverse_1h": adverse_1h,
        "points_label": POINTS_LABEL,
    }


def _candle_horizon(record: dict) -> int:
    value = record.get("horizon_bars")
    if value is None:
        return CANDLE_HORIZON
    return int(value)


def score_record(record: dict, bars: dict[str, np.ndarray], *, closed_through: int | None = None) -> dict | None:
    """Hit, hypothetical points, and the 1h adverse move. None until the horizon bar has closed."""
    if record.get("status") == INSUFFICIENT:
        return _scored(record, hit=None, points=None, raw_points=None, adverse_1h=None)
    times = bars["times"]
    end = _index_at(times, int(record["query"]["end"]))
    if end is None:
        return None
    direction = str(record["direction"])
    if record["kind"] == "candles":
        horizon = _candle_horizon(record)
        last = end + horizon
        if last >= len(times) or not _closed(times, last, closed_through):
            return None
        if int(ist_day(times[end:end + 1])[0]) != int(ist_day(times[last:last + 1])[0]):
            return None
        ref = float(bars["close"][end])
        raw = float(bars["close"][last]) - ref
        adverse = _adverse(direction, ref, bars["high"][end + 1:last + 1], bars["low"][end + 1:last + 1])
    else:
        days = ist_day(times)
        same = np.flatnonzero(days == days[end])
        if len(same) == 0:
            return None
        last_i = int(same[-1])
        minute = int(((int(times[last_i]) + 19_800) % 86_400) // 60)
        if minute < SESSION_LAST_MINUTE or not _closed(times, last_i, closed_through):
            return None
        ref = float(bars["close"][end])
        raw = float(bars["close"][last_i]) - ref
        hour = end + CANDLE_HORIZON
        adverse = None
        if hour < len(times) and int(days[hour]) == int(days[end]):
            adverse = _adverse(direction, ref, bars["high"][end + 1:hour + 1], bars["low"][end + 1:hour + 1])
    hit = _hit(direction, raw)
    points = None if direction == "none" else _round(raw if direction == "up" else -raw, 2)
    return _scored(record, hit=hit, points=points, raw_points=_round(raw, 2), adverse_1h=_round(adverse, 2))


def _hit(direction: str, raw: float) -> bool | None:
    if direction == "none":
        return None
    if raw == 0:
        return False
    return raw > 0 if direction == "up" else raw < 0


def _adverse(direction: str, ref: float, high: np.ndarray, low: np.ndarray) -> float | None:
    if direction == "none" or len(high) == 0:
        return None
    if direction == "up":
        return max(0.0, ref - float(np.min(low)))
    return max(0.0, float(np.max(high)) - ref)


def review_verdict(hit_rate: float | None, base_rate: float | None, n: int) -> str | None:
    """Plain verdict once 50 directional calls exist. Fewer than that stays blank."""
    if n < VERDICT_N or hit_rate is None or base_rate is None:
        return None
    gap = hit_rate - base_rate
    p = min(0.999, max(0.001, base_rate / 100.0))
    se = math.sqrt(p * (1.0 - p) / n) * 100.0
    z = gap / se if se else 0.0
    if gap > 3.0 and z >= 1.96:
        return "better than base rate"
    return "no better than coin flip"


def _block(rows: list[dict], *, include_none: bool) -> dict:
    chosen = rows if include_none else [row for row in rows if row.get("direction") != "none"]
    directional = [row for row in chosen if row.get("direction") != "none" and row.get("hit") is not None]
    hits = sum(1 for row in directional if row["hit"])
    n = len(chosen)
    hit_n = len(directional) if not include_none else n
    chances = []
    for row in directional:
        base = row.get("base_rate")
        if base is None:
            continue
        chances.append(float(base) if row["direction"] == "up" else 100.0 - float(base))
    base_rate = None if not chances else round(float(np.mean(chances)), 1)
    hit_rate = None if hit_n == 0 else round(100.0 * hits / hit_n, 1)
    points = 0.0
    for row in chosen:
        if row.get("direction") == "none":
            if include_none and row.get("raw_points") is not None:
                points += float(row["raw_points"])
        elif row.get("points") is not None:
            points += float(row["points"])
    verdict_n = len(directional) if not include_none else n
    return {
        "predictions": n,
        "hits": hits,
        "hit_rate": hit_rate,
        "base_rate": base_rate,
        "points": round(points, 2),
        "verdict": review_verdict(hit_rate, base_rate, verdict_n) if not include_none else None,
    }


def _pack_group(rows: list[dict]) -> dict:
    def pack(group: list[dict]) -> dict:
        return {"excluded": _block(group, include_none=False), "included": _block(group, include_none=True)}

    by_month: dict[str, list[dict]] = {}
    by_week: dict[str, list[dict]] = {}
    by_slot: dict[str, list[dict]] = {}
    for row in rows:
        day = date.fromisoformat(str(row["day"]))
        by_month.setdefault(f"{day.year}-{day.month:02d}", []).append(row)
        iso = day.isocalendar()
        by_week.setdefault(f"{iso.year}-W{iso.week:02d}", []).append(row)
        by_slot.setdefault(str(row["slot"]), []).append(row)
    return {
        "overall": pack(rows),
        "month": [{"key": key, **pack(by_month[key])} for key in sorted(by_month)],
        "week": [{"key": key, **pack(by_week[key])} for key in sorted(by_week)],
        "slot": [{"key": key, **pack(by_slot[key])} for key in SNAPSHOT_CLOCKS if key in by_slot],
    }


def tally(rows: list[dict]) -> dict:
    """Hit rates stay inside one settings version. Versions are not pooled."""
    grouped: dict[str, list[dict]] = {}
    order: list[str] = []
    for row in rows:
        if row.get("status") == INSUFFICIENT:
            key = settings_key(row)
            grouped.setdefault(key, [])
            if key not in order:
                order.append(key)
            continue
        key = settings_key(row)
        if key not in grouped:
            order.append(key)
            grouped[key] = []
        grouped[key].append(row)
    versions = []
    for key in order:
        group = grouped[key]
        sample = next((row for row in rows if settings_key(row) == key), group[0] if group else None)
        versions.append({
            "key": key,
            "settings": settings_of(sample) if sample else {},
            "insufficient": sum(1 for row in rows if settings_key(row) == key and row.get("status") == INSUFFICIENT),
            **_pack_group(group),
        })
    return {"versions": versions, "points_label": POINTS_LABEL}


def _day_bars(store: CandleStore, symbol: str, day: str) -> dict[str, np.ndarray]:
    start = int(datetime.fromisoformat(f"{day}T09:00:00+05:30").timestamp())
    end = int(datetime.fromisoformat(f"{day}T15:40:00+05:30").timestamp())
    from app.data.service import get_candles

    result = get_candles(
        store, symbol, CANDLE_TIMEFRAME, session_types=("normal",), from_time=start, to_time=end,
    )
    return {
        "times": np.array([c["time"] for c in result.candles], dtype=np.int64),
        "high": np.array([c["high"] for c in result.candles], dtype=np.float64),
        "low": np.array([c["low"] for c in result.candles], dtype=np.float64),
        "close": np.array([c["close"] for c in result.candles], dtype=np.float64),
    }


def score_day(root: Path, store: CandleStore, day: str, *, closed_through: int | None = None) -> list[dict] | None:
    """Score every saved prediction. None if any horizon is still in the future."""
    records = read_predictions(root, day)
    if not records:
        return None
    cache: dict[str, dict[str, np.ndarray]] = {}
    scored = []
    for record in records:
        symbol = str(record["symbol"])
        if symbol not in cache:
            try:
                cache[symbol] = _day_bars(store, symbol, day)
            except Exception:
                return None
        row = score_record(record, cache[symbol], closed_through=closed_through)
        if row is None:
            return None
        scored.append(row)
    return scored


def write_review_if_due(root: Path, store: CandleStore, *, now: datetime | None = None) -> bool:
    """Write today's review once, after 15:30 IST. A file that exists is not opened for writing."""
    moment = now or datetime.now(IST)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=IST)
    local = moment.astimezone(IST)
    if local.hour * 60 + local.minute < 15 * 60 + 30:
        return False
    day = local.date().isoformat()
    path = review_path(root, day)
    if path.exists():
        return False
    rows = score_day(root, store, day, closed_through=int(local.timestamp()))
    if rows is None:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"day": day, "written_at": datetime.now(IST).isoformat(timespec="seconds"), "points_label": POINTS_LABEL, "rows": rows}
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as handle:
        json.dump(payload, handle, separators=(",", ":"))
        handle.write("\n")
    return True


def read_reviews(root: Path) -> list[dict]:
    folder = predictions_dir(root) / "reviews"
    if not folder.exists():
        return []
    rows: list[dict] = []
    for path in sorted(folder.glob("*.json")):
        payload = json.loads(path.read_text())
        rows.extend(payload.get("rows") or [])
    return rows


def today_table(root: Path, store: CandleStore | None, day: str) -> list[dict]:
    """Saved predictions plus a score when the horizon already exists. The log is not written."""
    saved = {row["id"]: row for row in read_predictions(root, day)}
    frozen = {}
    path = review_path(root, day)
    if path.exists():
        for row in json.loads(path.read_text()).get("rows") or []:
            frozen[row["id"]] = row
    bars: dict[str, dict[str, np.ndarray]] = {}
    table = []
    for record in saved.values():
        scored = frozen.get(record["id"])
        if scored is None and store is not None and record["id"] not in frozen:
            symbol = str(record["symbol"])
            if symbol not in bars:
                try:
                    bars[symbol] = _day_bars(store, symbol, day)
                except Exception:
                    bars[symbol] = {"times": np.empty(0, dtype=np.int64), "high": np.empty(0), "low": np.empty(0), "close": np.empty(0)}
            scored = score_record(record, bars[symbol], closed_through=int(datetime.now(IST).timestamp()))
        table.append({
            "id": record["id"],
            "slot": record["slot"],
            "symbol": record["symbol"],
            "kind": record["kind"],
            "direction": record["direction"],
            "confidence": record["confidence"],
            "base_rate": record["base_rate"],
            "up_count": record["up_count"],
            "match_n": record["match_n"],
            "label": _label(int(record["up_count"]), int(record["match_n"]), record.get("status")),
            "status": record.get("status") or "call",
            "settings": settings_of(record),
            "query_end": record["query"]["end"],
            "query_start": record["query"]["start"],
            "hit": None if scored is None else scored["hit"],
            "points": None if scored is None else scored["points"],
            "adverse_1h": None if scored is None else scored["adverse_1h"],
            "pending": scored is None,
            "points_label": POINTS_LABEL,
        })
    table.sort(key=lambda row: (str(row["slot"]), str(row["symbol"]), str(row["kind"])))
    return table


def _label(up_count: int, match_n: int, status: object = None) -> str:
    if status == INSUFFICIENT:
        return INSUFFICIENT
    if match_n <= 0:
        return "no match"
    return f"{up_count}/{match_n} up"


def take_snapshots(store: CandleStore, *, day: date, clock: str, symbols: tuple[str, ...] = tuple(sorted(INDEX_SYMBOLS))) -> list[dict]:
    """Build the records for one clock. Bars after the clock are not loaded."""
    as_of = snapshot_cursor(day, clock)
    day_text = day.isoformat()
    records = []
    for symbol in symbols:
        if symbol not in INDEX_SYMBOLS:
            continue
        try:
            bars = load_normal(store, symbol, CANDLE_TIMEFRAME, as_of=as_of)
        except Exception:
            continue
        candle = build_candle_record(bars, symbol=symbol, day=day_text, clock=clock, as_of=as_of)
        if candle is not None:
            records.append(candle)
        if clock not in DAY_CLOCKS:
            continue
        try:
            intraday = load_normal(store, symbol, "5m", as_of=as_of)
        except Exception:
            continue
        try:
            vix = load_normal(store, VIX_SYMBOL, "5m", as_of=as_of)
        except Exception:
            vix = {"times": np.empty(0, dtype=np.int64), "close": np.empty(0, dtype=np.float64)}
        candle_end = int(candle["query"]["end"]) if candle is not None else candle_bar_start(day, clock)
        day_record = build_day_record(
            intraday,
            symbol=symbol,
            day=day_text,
            clock=clock,
            as_of=as_of,
            vix_times=vix["times"],
            vix_close=vix["close"],
            candle_end=candle_end,
        )
        if day_record is not None:
            records.append(day_record)
    return records
