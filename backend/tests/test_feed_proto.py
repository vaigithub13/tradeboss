"""The committed protobuf module decodes REAL frames captured by scripts.feed_probe (no network)."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import pytest

from app.upstox.feed import MarketDataFeedV3_pb2 as pb

FIXTURE = Path(__file__).parent / "fixtures" / "upstox" / "feed_probe_2026-10-03.json"
NIFTY, VIX, FUT, REL = "NSE_INDEX|Nifty 50", "NSE_INDEX|India VIX", "NSE_FO|48704", "NSE_EQ|INE002A01018"


@pytest.fixture(scope="module")
def frames() -> list[pb.FeedResponse]:
    doc = json.loads(FIXTURE.read_text())
    out = []
    for m in doc["messages"]:
        if m["dir"] == "recv":
            fr = pb.FeedResponse()
            fr.ParseFromString(base64.b64decode(m["b64"]))
            out.append(fr)
    return out


def test_fixture_holds_no_token_or_authorized_url() -> None:
    text = FIXTURE.read_text()
    assert "wss://" not in text and "code=" not in text and "Bearer" not in text
    assert not re.search(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.", text)  # JWT shape


def test_first_frame_is_market_info_with_segment_statuses(frames: list[pb.FeedResponse]) -> None:
    mi = frames[0]
    assert mi.type == pb.market_info and mi.currentTs > 0 and len(mi.feeds) == 0
    status = {k: pb.MarketStatus.Name(v) for k, v in mi.marketInfo.segmentStatus.items()}
    assert status["NSE_EQ"] == "CLOSING_END" and status["NSE_INDEX"] == "CLOSING_END"
    assert status["NSE_FO"] == "NORMAL_CLOSE"


def test_second_frame_is_the_snapshot_for_all_four_keys(frames: list[pb.FeedResponse]) -> None:
    snap = frames[1]
    assert snap.type == pb.initial_feed  # proto3 default: absent on the wire
    assert set(snap.feeds) == {NIFTY, VIX, FUT, REL}
    assert all(f.requestMode == pb.full_d5 for f in snap.feeds.values())  # "full" == full_d5


def test_indices_use_indexFF_without_volume_and_stocks_futures_use_marketFF(frames: list[pb.FeedResponse]) -> None:
    feeds = frames[1].feeds
    for key in (NIFTY, VIX):
        ff = feeds[key].fullFeed
        assert ff.WhichOneof("FullFeedUnion") == "indexFF"
        assert [o.interval for o in ff.indexFF.marketOHLC.ohlc] == ["1d", "I1"]
        assert all(o.vol == 0 for o in ff.indexFF.marketOHLC.ohlc)
    for key in (FUT, REL):
        ff = feeds[key].fullFeed
        assert ff.WhichOneof("FullFeedUnion") == "marketFF"
        assert ff.marketFF.vtt > 0 and len(ff.marketFF.marketLevel.bidAskQuote) == 5
    assert feeds[FUT].fullFeed.marketFF.oi > 0 and feeds[REL].fullFeed.marketFF.oi == 0


def test_timestamps_are_epoch_milliseconds(frames: list[pb.FeedResponse]) -> None:
    rel = frames[1].feeds[REL].fullFeed.marketFF
    i1 = next(o for o in rel.marketOHLC.ohlc if o.interval == "I1")
    assert i1.ts % 60_000 == 0  # a minute boundary
    assert rel.ltpc.ltt > i1.ts and 1_700_000_000_000 < rel.ltpc.ltt < 2_000_000_000_000
