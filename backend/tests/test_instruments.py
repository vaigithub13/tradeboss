from __future__ import annotations

import gzip
import json
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest

from app.upstox.instruments import (
    IST,
    NIFTY_INDEX_KEY,
    InstrumentIndex,
    SnapshotError,
    current_index,
    download_nse,
    latest_snapshot,
    list_snapshots,
    load_instruments,
    parse_master,
    snapshot_due,
    snapshot_path,
    take_snapshot,
)
from tests.upstox_helpers import make_master_bytes

MORNING = datetime(2026, 10, 3, 8, 30, tzinfo=IST)


def index_from_sample() -> InstrumentIndex:
    p = Path(__file__).parent / "_tmp_master.json.gz"
    p.write_bytes(make_master_bytes())
    try:
        return InstrumentIndex(load_instruments(p))
    finally:
        p.unlink()


# ------------------------------------------------------------------ download is public / token-free
def test_download_sends_no_authorization_header_and_uses_the_public_url() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, content=b"xyz")

    out = download_nse(httpx.Client(transport=httpx.MockTransport(handler)))
    assert out == b"xyz"
    assert seen[0].url.host == "assets.upstox.com"
    assert "authorization" not in {k.lower() for k in seen[0].headers}


# ------------------------------------------------------------------ validation
def test_parse_master_accepts_a_good_file() -> None:
    assert len(parse_master(make_master_bytes())) > 1000


@pytest.mark.parametrize(
    "raw",
    [b"not gzip", gzip.compress(b"not json"), gzip.compress(b"{}"), gzip.compress(json.dumps([{"a": 1}] * 5).encode())],
)
def test_parse_master_refuses_garbage_and_truncated_files(raw: bytes) -> None:
    with pytest.raises(SnapshotError):
        parse_master(raw)


def test_parse_master_requires_nifty_50_to_be_present() -> None:
    rows = [{"instrument_key": f"NSE_EQ|X{i}"} for i in range(1500)]
    with pytest.raises(SnapshotError, match="Nifty 50"):
        parse_master(gzip.compress(json.dumps(rows).encode()))


# ------------------------------------------------------------------ dated snapshots
def test_an_empty_instrument_download_is_not_saved(tmp_path: Path) -> None:
    with pytest.raises(SnapshotError):
        take_snapshot(tmp_path, now=MORNING, fetch=lambda: b"")
    assert list(tmp_path.rglob("*")) == []


def test_snapshot_is_stored_under_the_ist_date_and_never_overwritten_without_force(tmp_path: Path) -> None:
    calls = 0

    def fetch() -> bytes:
        nonlocal calls
        calls += 1
        return make_master_bytes()

    r1 = take_snapshot(tmp_path, now=MORNING, fetch=fetch)
    assert r1.created and r1.path == tmp_path / "2026-10-03" / "NSE.json.gz" and r1.path.is_file()
    r2 = take_snapshot(tmp_path, now=MORNING, fetch=fetch)
    assert not r2.created and calls == 1
    r3 = take_snapshot(tmp_path, now=MORNING, fetch=fetch, force=True)
    assert r3.created and calls == 2


def _master_bytes_with(extra_symbol: str | None = None, *, gzip_mtime: int = 0) -> bytes:
    """The sample master, gzipped with a chosen header timestamp (and optionally one more row)."""
    rows = json.loads(gzip.decompress(make_master_bytes()))
    if extra_symbol:
        rows.append({**rows[-1], "instrument_key": f"NSE_EQ|{extra_symbol}", "trading_symbol": extra_symbol})
    return gzip.compress(json.dumps(rows).encode(), mtime=gzip_mtime)


def test_an_identical_file_is_not_saved_again_but_leaves_a_note(tmp_path: Path) -> None:
    friday = datetime(2026, 10, 2, 9, 0, tzinfo=IST)
    saturday = datetime(2026, 10, 3, 9, 0, tzinfo=IST)
    first = take_snapshot(tmp_path, now=friday, fetch=_master_bytes_with)
    assert first.outcome == "saved"
    second = take_snapshot(tmp_path, now=saturday, fetch=_master_bytes_with)
    assert second.outcome == "unchanged" and not second.created
    assert second.same_as == date(2026, 10, 2) and second.path == first.path
    assert not (tmp_path / "2026-10-03" / "NSE.json.gz").exists()  # not saved
    assert (tmp_path / "2026-10-03" / "UNCHANGED").read_text().strip() == "2026-10-02"
    assert list_snapshots(tmp_path) == [date(2026, 10, 2)]  # only real files count


def test_identical_content_with_a_different_gzip_header_still_counts_as_unchanged(tmp_path: Path) -> None:
    take_snapshot(tmp_path, now=datetime(2026, 10, 2, 9, 0, tzinfo=IST), fetch=lambda: _master_bytes_with(gzip_mtime=1))
    other = _master_bytes_with(gzip_mtime=999)
    assert other != _master_bytes_with(gzip_mtime=1)  # the raw bytes do differ
    r = take_snapshot(tmp_path, now=datetime(2026, 10, 3, 9, 0, tzinfo=IST), fetch=lambda: other)
    assert r.outcome == "unchanged"


