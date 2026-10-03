"""Open ONE market-data-feed connection for a few seconds, save the raw messages, close it.

Used to check that the Analytics Token works for the v3 feed and to capture real message shapes
as a test fixture (tests never touch the network). Makes exactly one connection, no retries.
Never prints or stores the token or the single-use authorized URL.

    uv run python -m scripts.feed_probe [--seconds 10] [--out tests/fixtures/upstox/feed_probe_<date>.json]
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from google.protobuf.json_format import MessageToDict
from websockets.asyncio.client import connect

from app.config import settings
from app.upstox.feed import MarketDataFeedV3_pb2 as pb
from app.upstox.instruments import IST, NIFTY_INDEX_KEY, VIX_KEY
from app.upstox.redact import redact

AUTHORIZE_URL = "https://api.upstox.com/v3/feed/market-data-feed/authorize"
PROBE_KEYS = [NIFTY_INDEX_KEY, VIX_KEY, "NSE_FO|48704", "NSE_EQ|INE002A01018"]  # Nifty, VIX, Nifty FUT Oct, RELIANCE


def authorize(token: str) -> str:
    resp = httpx.get(
        AUTHORIZE_URL, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, timeout=15.0
    )
    if resp.status_code != 200:
        raise RuntimeError(f"authorize failed: HTTP {resp.status_code}: {redact(resp.text[:300], [token])}")
    return str(resp.json()["data"]["authorized_redirect_uri"])


async def run(uri: str, seconds: float, subscribe: bool = True) -> list[dict[str, Any]]:
    t0 = time.monotonic()
    out: list[dict[str, Any]] = []
    async with connect(uri, open_timeout=15, max_size=None, ping_interval=None) as ws:
        if subscribe:
            req = {"guid": "tradeboss-probe", "method": "sub", "data": {"mode": "full", "instrumentKeys": PROBE_KEYS}}
            await ws.send(json.dumps(req).encode())  # binary frame, as the docs require
            out.append({"dt_ms": round((time.monotonic() - t0) * 1000), "dir": "sent", "kind": "binary",
                        "b64": base64.b64encode(json.dumps(req).encode()).decode(), "decoded": req})
        try:
            async with asyncio.timeout(seconds):
                while True:
                    msg = await ws.recv()
                    raw = msg if isinstance(msg, bytes) else msg.encode()
                    item: dict[str, Any] = {
                        "dt_ms": round((time.monotonic() - t0) * 1000), "dir": "recv",
                        "kind": "binary" if isinstance(msg, bytes) else "text",
                        "b64": base64.b64encode(raw).decode(),
                    }
                    if isinstance(msg, bytes):
                        fr = pb.FeedResponse()
                        fr.ParseFromString(raw)
                        item["decoded"] = MessageToDict(fr, preserving_proto_field_name=True)
                    else:
                        item["decoded"] = msg
                    out.append(item)
        except TimeoutError:
            pass
        await ws.close()  # clean close frame
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    token = settings.upstox_token_value()
    if not token:
        print("No UPSTOX_ANALYTICS_TOKEN in .env", file=sys.stderr)
        return 2
    out = args.out or Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "upstox" / f"feed_probe_{datetime.now(IST).date()}.json"
    try:
        uri = authorize(token)
        msgs = asyncio.run(run(uri, args.seconds))
    except Exception as e:  # noqa: BLE001
        print(f"probe FAILED: {type(e).__name__}: {redact(str(e), [token, locals().get('uri', '')])}", file=sys.stderr)
        return 1
    doc = {
        "captured_at": datetime.now(IST).isoformat(timespec="seconds"),
        "note": "raw frames (base64) + protobuf decoded for reading; token and authorized URL are not stored",
        "subscribed_keys": PROBE_KEYS,
        "messages": msgs,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1))
    recv = [m for m in msgs if m["dir"] == "recv"]
    print(f"saved {len(recv)} received frame(s) to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
