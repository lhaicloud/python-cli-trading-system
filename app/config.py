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

    # Symbols pinned to the baseline (pre-relaxation) gating thresholds
    # regardless of MTF_MIN_SCORE/MIN_ZONE_SCORE/MIN_SIGNAL_CONFIDENCE tuning.
    # Comma-separated in .env, e.g. STRICT_SYMBOLS=DOGEUSDT,HYPEUSDT
    strict_symbols: str = Field("", env="STRICT_SYMBOLS")

    @property
    def strict_symbol_set(self) -> set[str]:
        return {s.strip().upper() for s in self.strict_symbols.split(",") if s.strip()}

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

    # Telegram notifications (paper)
    telegram_bot_token: str = ""
    telegram_chat_id: str   = ""

    # ── Binance live execution ─────────────────────────────────────────────────
    binance_api_key:    str  = Field("", env="BINANCE_API_KEY")
    binance_api_secret: str  = Field("", env="BINANCE_API_SECRET")
    # Set BINANCE_TESTNET=true to use testnet.binancefuture.com
    binance_testnet:    bool = Field(False, env="BINANCE_TESTNET")

    # ── Live trading parameters (fully independent from paper) ─────────────────
    live_risk_pct:           float = Field(1.0,  env="LIVE_RISK_PCT")
    live_max_portfolio:      int   = Field(3,    env="LIVE_MAX_PORTFOLIO")
    live_max_same_dir:       int   = Field(2,    env="LIVE_MAX_SAME_DIR")
    live_max_daily_loss_pct: float = Field(3.0,  env="LIVE_MAX_DAILY_LOSS_PCT")
    # Leverage: start at 1 (testnet). After verification set to 10.
    # dynamic_leverage() scales 1×–N× based on confidence, zone, R:R, regime.
    # 3+ consecutive losses always forces back to 1× regardless of this ceiling.
    live_max_leverage:       int   = Field(1,    env="LIVE_MAX_LEVERAGE")
    # Minimum R:R measured from the CURRENT mark price at execution time.
    # Signals are computed on candle close; by the time the executor runs,
    # price may have drifted past the planned entry, silently inverting the
    # trade's R:R. Entries below this floor are skipped.
    live_min_rr:             float = Field(1.2,  env="LIVE_MIN_RR")

    # ── Live Telegram channel (separate from paper channel) ────────────────────
    live_telegram_token:   str = Field("", env="LIVE_TELEGRAM_TOKEN")
    live_telegram_chat_id: str = Field("", env="LIVE_TELEGRAM_CHAT_ID")

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

    # Fraction of available balance a single live position's notional may use.
    # position_size() caps notional at capital × leverage with zero headroom, so
    # near-cap orders fail Binance margin checks (-2019: taker fee + mark-price
    # drift) — seen 4× in a month (HYPE/FET×2/TRX), delaying entries 60-90 min.
    live_margin_buffer: float = Field(0.95, env="LIVE_MARGIN_BUFFER")

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
    # Independent live-only switch (real resting order on the exchange, not the
    # simulated paper one above). Reuses entry_limit_expiry_candles /
    # entry_limit_min_gap_pct for its knobs.
    # 2026-07-15 production run: rested AT the zone price like the paper
    # version above, 0/6 fills — same "winners don't retrace" failure, worse
    # (paper at least fills at market as a fallback; live just expires and
    # skips the trade entirely). Reverted to off 2026-07-16.
    live_entry_limit_enabled:   bool  = Field(False, env="LIVE_ENTRY_LIMIT_ENABLED")
    # 2026-07-16 redesign: rest at whatever price yields this R:R (computed
    # from the signal's own stop/take-profit) instead of the full zone price.
    # Counterfactual on 104 historical paper trades: chasing the drifted
    # market price destroys ~87% of edge (93R zone-price -> 12R market-price),
    # but the R:R floor is correctly directional (floor-passing trades average
    # +0.18R at market vs +0.08R for floor-blocked) — most of the strategy's
    # edge sits in the trades a market order can't reach. This is the
    # untested middle ground: ask for less retrace than the full zone price
    # (better fill odds than the 0/6 above) while still landing comfortably
    # above live_min_rr once filled. Must stay > live_min_rr and materially
    # below the ~1.8-2.6 typical promised R:R (min_rr_ratio=1.8 is the floor
    # for a signal to be generated at all, so there's always room above 1.6).
    # Untuned — no historical fill-rate simulation has been run for this
    # specific price target yet.
    live_entry_limit_target_rr: float = Field(1.6, env="LIVE_ENTRY_LIMIT_TARGET_RR")

    # ── Paper drift gate (live/paper fill parity) ─────────────────────────────
    # Paper fills at the signal's candle-close entry_price; the live executor
    # instead re-measures R:R from the *current* price at execution time and
    # skips when drift has crushed it below live_min_rr (executor.py open_trade).
    # Without this, paper banks idealized fills live can never reach (e.g. strong
    # trends where price runs past the zone before entry), inflating the paper
    # record vs live. When enabled, PositionManager.submit() applies the SAME
    # revalidation so the paper track record forecasts what live would take.
    # Reuses live_min_rr as the floor. Only gates market entries — a resting
    # limit fills at its own price, so the pending-order path is unaffected.
    paper_drift_gate_enabled:   bool  = Field(True, env="PAPER_DRIFT_GATE_ENABLED")

    # ── SignalFilter A/B toggles ──────────────────────────────────────────────
    # Both filters were fit on very small samples (16 and ~8 trades, 2026-06-09).
    # A/B matrix 2026-07 (4 variants × 8 symbols, backtest variants filter_ab_*):
    # disabling BOTH gave +33% net PnL at equal max drawdown. Defaults stay True;
    # production disables via .env so the code default remains conservative.
    filter_distribution_enabled: bool = Field(True, env="FILTER_DISTRIBUTION_ENABLED")
    filter_hours_enabled:        bool = Field(True, env="FILTER_HOURS_ENABLED")

    # Symbols where the distribution filter applies even when globally disabled.
    # SOLUSDT was the one coin the A/B matrix showed the filter still helping
    # (PF collapsed to ~1.1 without it). Comma-separated, like STRICT_SYMBOLS.
    filter_distribution_symbols: str = Field("", env="FILTER_DISTRIBUTION_SYMBOLS")

    @property
    def filter_distribution_symbol_set(self) -> set[str]:
        return {s.strip().upper() for s in self.filter_distribution_symbols.split(",") if s.strip()}

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


# Baseline (pre-relaxation) gating thresholds -- always used for STRICT_SYMBOLS
# regardless of what MTF_MIN_SCORE/MIN_ZONE_SCORE/etc. are currently tuned to.
_STRICT_THRESHOLDS = dict(
    mtf_min_score=55.0,
    mtf_min_score_gap=15.0,
    min_zone_score=70.0,
    min_zone_score_no_model=70.0,
    min_signal_confidence=65.0,
)


def get_settings_for_symbol(symbol: str) -> Settings:
    """
    Same as get_settings(), but pins gating thresholds to the strict baseline
    for any symbol listed in STRICT_SYMBOLS -- lets the rest of the universe
    run relaxed thresholds while excluding specific underperforming symbols.
    """
    cfg = get_settings()
    if symbol.upper() in cfg.strict_symbol_set:
        return cfg.model_copy(update=_STRICT_THRESHOLDS)
    return cfg