def test_a_changed_file_is_saved_and_compared_with_the_latest_saved_snapshot(tmp_path: Path) -> None:
    fri, sat, sun, mon = (datetime(2026, 10, d, 9, 0, tzinfo=IST) for d in (2, 3, 4, 5))
    take_snapshot(tmp_path, now=fri, fetch=_master_bytes_with)
    assert take_snapshot(tmp_path, now=sat, fetch=_master_bytes_with).outcome == "unchanged"
    assert take_snapshot(tmp_path, now=sun, fetch=_master_bytes_with).outcome == "unchanged"
    changed = take_snapshot(tmp_path, now=mon, fetch=lambda: _master_bytes_with("NEWCO"))
    assert changed.outcome == "saved" and changed.path.is_file()
    assert list_snapshots(tmp_path) == [date(2026, 10, 2), date(2026, 10, 5)]
    # the next identical file is compared with Monday's, not Friday's
    tue = datetime(2026, 10, 6, 9, 0, tzinfo=IST)
    r = take_snapshot(tmp_path, now=tue, fetch=lambda: _master_bytes_with("NEWCO"))
    assert r.outcome == "unchanged" and r.same_as == date(2026, 10, 5)


def test_an_unchanged_day_is_not_downloaded_again_and_the_startup_fallback_is_satisfied(tmp_path: Path) -> None:
    take_snapshot(tmp_path, now=datetime(2026, 10, 2, 9, 0, tzinfo=IST), fetch=_master_bytes_with)
    sat = datetime(2026, 10, 3, 9, 0, tzinfo=IST)
    take_snapshot(tmp_path, now=sat, fetch=_master_bytes_with)
    calls = 0

    def fetch() -> bytes:
        nonlocal calls
        calls += 1
        return _master_bytes_with()

    again = take_snapshot(tmp_path, now=sat, fetch=fetch)
    assert again.outcome == "exists" and calls == 0
    assert snapshot_due(tmp_path, datetime(2026, 10, 3, 20, 0, tzinfo=IST)) is False
    assert snapshot_due(tmp_path, datetime(2026, 10, 4, 7, 0, tzinfo=IST)) is True


def test_force_always_writes_the_file_even_if_identical(tmp_path: Path) -> None:
    take_snapshot(tmp_path, now=datetime(2026, 10, 2, 9, 0, tzinfo=IST), fetch=_master_bytes_with)
    sat = datetime(2026, 10, 3, 9, 0, tzinfo=IST)
    take_snapshot(tmp_path, now=sat, fetch=_master_bytes_with)
    r = take_snapshot(tmp_path, now=sat, fetch=_master_bytes_with, force=True)
    assert r.outcome == "saved" and (tmp_path / "2026-10-03" / "NSE.json.gz").is_file()
    assert not (tmp_path / "2026-10-03" / "UNCHANGED").exists()
    assert list_snapshots(tmp_path) == [date(2026, 10, 2), date(2026, 10, 3)]


def test_a_bad_download_leaves_no_note_either(tmp_path: Path) -> None:
    take_snapshot(tmp_path, now=datetime(2026, 10, 2, 9, 0, tzinfo=IST), fetch=_master_bytes_with)
    with pytest.raises(SnapshotError):
        take_snapshot(tmp_path, now=datetime(2026, 10, 3, 9, 0, tzinfo=IST), fetch=lambda: b"junk")
    assert not (tmp_path / "2026-10-03").exists()


def test_each_day_gets_its_own_folder_and_older_days_are_kept(tmp_path: Path) -> None:
    take_snapshot(tmp_path, now=datetime(2026, 10, 1, 9, 0, tzinfo=IST), fetch=make_master_bytes)
    take_snapshot(tmp_path, now=datetime(2026, 10, 3, 9, 0, tzinfo=IST), fetch=lambda: _master_bytes_with("NEWCO"))
    assert list_snapshots(tmp_path) == [date(2026, 10, 1), date(2026, 10, 3)]
    latest = latest_snapshot(tmp_path)
    assert latest is not None and latest[0] == date(2026, 10, 3)


def test_the_date_is_the_ist_date_not_the_utc_date(tmp_path: Path) -> None:
    # 2026-10-03 00:30 IST is still 2026-10-02 in UTC
    take_snapshot(tmp_path, now=datetime(2026, 10, 3, 0, 30, tzinfo=IST), fetch=make_master_bytes)
    assert (tmp_path / "2026-10-03").is_dir()


def test_a_bad_download_is_not_stored_and_leaves_no_partial_file(tmp_path: Path) -> None:
    with pytest.raises(SnapshotError):
        take_snapshot(tmp_path, now=MORNING, fetch=lambda: b"junk")
    assert list(tmp_path.rglob("*")) == []


