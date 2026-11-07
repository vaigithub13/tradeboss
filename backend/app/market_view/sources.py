"""Outside data for the pre-market view. Every fetch is read-only and fails soft: it returns None and
the view shows that check as unavailable instead of failing the whole card.

* Global and crude: Yahoo Finance chart API (no key). At 08:50 IST the US indices show their last
  session, US futures (ES=F) and Brent (BZ=F) show the overnight move, Nikkei and Hang Seng are trading.
  query1 often answers 429 ("Too Many Requests") to this client; the quote page is the fallback. It
  embeds the same last price and previous close, and no account data is sent.
* FII/DII cash: NSE /api/fiidiiTradeReact (needs the cookies NSE sets on its home page).
* FII index futures long/short: NSE participant-wise OI CSV of the previous trading day.

No account data or token is sent anywhere; requests use a generic browser User-Agent.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
TIMEOUT_S = 10.0
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_QUOTE = "https://finance.yahoo.com/quote/{symbol}/"
GLOBAL_SYMBOLS: dict[str, tuple[str, str]] = {
    "sp500": ("^GSPC", "S&P 500"),
    "nasdaq": ("^IXIC", "Nasdaq"),
    "us_futures": ("ES=F", "US futures"),
    "nikkei": ("^N225", "Nikkei"),
    "hang_seng": ("^HSI", "Hang Seng"),
}
BRENT = ("BZ=F", "Brent crude")
NSE_HOME = "https://www.nseindia.com"
NSE_FII_DII = "https://www.nseindia.com/api/fiidiiTradeReact"
PARTICIPANT_OI_URLS = (
    "https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_{ddmmyyyy}.csv",
    "https://archives.nseindia.com/content/nsccl/fao_participant_oi_{ddmmyyyy}.csv",
)


def make_http() -> httpx.Client:
    return httpx.Client(
        timeout=TIMEOUT_S,
        follow_redirects=True,
        headers={"User-Agent": UA, "Accept": "application/json,text/csv,*/*", "Accept-Language": "en-US,en;q=0.9"},
    )


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


# ------------------------------------------------------------------ Yahoo
def parse_yahoo(body: Any, symbol: str, name: str) -> dict[str, Any] | None:
    try:
        meta = body["chart"]["result"][0]["meta"]
    except (KeyError, IndexError, TypeError):
        return None
    last = _num(meta.get("regularMarketPrice"))
    prev = _num(meta.get("chartPreviousClose")) or _num(meta.get("previousClose"))
    if last is None or not prev:
        return None
    return {
        "symbol": symbol,
        "name": name,
        "last": last,
        "prev_close": prev,
        "change_pct": (last / prev - 1.0) * 100.0,
        "time": int(meta["regularMarketTime"]) if meta.get("regularMarketTime") else None,
    }


def parse_yahoo_page(html: str, symbol: str, name: str) -> dict[str, Any] | None:
    """Last price and previous close from the quote page's embedded spark meta.

    The page inlines that payload as escaped JSON (`\\"regularMarketPrice\\":7376.86`). A bare
    number is required so a streamer card (`\\"regularMarketPrice\\":{\\"raw\\":...`) is skipped.
    """
    marker = '\\"symbol\\":\\"' + symbol + '\\"'
    start = 0
    while True:
        i = html.find(marker, start)
        if i < 0:
            return None
        window = html[i:i + 2500]

        def grab(key: str) -> float | None:
            m = re.search(r'\\"' + re.escape(key) + r'\\":(-?\d+(?:\.\d+)?)', window)
            return float(m.group(1)) if m else None

        last = grab("regularMarketPrice")
        prev = grab("chartPreviousClose") or grab("previousClose")
        if last is not None and prev:
            t = grab("regularMarketTime")
            return {
                "symbol": symbol,
                "name": name,
                "last": last,
                "prev_close": prev,
                "change_pct": (last / prev - 1.0) * 100.0,
                "time": int(t) if t is not None else None,
            }
        start = i + len(marker)


def _chart_quote(http: httpx.Client, symbol: str, name: str) -> dict[str, Any] | None:
    try:
        resp = http.get(YAHOO_CHART.format(symbol=symbol), params={"range": "1d", "interval": "5m"})
    except httpx.HTTPError as e:
        log.info("yahoo %s chart failed: %s", symbol, type(e).__name__)
        return None
    if resp.status_code != 200:
        log.info("yahoo %s chart: HTTP %s", symbol, resp.status_code)
        return None
    try:
        return parse_yahoo(resp.json(), symbol, name)
    except ValueError:
        return None


def _page_quote(http: httpx.Client, symbol: str, name: str) -> dict[str, Any] | None:
    try:
        resp = http.get(
            YAHOO_QUOTE.format(symbol=quote(symbol, safe="")),
            headers={"Accept": "text/html"},
            timeout=20.0,
        )
    except httpx.HTTPError as e:
        log.warning("yahoo %s failed: %s", symbol, type(e).__name__)
        return None
    if resp.status_code != 200:
        log.warning("yahoo %s page: HTTP %s", symbol, resp.status_code)
        return None
    got = parse_yahoo_page(resp.text, symbol, name)
    if got is None:
        log.warning("yahoo %s page: no quote in the response", symbol)
    return got


def fetch_quote(http: httpx.Client, symbol: str, name: str) -> dict[str, Any] | None:
    """Last price vs the previous session's close.

    The chart call (range=1d, so chartPreviousClose is the previous session) is first. A 429 or any
    other miss falls through to the quote page; a failure of both leaves this quote unavailable.
    """
    return _chart_quote(http, symbol, name) or _page_quote(http, symbol, name)


# ------------------------------------------------------------------ NSE
def parse_fii_dii(body: Any) -> dict[str, Any] | None:
    if not isinstance(body, list):
        return None
    out: dict[str, Any] = {}
    for row in body:
        if not isinstance(row, dict):
            continue
        cat = str(row.get("category", "")).upper()
        net = _num(row.get("netValue"))
        if net is None:
            continue
        if cat.startswith("FII") or "FPI" in cat:
            out["fii_net"] = net
            out["date"] = row.get("date")
        elif cat.startswith("DII"):
            out["dii_net"] = net
            out.setdefault("date", row.get("date"))
    return out if "fii_net" in out else None


def fetch_fii_dii(http: httpx.Client) -> dict[str, Any] | None:
    """Previous session's provisional FII/DII cash figures (₹ crore)."""
    try:
        http.get(NSE_HOME, headers={"Accept": "text/html"})  # sets the cookies the API checks
        resp = http.get(NSE_FII_DII, headers={"Referer": NSE_HOME + "/reports/fii-dii"})
        if resp.status_code != 200:
            log.warning("nse fii/dii: HTTP %s", resp.status_code)
            return None
        return parse_fii_dii(resp.json())
    except (httpx.HTTPError, ValueError) as e:
        log.warning("nse fii/dii failed: %s", type(e).__name__)
        return None


