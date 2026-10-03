"""Decode Upstox v3 feed frames (protobuf) into plain objects - and encode them for tests.

Everything the pipeline needs from a `FeedResponse`:

* `kind`: "initial_feed" (the snapshot sent right after subscribing; proto3 omits type 0),
  "live_feed" or "market_info"
* `current_ts`: the server's clock (ms) - used for the trading day, the F6 future-tick check and
  latency logging; never for bar times
* per instrument: last trade (ltt/ltp/ltq), cumulative volume `vtt`, `oi` and the exchange
  1-minute OHLC entry (interval "I1")
* for market_info: segment statuses
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from google.protobuf.json_format import ParseDict

from app.live.model import I1Bar
from app.upstox.feed import MarketDataFeedV3_pb2 as pb

FrameKind = Literal["initial_feed", "live_feed", "market_info"]

OPENISH_STATUSES = frozenset({"PRE_OPEN_START", "PRE_OPEN_END", "NORMAL_OPEN", "CLOSING_START"})
CLOSED_STATUSES = frozenset({"NORMAL_CLOSE", "CLOSING_END"})


class FrameError(ValueError):
    pass


@dataclass(frozen=True)
class FeedItem:
    key: str
    ltp: float
    ltt: int  # 0 when absent
    ltq: int
    vtt: int | None  # None for indices / ltpc-only frames
    oi: float | None
    i1: I1Bar | None
    has_volume: bool  # marketFF (stocks, futures, options) vs indexFF


@dataclass(frozen=True)
class Frame:
    kind: FrameKind
    current_ts: int
    items: tuple[FeedItem, ...] = ()
    segments: dict[str, str] = field(default_factory=dict)


_KINDS: dict[int, FrameKind] = {0: "initial_feed", 1: "live_feed", 2: "market_info"}


def decode_frame(raw: bytes) -> Frame:
    msg = pb.FeedResponse()
    try:
        msg.ParseFromString(raw)
    except Exception as exc:  # noqa: BLE001 - protobuf raises DecodeError
        raise FrameError(f"not a FeedResponse: {exc}") from exc
    kind = _KINDS.get(int(msg.type))
    if kind is None:
        raise FrameError(f"unknown frame type {msg.type}")
    segments = {k: pb.MarketStatus.Name(v) for k, v in msg.marketInfo.segmentStatus.items()}
    items = [_item(key, feed) for key, feed in msg.feeds.items()]
    return Frame(kind, int(msg.currentTs), tuple(x for x in items if x is not None), segments)


def _item(key: str, feed: "pb.Feed") -> FeedItem | None:
    which = feed.WhichOneof("FeedUnion")
    if which == "ltpc":
        p = feed.ltpc
        return FeedItem(key, p.ltp, p.ltt, p.ltq, None, None, None, False)
    if which == "firstLevelWithGreeks":
        f = feed.firstLevelWithGreeks
        return FeedItem(key, f.ltpc.ltp, f.ltpc.ltt, f.ltpc.ltq, int(f.vtt), f.oi or None, None, True)
    if which != "fullFeed":
        return None
    ff = feed.fullFeed
    kind = ff.WhichOneof("FullFeedUnion")
    if kind == "marketFF":
        m = ff.marketFF
        return FeedItem(key, m.ltpc.ltp, m.ltpc.ltt, m.ltpc.ltq, int(m.vtt), m.oi or None, _i1(m.marketOHLC), True)
    if kind == "indexFF":
        i = ff.indexFF
        return FeedItem(key, i.ltpc.ltp, i.ltpc.ltt, i.ltpc.ltq, None, None, _i1(i.marketOHLC), False)
    return None


def _i1(m: "pb.MarketOHLC") -> I1Bar | None:
    for o in m.ohlc:
        if o.interval == "I1":
            return I1Bar(int(o.ts), o.open, o.high, o.low, o.close, int(o.vol))
    return None


# ---------------------------------------------------------------- encoders (tests, replays)
def encode_market_info(segments: dict[str, str], current_ts: int) -> bytes:
    msg = pb.FeedResponse()
    ParseDict({"type": "market_info", "currentTs": str(current_ts), "marketInfo": {"segmentStatus": segments}}, msg)
    return msg.SerializeToString()


def encode_item(item: FeedItem) -> dict:
    ohlc = []
    if item.i1 is not None:
        i = item.i1
        ohlc.append({"interval": "I1", "open": i.open, "high": i.high, "low": i.low, "close": i.close,
                     "vol": str(i.vol), "ts": str(i.ts)})
    ltpc = {"ltp": item.ltp, "ltt": str(item.ltt), "ltq": str(item.ltq)}
    if item.has_volume:
        ff: dict = {"marketFF": {"ltpc": ltpc, "marketOHLC": {"ohlc": ohlc}, "vtt": str(item.vtt or 0)}}
        if item.oi is not None:
            ff["marketFF"]["oi"] = item.oi
    else:
        ff = {"indexFF": {"ltpc": ltpc, "marketOHLC": {"ohlc": ohlc}}}
    return {"fullFeed": ff, "requestMode": "full_d5"}


def encode_feed(items: list[FeedItem], current_ts: int, *, snapshot: bool = False) -> bytes:
    msg = pb.FeedResponse()
    d: dict = {"feeds": {x.key: encode_item(x) for x in items}, "currentTs": str(current_ts)}
    if not snapshot:
        d["type"] = "live_feed"
    ParseDict(d, msg)
    return msg.SerializeToString()
