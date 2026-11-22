"""Pull 1-minute history for the scanner universe through the existing sync_symbol path.

Five-minute bars are resampled on read. This does not open a second websocket.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta

from app.config import settings
from app.data.history import sync_symbol
from app.scanner.config import load_settings
from app.scanner.universe import UniverseStock, equity_table, resolve_names, resolve_universe
from app.upstox.deps import make_client
from app.upstox.instruments import Instrument, current_index


def main() -> None:
    cfg = load_settings(settings.data_dir / "scanner" / "settings.json")
    index = current_index(settings.instruments_dir)
    if index is None:
        raise SystemExit("no instrument snapshot; run the morning snapshot first")
    names = [arg.strip().upper() for arg in sys.argv[1:] if arg.strip()]
    if names:
        resolved, unresolved = resolve_names([UniverseStock(name, "") for name in names], equity_table(index))
    else:
        resolved, unresolved = resolve_universe(index, cfg.universe)
    for symbol in unresolved:
        print(f"WARN unmapped {symbol}")
    client = make_client()
    start = date.today() - timedelta(days=90)
    by_key = {inst.key: inst for inst in index.instruments}
    for stock in resolved:
        inst = by_key.get(stock.instrument_key)
        if inst is None:
            print(f"WARN missing instrument {stock.symbol}")
            continue
        if not isinstance(inst, Instrument):
            continue
        result = sync_symbol(client, settings.candles_dir, inst, from_date=start)
        print(f"{stock.symbol} windows={result.windows_fetched} bars={result.bars_in_file}")


if __name__ == "__main__":
    main()