def parse_participant_oi(text: str) -> dict[str, Any] | None:
    """FII index-futures long / short contracts from fao_participant_oi_DDMMYYYY.csv."""
    rows = list(csv.reader(io.StringIO(text)))
    header_at = next((i for i, r in enumerate(rows) if r and r[0].strip().lower() == "client type"), None)
    if header_at is None:
        return None
    header = [h.strip().lower() for h in rows[header_at]]
    try:
        li, si = header.index("future index long"), header.index("future index short")
    except ValueError:
        return None
    for r in rows[header_at + 1:]:
        if r and r[0].strip().upper() == "FII":
            long_, short = _num(r[li]), _num(r[si])
            if long_ is None or short is None or long_ + short <= 0:
                return None
            return {"fii_index_long": long_, "fii_index_short": short, "fii_long_pct": long_ / (long_ + short) * 100.0}
    return None


def fetch_participant_oi(
    http: httpx.Client, before: date, is_trading_day: Callable[[date], bool], max_back: int = 7
) -> dict[str, Any] | None:
    """The latest participant-OI file of a trading day strictly before `before`."""
    d = before
    for _ in range(max_back):
        d -= timedelta(days=1)
        if not is_trading_day(d):
            continue
        for url in PARTICIPANT_OI_URLS:
            try:
                resp = http.get(url.format(ddmmyyyy=d.strftime("%d%m%Y")))
            except httpx.HTTPError:
                continue
            if resp.status_code == 200:
                parsed = parse_participant_oi(resp.text)
                if parsed:
                    return {**parsed, "date": d.isoformat()}
        return None  # the last trading day's file is missing: do not reach further back
    return None
