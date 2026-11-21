"""Phase A: resolve research_universe_v1, backfill 1m candles, CA-adjust equities, quality report.

Research only — no live subscriptions, no UI, no paper. Safe to interrupt and re-run (sync_symbol
resumes via meta.json; CA/quality re-run over existing parquet).

    uv run python -m scripts.research_universe_backfill              # resolve + sync + CA + quality
    uv run python -m scripts.research_universe_backfill --resolve-only
    uv run python -m scripts.research_universe_backfill --skip-sync   # CA + quality on existing candles
    uv run python -m scripts.research_universe_backfill --quality-only
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from app.config import settings
from app.data.history import SyncProgress, sync_symbol
from app.research.corporate_actions import fetch_corporate_actions, write_adjusted_series
from app.research.quality import assess_symbol, render_quality_markdown
from app.research.universe import equity_isin, load_universe_spec, resolve_universe
from app.upstox.client import UpstoxError
from app.upstox.deps import make_client
from app.upstox.instruments import IST, current_index


def _printer(label: str):  # noqa: ANN202
    last = {"msg": ""}

    def on(p: SyncProgress) -> None:
        if p.message != last["msg"] and p.message.startswith(("fetching", "fetched", "done")):
            last["msg"] = p.message
            print(f"  [{label}] {p.windows_done}/{p.windows_total} {p.message}", flush=True)

    return on


def _write_resolution(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--resolve-only", action="store_true", help="resolve instruments and exit")
    ap.add_argument("--skip-sync", action="store_true", help="skip candle backfill (CA + quality only)")
    ap.add_argument("--quality-only", action="store_true", help="quality report only (no sync, no CA fetch)")
    ap.add_argument(
        "--report-dir",
        type=Path,
        default=None,
        help="where to write quality markdown (default: data/validation/)",
    )
    args = ap.parse_args(argv)

    today = datetime.now(IST).date()
    spec = load_universe_spec()
    history_from = date.fromisoformat(str(spec["history_from"]))
    index = current_index(settings.instruments_dir)
    if index is None:
        print("No instrument snapshot: run uv run python -m scripts.snapshot_instruments", file=sys.stderr)
        return 2

    resolution = resolve_universe(index, today=today, spec=spec)
    resolved_path = Path(__file__).resolve().parents[1] / "app" / "research" / "data" / "research_universe_v1_resolved.json"
    _write_resolution(resolved_path, resolution.to_dict())
    print(f"resolved {len(resolution.resolved)} · unresolved {resolution.unresolved}", flush=True)
    for note in resolution.notes:
        print(f"  note: {note}", flush=True)
    if resolution.unresolved:
        print("UNRESOLVED (not substituted):", ", ".join(resolution.unresolved), flush=True)

    if args.resolve_only:
        return 0

    client = None
    if not args.quality_only:
        client = make_client()
        if client is None:
            print("No UPSTOX_ANALYTICS_TOKEN in .env", file=sys.stderr)
            return 2

    if not args.skip_sync and not args.quality_only:
        assert client is not None
        for member in resolution.resolved:
            inst = index.get(member.instrument_key)
            if inst is None:
                print(f"skip {member.symbol}: key vanished from snapshot", flush=True)
                continue
            print(f"sync {member.symbol} ({member.role}) {member.instrument_key} from {history_from}", flush=True)
            try:
                res = sync_symbol(
                    client,
                    settings.candles_dir,
                    inst,
                    from_date=history_from,
                    on_progress=_printer(member.symbol),
                )
            except UpstoxError as e:
                print(f"  ERROR {member.symbol}: {e}", file=sys.stderr, flush=True)
                continue
            print(
                f"  -> {res.bars_in_file} bars in {res.symbol_dir}/1m.parquet "
                f"({res.windows_fetched} window(s))",
                flush=True,
            )

    actions_by_folder: dict[str, list] = {}
    if not args.quality_only:
        assert client is not None
        for member in resolution.resolved:
            if member.role != "equity":
                continue
            isin = equity_isin(member.instrument_key)
            if not isin:
                print(f"CA skip {member.symbol}: no ISIN in key", flush=True)
                continue
            symbol_dir = settings.candles_dir / member.folder
            if not (symbol_dir / "1m.parquet").is_file():
                print(f"CA skip {member.symbol}: no 1m.parquet yet", flush=True)
                continue
            print(f"CA {member.symbol} ISIN={isin}", flush=True)
            try:
                actions = fetch_corporate_actions(client, isin)
                ledger = write_adjusted_series(
                    symbol_dir,
                    actions,
                    as_of=today,
                    source=f"upstox:/v2/fundamentals/{isin}/corporate-actions",
                )
            except (UpstoxError, OSError, FileNotFoundError) as e:
                print(f"  ERROR CA {member.symbol}: {e}", file=sys.stderr, flush=True)
                continue
            actions_by_folder[member.folder] = actions
            print(
                f"  -> {ledger['rows_adj']} adj rows; "
                f"{len(ledger['applied_splits_bonuses'])} split/bonus applied",
                flush=True,
            )

    report_dir = args.report_dir or (settings.data_dir / "validation")
    report_dir.mkdir(parents=True, exist_ok=True)
    quality_rows = [
        assess_symbol(
            symbol=member.symbol,
            folder=member.folder,
            role=member.role,
            instrument_key=member.instrument_key,
            candles_dir=settings.candles_dir,
            history_from=history_from,
            history_to=today,
            actions=actions_by_folder.get(member.folder),
        )
        for member in resolution.resolved
    ]

    md = render_quality_markdown(
        as_of=str(spec["as_of"]),
        history_from=str(history_from),
        history_to=today.isoformat(),
        unresolved=resolution.unresolved,
        rows=quality_rows,
    )
    out = report_dir / f"research_universe_v1_quality_{spec['as_of']}.md"
    out.write_text(md, encoding="utf-8")
    summary = {
        "as_of": spec["as_of"],
        "history_from": str(history_from),
        "history_to": today.isoformat(),
        "unresolved": resolution.unresolved,
        "symbols": [r.to_dict() for r in quality_rows],
    }
    (report_dir / f"research_universe_v1_quality_{spec['as_of']}.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(md, flush=True)
    print(f"wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
