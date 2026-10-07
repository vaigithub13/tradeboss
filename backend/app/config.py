from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# .env lives in the project root (one level above backend/)
ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    frontend_origin: str = "http://localhost:5173"

    # Local data root; candles live in <data_dir>/candles/<SYMBOL>/<N>m.parquet
    data_dir: Path = ROOT_DIR / "data"

    # Session types shown by default in charts / backtests (comma-separated):
    # normal, weekend_full, special_short, muhurat
    include_session_types: str = "normal,weekend_full"

    @property
    def default_sessions(self) -> tuple[str, ...]:
        return tuple(t.strip() for t in self.include_session_types.split(",") if t.strip())

    # Safety switch: nothing in Phase 0 places orders. Stays false until Phase 7.
    live_trading: bool = False

    # Upstox Analytics Token (read-only, ~1 year). Only ever sent to api.upstox.com.
    # SecretStr: never shows up in repr / logs / API responses.
    upstox_analytics_token: SecretStr | None = None

    # Our own client-side caps (deliberately below Upstox's 50/s, 500/min, 2000/30min because
    # the account's budget is shared with other tools, e.g. My Trading Desk).
    upstox_rate_per_second: int = 20
    upstox_rate_per_minute: int = 300
    upstox_rate_per_half_hour: int = 1200

    # Startup fallback: download today's instrument file if the daily job has not (see launchd/)
    snapshot_on_startup: bool = True

    # Backend log: data/logs/backend-YYYY-MM-DD.log next to the terminal (tests turn it off in conftest)
    backend_log_file: bool = True

    # ---- Phase 2b: live feed (one connection, max) -------------------------------------------
    live_feed_enabled: bool = True
    # 09:15 bar volume baseline: "pre_open_inclusive" (default; 5 Oct was 260 under official)
    # | "first_tick" (pre-open volume excluded; 5 Oct was 9,555 under official)
    live_open_volume_baseline: str = "pre_open_inclusive"
    # connect window (IST) for the feed connection, any day (the feed's market_info decides the rest)
    live_connect_start: str = "08:55"
    live_connect_end: str = "16:10"
    # recorder window (IST), gated by market_info (see live/recorder.py)
    live_record_start: str = "09:00"
    live_record_end: str = "16:05"
    # daily reconcile time (IST) and its retry limit
    live_reconcile_at: str = "15:45"
    live_reconcile_until: str = "16:30"

    # Option book recorder. Stays off until it is turned on after the first live session.
    spread_recorder_enabled: bool = False

    @property
    def feed_recordings_dir(self) -> Path:
        return self.data_dir / "feed-recordings"

    @property
    def live_state_dir(self) -> Path:
        return self.data_dir / "live-state"

    @property
    def instruments_dir(self) -> Path:
        return self.data_dir / "instruments"

    @property
    def candles_dir(self) -> Path:
        return self.data_dir / "candles"

    def upstox_token_value(self) -> str | None:
        t = self.upstox_analytics_token
        value = t.get_secret_value().strip() if t is not None else ""
        return value or None


settings = Settings()
