"""Central configuration using pydantic-settings."""

from __future__ import annotations

from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        protected_namespaces=("settings_",),
    )

    # Paths
    db_path: Path = Field(default=Path("data/db/lqmtf.db"))
    model_dir: Path = Field(default=Path("data/models"))
    log_file: Path = Field(default=Path("data/lqmtf.log"))

    # Binance — USDT-margined futures (fapi)
    # Spot:    https://api.binance.com   — /api/v3/klines
    # Futures: https://fapi.binance.com  — /fapi/v1/klines
    binance_base_url: str = "https://fapi.binance.com"

    # Optional proxy for Binance traffic. Binance geo-blocks US IPs (HTTP 451),
    # so a US-hosted VM must route requests through a non-US proxy.
    # Supports http://, https://, or socks5:// URLs, with optional credentials:
    #   http://user:pass@host:port  |  socks5://host:port
    # Empty = direct connection (no proxy).
    binance_proxy_url: str = Field("", env="BINANCE_PROXY_URL")

    # Logging
    log_level: str = "INFO"

    # Defaults
    default_symbol: str = "BTCUSDT"
    default_capital: float = 10_000.0
    default_risk_pct: float = 1.0
    default_max_daily_loss_pct: float = 3.0
    # Futures leverage: max multiplier for dynamic_leverage()
    # Default 1 = spot (no leverage). Set LEVERAGE=5 in .env to enable futures mode.
    max_leverage: int = Field(1, env="LEVERAGE")
    min_rr_ratio: float = 1.8
    min_zone_score: float = 70.0
    min_zone_score_no_model: float = 70.0
    min_signal_confidence: float = 65.0

    # Backtest — futures fees (taker 0.05% vs spot 0.10%)
    backtest_fee_pct: float = 0.05
    backtest_slippage_pct: float = 0.05
    # Generate signals every N candles in backtests. Live evaluates every
    # candle; set 1 for full parity (~2× runtime).
    backtest_signal_step: int = Field(2, env="BACKTEST_SIGNAL_STEP")

    # Backtest validation thresholds — a run must pass ALL THREE to count as
    # "profitable" for the purposes of the validated watchlist.
    # net_profit > 0 alone is not enough (e.g. $0.90 on $10k = noise).
    backtest_min_win_rate: float = Field(0.45, env="BACKTEST_MIN_WIN_RATE")   # 45%
    backtest_min_trades:   int   = Field(5,    env="BACKTEST_MIN_TRADES")     # at least 5 trades
    backtest_min_profit:   float = Field(0.0,  env="BACKTEST_MIN_PROFIT")     # > 0

    # Telegram notifications
    telegram_bot_token: str = ""
    telegram_chat_id: str   = ""

    # Supported timeframes in ascending order
    supported_timeframes: list[str] = ["1m", "5m", "15m", "30m", "1h", "4h", "12h", "1d", "1w"]

    # Binance kline limit per request
    binance_kline_limit: int = 1000

    # ── MTF strategy settings ─────────────────────────────────────────────────
    # Timeframe weights (must sum to 1.0)
    # 1W is scored in the composite (not a hard gate) so weekly bias is a
    # score contribution rather than an outright block.
    mtf_tf_weight_1w:  float = Field(0.15, env="MTF_TF_WEIGHT_1W")
    mtf_tf_weight_1d:  float = Field(0.20, env="MTF_TF_WEIGHT_1D")
    mtf_tf_weight_12h: float = Field(0.15, env="MTF_TF_WEIGHT_12H")
    mtf_tf_weight_4h:  float = Field(0.22, env="MTF_TF_WEIGHT_4H")
    mtf_tf_weight_1h:  float = Field(0.18, env="MTF_TF_WEIGHT_1H")
    mtf_tf_weight_30m: float = Field(0.10, env="MTF_TF_WEIGHT_30M")

    # Score thresholds
    mtf_min_score:     float = Field(55.0, env="MTF_MIN_SCORE")
    mtf_min_score_gap: float = Field(15.0, env="MTF_MIN_SCORE_GAP")

    # Entry filters
    mtf_rsi_overbought:   float = Field(70.0, env="MTF_RSI_OVERBOUGHT")
    mtf_rsi_oversold:     float = Field(30.0, env="MTF_RSI_OVERSOLD")
    mtf_ema200_strict:    bool  = Field(True,  env="MTF_EMA200_STRICT")
    mtf_min_volume_ratio: float = Field(0.8,   env="MTF_MIN_VOLUME_RATIO")

    # Ratchet stop
    mtf_ratchet_enabled: bool = Field(True, env="MTF_RATCHET_ENABLED")

    # ── Portfolio risk budget ─────────────────────────────────────────────────
    # Total capital_at_risk across ALL open positions may not exceed this % of
    # portfolio equity. With 1% risk/trade and max_portfolio=5 allows 5 concurrent.
    max_open_risk_pct: float = Field(5.0, env="MAX_OPEN_RISK_PCT")
    # Hard cap on concurrent open trades (should match watchlist size COINS_PER_DAY).
    max_portfolio: int = Field(5, env="MAX_PORTFOLIO")
    # Max trades allowed in the same direction (set equal to max_portfolio to disable).
    max_same_dir: int = Field(5, env="MAX_SAME_DIR")
    # Risk scaling for additional same-direction positions (crypto is one big
    # BTC trade): 1st position 100% risk, 2nd 70%, 3rd+ 50%.
    corr_risk_scale: list[float] = [1.0, 0.7, 0.5]

    # ── Live trade management (parity with backtest engine) ──────────────────
    # Force-close stagnating trades after this many hours (96 × 30m candles).
    max_trade_age_hours: float = Field(48.0, env="MAX_TRADE_AGE_HOURS")

    # ── Partial take-profit ───────────────────────────────────────────────────
    partial_tp_enabled:  bool  = Field(True, env="PARTIAL_TP_ENABLED")
    partial_tp_r:        float = Field(1.5,  env="PARTIAL_TP_R")        # trigger at +1.5R
    partial_tp_fraction: float = Field(0.5,  env="PARTIAL_TP_FRACTION") # close 50%

    # ── Entry limit orders ────────────────────────────────────────────────────
    # Instead of market-entering when price is anywhere near the zone, place a
    # simulated limit at the signal's entry price and wait for the retrace.
    # DEFAULT OFF: backtest (BTC Q1-2025: 1 trade/+$133 vs 6 trades/+$895) and
    # live MAE data (winners never retrace) both show waiting for a retrace
    # skips the best trades. Enable with ENTRY_LIMIT_ENABLED=1 to experiment.
    entry_limit_enabled:        bool  = Field(False, env="ENTRY_LIMIT_ENABLED")
    entry_limit_expiry_candles: int   = Field(4,    env="ENTRY_LIMIT_EXPIRY_CANDLES")  # × 30m = 2h
    # If |entry - current| is below this %, fill at market immediately.
    entry_limit_min_gap_pct:    float = Field(0.05, env="ENTRY_LIMIT_MIN_GAP_PCT")

    # ── Funding-rate filter (futures sentiment) ───────────────────────────────
    # Block SELLs when funding is already strongly negative (crowded short,
    # squeeze risk) and BUYs when strongly positive. 0.0005 = 0.05% per 8h.
    funding_filter_enabled: bool  = Field(True,   env="FUNDING_FILTER_ENABLED")
    funding_rate_limit:     float = Field(0.0005, env="FUNDING_RATE_LIMIT")

    # ── Volatility-scaled position sizing ─────────────────────────────────────
    # High DAILY ATR%-of-price trades keep positive expectancy but lower win-rate
    # and a fat loss tail — and fare far worse live than in backtest (stop
    # slippage, noise-swept stops). So size them DOWN rather than skip them
    # (universe study: 1k v2 + 11k v1 trades). Curve via volatility_size_factor:
    #   factor = clamp(1 - slope*(atr% - full), floor, 1.0)
    #   atr% ≤ 8 → full size, 13 → 0.6×, 18 → floor 0.3×.
    vol_size_full_atr_pct: float = Field(8.0,  env="VOL_SIZE_FULL_ATR_PCT")
    vol_size_slope:        float = Field(0.08, env="VOL_SIZE_SLOPE")
    vol_size_floor:        float = Field(0.3,  env="VOL_SIZE_FLOOR")
    # Selection-time demotion only: rank coins whose daily atr% exceeds this lower
    # in the rotation (scan_universe). Not an entry gate.
    max_daily_atr_pct: float = Field(13.0, env="MAX_DAILY_ATR_PCT")

    # ── Liquidity quality gate (coin selection) ───────────────────────────────
    # Exclude / demote coins whose avg daily quote-volume (USDT) is below this
    # floor. RIF traded ~$26M/day; every winner ≥ $38M/day. $30M splits them.
    min_daily_quote_volume_musd: float = Field(30.0, env="MIN_DAILY_QUOTE_VOLUME_MUSD")

    # ── Per-symbol risk memory (backstop) ─────────────────────────────────────
    # Bench a symbol after this many stops over a rolling window — generalises
    # the intraday cooldowns so spaced re-entries (RIF: 29h then 6d apart) can't
    # slip through. 0 disables.
    symbol_bench_window_days: float = Field(7.0, env="SYMBOL_BENCH_WINDOW_DAYS")
    symbol_bench_max_stops:   int   = Field(3,   env="SYMBOL_BENCH_MAX_STOPS")

    # ── Macro event guard ─────────────────────────────────────────────────────
    # Block new entries within ± this many hours of scheduled macro events
    # (FOMC, CPI, ...) listed in data/macro_events.json.
    macro_guard_enabled: bool  = Field(True, env="MACRO_GUARD_ENABLED")
    macro_guard_hours:   float = Field(2.0,  env="MACRO_GUARD_HOURS")
    macro_events_file:   Path  = Field(default=Path("data/macro_events.json"))

    def ensure_dirs(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
        _settings.ensure_dirs()
    return _settings
