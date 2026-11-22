"""Recompute the stock × setup ranking. Saturday launchd runs this; it does not start a second backend."""

from __future__ import annotations

from app.scanner.service import ScannerService
from app.config import settings
from app.routes.candles import get_store


def main() -> None:
    service = ScannerService(settings.data_dir / "scanner", store=get_store(), instruments_dir=settings.instruments_dir)
    payload = service.run_rank()
    print(f"ranked {len(payload['pairs'])} stock×setup pairs as of {payload['as_of']}")


if __name__ == "__main__":
    main()
