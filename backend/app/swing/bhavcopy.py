"""NSE cash-market bhavcopy: official archive URLs, zip parsing, EQ and BE only.

The live app does not import this module. Raw files stay under data/bhavcopy/.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

HISTORY_FROM = date(2015, 1, 1)
# NSE circular: the old cmDDMMMYYYYbhav file stops after 5 Jul 2024.
# The UDiFF file starts 8 Jul 2024. 6 and 7 Jul 2024 were the weekend.
UDIFF_FROM = date(2024, 7, 8)
KEEP_SERIES = frozenset({"EQ", "BE"})
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
MONTH_INDEX = {name: i + 1 for i, name in enumerate(MONTHS)}

COLUMNS = [
    "date", "symbol", "series", "isin", "open", "high", "low", "close",
    "prev_close", "volume", "turnover",
]

_DATE_TEXT = re.compile(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})")
_ISO_TEXT = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


class NseBlocked(RuntimeError):
    """The official archive refused an automated request. Do not try another host."""


def archive_urls(day: date) -> list[str]:
    """Official NSE URLs for one day, primary first. Near the format change, both are listed."""
    legacy = _legacy_url(day)
    udiff = _udiff_url(day)
    if day < UDIFF_FROM:
        urls = [legacy]
    else:
        urls = [udiff]
    if abs((day - UDIFF_FROM).days) <= 3 and urls[-1] != (udiff if day < UDIFF_FROM else legacy):
        urls.append(udiff if day < UDIFF_FROM else legacy)
    return urls


def _legacy_url(day: date) -> str:
    mon = MONTHS[day.month - 1]
    name = f"cm{day.day:02d}{mon}{day.year}bhav.csv.zip"
    return f"https://archives.nseindia.com/content/historical/EQUITIES/{day.year}/{mon}/{name}"


def _udiff_url(day: date) -> str:
    stamp = f"{day.year}{day.month:02d}{day.day:02d}"
    return f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{stamp}_F_0000.csv.zip"


def raw_path(root: Path, day: date) -> Path:
    return root / "raw" / f"{day.year}" / f"{day.isoformat()}.zip"


def iter_days(start: date, end: date):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def zip_ready(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 200:
        return False
    with path.open("rb") as handle:
        return handle.read(2) == b"PK"


def classify_body(status: int, content: bytes) -> str:
    """zip, absent, blocked, or retry. HTML for a missing day is absent, not a mirror signal."""
    if status in (401, 403):
        return "blocked"
    if status == 404:
        return "absent"
    if status in (408, 429, 500, 502, 503, 504):
        return "retry"
    if status == 200 and content[:2] == b"PK":
        return "zip"
    if status == 200:
        head = content[:800].lower()
        if b"access denied" in head or b"request rejected" in head or b"captcha" in head:
            return "blocked"
        return "absent"
    return "retry"


def parse_nse_date(value: object) -> date | None:
    text = str(value).strip()
    iso = _ISO_TEXT.fullmatch(text)
    if iso:
        return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
    matched = _DATE_TEXT.fullmatch(text)
    if not matched:
        return None
    month = MONTH_INDEX.get(matched.group(2).upper())
    if month is None:
        return None
    return date(int(matched.group(3)), month, int(matched.group(1)))


def parse_bhav_zip(payload: bytes) -> pd.DataFrame:
    """EQ and BE rows from one official zip. The date inside the file wins."""
    if payload[:2] != b"PK":
        raise ValueError("bhavcopy payload is not a zip")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if not names:
            return _empty()
        raw = archive.read(names[0])
    frame = pd.read_csv(io.BytesIO(raw))
    frame.columns = [str(col).strip() for col in frame.columns]
    if "SYMBOL" in frame.columns:
        return _legacy(frame)
    if "TckrSymb" in frame.columns:
        return _udiff(frame)
    raise ValueError("bhavcopy schema is not the legacy or UDiFF cash file")


def _legacy(frame: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame({
        "date": frame["TIMESTAMP"].map(parse_nse_date) if "TIMESTAMP" in frame.columns else None,
        "symbol": _text(frame, "SYMBOL"),
        "series": _text(frame, "SERIES"),
        "isin": _text(frame, "ISIN"),
        "open": _num(frame, "OPEN"),
        "high": _num(frame, "HIGH"),
        "low": _num(frame, "LOW"),
        "close": _num(frame, "CLOSE"),
        "prev_close": _num(frame, "PREVCLOSE"),
        "volume": _num(frame, "TOTTRDQTY"),
        "turnover": _num(frame, "TOTTRDVAL"),
    })
    return _keep(out)


def _udiff(frame: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame({
        "date": frame["TradDt"].map(parse_nse_date) if "TradDt" in frame.columns else None,
        "symbol": _text(frame, "TckrSymb"),
        "series": _text(frame, "SctySrs"),
        "isin": _text(frame, "ISIN"),
        "open": _num(frame, "OpnPric"),
        "high": _num(frame, "HghPric"),
        "low": _num(frame, "LwPric"),
        "close": _num(frame, "ClsPric"),
        "prev_close": _num(frame, "PrvsClsgPric"),
        "volume": _num(frame, "TtlTradgVol"),
        "turnover": _num(frame, "TtlTrfVal"),
    })
    return _keep(out)


def _text(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series([""] * len(frame))
    return frame[column].map(lambda value: "" if pd.isna(value) else str(value).strip().upper())


def _num(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series([float("nan")] * len(frame))
    return pd.to_numeric(frame[column], errors="coerce")


def _keep(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return _empty()
    keep = frame["series"].isin(KEEP_SERIES) & frame["isin"].str.startswith("IN") & frame["symbol"].ne("")
    kept = frame.loc[keep].copy()
    kept = kept.dropna(subset=["date", "close"])
    kept = kept.loc[kept["close"] > 0]
    if kept.empty:
        return _empty()
    for column in ("open", "high", "low", "prev_close", "volume", "turnover"):
        kept[column] = kept[column].astype(float)
    kept["volume"] = kept["volume"].fillna(0.0)
    kept["turnover"] = kept["turnover"].fillna(0.0)
    return kept[COLUMNS].reset_index(drop=True)


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUMNS)


def collapse_series(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per symbol, ISIN, and day. EQ wins a tie. Turnover is the sum of the series."""
    if frame.empty:
        return _empty()
    work = frame.copy()
    sums = work.groupby(["date", "symbol", "isin"], as_index=False)[["volume", "turnover"]].sum()
    work["_eq"] = (work["series"] != "EQ").astype(int)
    chosen = (
        work.sort_values(["_eq", "turnover"], ascending=[True, False])
        .groupby(["date", "symbol", "isin"], sort=False, as_index=False)
        .head(1)
        .drop(columns=["volume", "turnover", "_eq"])
    )
    merged = chosen.merge(sums, on=["date", "symbol", "isin"], how="left")
    return merged[COLUMNS].reset_index(drop=True)


def trading_calendar(frame: pd.DataFrame) -> list[date]:
    if frame.empty:
        return []
    return sorted({day if isinstance(day, date) else date.fromisoformat(str(day)[:10]) for day in frame["date"]})


def coverage_complete(first: date | None, last: date | None, start: date, end: date) -> bool:
    """True when the parsed sessions reach the requested history and the recent tail."""
    if first is None or last is None:
        return False
    return first <= start + timedelta(days=10) and last >= end - timedelta(days=7)