def test_snapshot_due_rule_for_the_startup_fallback(tmp_path: Path) -> None:
    before_six_thirty = datetime(2026, 10, 3, 6, 29, tzinfo=IST)
    assert snapshot_due(tmp_path, before_six_thirty) is False  # file for today not published yet
    assert snapshot_due(tmp_path, datetime(2026, 10, 3, 6, 31, tzinfo=IST)) is True
    take_snapshot(tmp_path, now=MORNING, fetch=make_master_bytes)
    assert snapshot_due(tmp_path, datetime(2026, 10, 3, 20, 0, tzinfo=IST)) is False
    assert snapshot_due(tmp_path, datetime(2026, 10, 4, 7, 0, tzinfo=IST)) is True


# ------------------------------------------------------------------ what we offer
def test_only_indices_nse_stocks_and_nifty_futures_options_are_offered() -> None:
    idx = index_from_sample()
    kinds = {i.key: i.kind for i in idx.instruments}
    assert kinds["NSE_INDEX|Nifty 50"] == "index" and kinds["NSE_INDEX|India VIX"] == "index"
    assert kinds["NSE_EQ|INE002A01018"] == "equity"
    assert kinds["NSE_FO|48704"] == "future" and kinds["NSE_FO|40793"] == "option"
    assert "NSE_EQ|IN2920250163" not in kinds  # SG (gold bond) series: not a stock
    assert "NSE_FO|59396" not in kinds  # BANKNIFTY option: Nifty only
    assert not any(k.startswith("NSE_EQ|FILL") for k in kinds)


def test_derivative_fields_expiry_strike_lot_size() -> None:
    idx = index_from_sample()
    ce = idx.get("NSE_FO|40793")
    assert ce is not None
    assert (ce.expiry, ce.strike, ce.lot_size, ce.instrument_type) == (date(2026, 10, 6), 24000.0, 65, "CE")
    assert ce.has_oi and ce.underlying_key == NIFTY_INDEX_KEY
    fut = idx.get("NSE_FO|48704")
    assert fut is not None and fut.strike is None and fut.expiry == date(2026, 10, 27)
    eq = idx.get("NSE_EQ|INE002A01018")
    assert eq is not None and not eq.has_oi and eq.symbol == "RELIANCE"


# ------------------------------------------------------------------ search
def test_search_ranks_exact_symbol_first_and_matches_every_token() -> None:
    idx = index_from_sample()
    today = date(2026, 10, 3)
    top = idx.search("nifty", today=today)
    assert top[0].key == "NSE_INDEX|Nifty 50"  # symbol == NIFTY
    assert idx.search("reliance", today=today)[0].symbol == "RELIANCE"
    names = [i.symbol for i in idx.search("nifty 24000 ce", today=today)]
    assert names == ["NIFTY 24000 CE 06 OCT 26"]
    assert idx.search("zzzz", today=today) == []


def test_search_can_filter_by_kind_and_orders_derivatives_by_expiry() -> None:
    idx = index_from_sample()
    today = date(2026, 10, 3)
    futs = idx.search("nifty fut", kind="future", today=today)
    assert [f.expiry for f in futs] == [date(2026, 10, 27), date(2026, 11, 23), date(2026, 12, 29)]
    assert {i.kind for i in idx.search("", kind="index", today=today)} == {"index"}


def test_empty_query_lists_preferred_instruments_first_but_a_query_ignores_it() -> None:
    idx = index_from_sample()
    today = date(2026, 10, 3)
    stored = {"NSE_INDEX|Nifty 50", "NSE_EQ|INE002A01018"}
    first = idx.search("", today=today, limit=5, prefer=lambda i: i.key in stored)
    assert {i.key for i in first[:2]} == stored
    # a real query keeps its relevance ranking
    top = idx.search("reliance", today=today, prefer=lambda i: i.key == "NSE_INDEX|Nifty 50")
    assert top[0].symbol == "RELIANCE"


def test_expired_contracts_are_never_offered() -> None:
    idx = index_from_sample()
    gone = idx.search("nifty fut", kind="future", today=date(2026, 10, 28))
    assert [f.expiry for f in gone] == [date(2026, 11, 23), date(2026, 12, 29)]


def test_front_future_is_the_nearest_unexpired_contract() -> None:
    idx = index_from_sample()
    f = idx.front_future(date(2026, 10, 3))
    assert f is not None and f.key == "NSE_FO|48704"
    f2 = idx.front_future(date(2026, 10, 27))  # expiry day itself still counts
    assert f2 is not None and f2.key == "NSE_FO|48704"
    f3 = idx.front_future(date(2026, 10, 28))
    assert f3 is not None and f3.key == "NSE_FO|61471"
    assert idx.front_future(date(2027, 1, 1)) is None


def test_current_index_uses_the_newest_snapshot(tmp_path: Path) -> None:
    assert current_index(tmp_path) is None
    take_snapshot(tmp_path, now=datetime(2026, 10, 1, 9, 0, tzinfo=IST), fetch=make_master_bytes)
    take_snapshot(tmp_path, now=datetime(2026, 10, 3, 9, 0, tzinfo=IST), fetch=lambda: _master_bytes_with("NEWCO"))
    idx = current_index(tmp_path)
    assert idx is not None and idx.snapshot_day == date(2026, 10, 3)
    assert snapshot_path(tmp_path, date(2026, 10, 3)).is_file()
