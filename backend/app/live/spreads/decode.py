"""Best five book levels from a full-mode feed frame. Index frames have no book."""

from __future__ import annotations

from dataclasses import dataclass

from google.protobuf.json_format import ParseDict

from app.upstox.feed import MarketDataFeedV3_pb2 as pb


@dataclass(frozen=True)
class Level:
    bid_p: float
    bid_q: int
    ask_p: float
    ask_q: int


@dataclass(frozen=True)
class DepthQuote:
    key: str
    ts_ms: int
    levels: tuple[Level, ...]


def encode_depth(current_ts: int, books: dict[str, list[Level]]) -> bytes:
    """A live full-mode frame. Tests build fixture bytes with this; the feed is never opened."""
    feeds = {}
    for key, levels in books.items():
        quotes = [
            {"bidQ": str(lv.bid_q), "bidP": lv.bid_p, "askQ": str(lv.ask_q), "askP": lv.ask_p}
            for lv in levels
        ]
        feeds[key] = {
            "fullFeed": {"marketFF": {
                "ltpc": {"ltp": 0.0, "ltt": "0", "ltq": "0"},
                "marketLevel": {"bidAskQuote": quotes},
            }},
            "requestMode": "full_d5",
        }
    msg = pb.FeedResponse()
    ParseDict({"type": "live_feed", "currentTs": str(current_ts), "feeds": feeds}, msg)
    return msg.SerializeToString()


def depth_quotes(raw: bytes) -> list[DepthQuote]:
    """One quote per option in the frame. A book with no positive price is omitted.

    Level 1 (index 0) is the best bid and ask. Levels 2-5 stay on the quote so a
    fill can walk them; they are not the best price.
    """
    msg = pb.FeedResponse()
    try:
        msg.ParseFromString(raw)
    except Exception:
        return []
    ts = int(msg.currentTs)
    out: list[DepthQuote] = []
    for key, feed in msg.feeds.items():
        if feed.WhichOneof("FeedUnion") != "fullFeed":
            continue
        if feed.fullFeed.WhichOneof("FullFeedUnion") != "marketFF":
            continue
        levels = tuple(
            Level(float(q.bidP), int(q.bidQ), float(q.askP), int(q.askQ))
            for q in feed.fullFeed.marketFF.marketLevel.bidAskQuote
        )
        if not any(lv.bid_p > 0 or lv.ask_p > 0 for lv in levels):
            continue
        out.append(DepthQuote(key, ts, levels))
    return out
