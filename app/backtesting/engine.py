"""
Backtesting engine ??? candle-by-candle simulation, no lookahead bias.

Processes historical candles one at a time.  Signal generation runs
only on data visible up to (but NOT including) the current candle.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from rich.progress import Progress, SpinnerColumn, BarColumn, TaskProgressColumn, TextColumn

from app.config import get_settings
from app.data.repository import (
    get_candles,
    save_backtest_run,
    save_backtest_trades,
)
from app.ta.indicators import add_indicators
from app.ta.signals import generate_signal
from app.paper.signal_filter import SignalFilter
from app.backtesting.metrics import compute_metrics
from app.utils.logger import get_logger
from app.utils.math_utils import position_size, risk_reward, volatility_size_factor
from app.ta.leverage import dynamic_leverage
from app.utils.timeframes import dt_to_ms
from app.version import ENGINE_VERSION

logger = get_logger(__name__)

_TF_LOOKBACK = {
    "1d":  365,   # candles of lookback for daily
    "4h":  500,
    "1h":  500,
    "30m": 500,
    "12h": 200,   # 200 ?? 12H ??? 100 days lookback
    "1w":  104,   # 104 weeks = 2 years lookback
}


@dataclass
class OpenTrade:
    direction:        str
    entry_price:      float
    stop_loss:        float           # current (possibly trailed) stop
    take_profit:      float
    position_size:    float
    entry_time:       int
    original_sl:      float = 0.0    # original SL at entry (for reference)
    signal_confidence: float = 0.0
    zone_score:       float  = 0.0
    setup_type:       str    = ""
    mfe:              float  = 0.0
    mae:              float  = 0.0
    breakeven_set:    bool   = False  # kept for backward compat; ratchet supersedes it
    open_candle_idx:  int    = 0      # candle index when trade was opened
    ratchet_level:    int    = 0      # ratchet levels activated so far
    ratchet_levels:   list   = field(default_factory=list)
    features:         dict   = field(default_factory=dict)
    partial_taken:    bool   = False  # 50% closed at +partial_tp_r R
    partial_pnl:      float  = 0.0    # realized partial PnL (already in capital)


def run_backtest(
    symbol: str,
    start_ms: int,
    end_ms: int,
    initial_capital: float = 10_000.0,
    risk_pct: float = 1.0,
) -> dict[str, Any]:
    """
    Run a full backtest.

    Algorithm:
    1. Load all 30M candles for the backtest window.
    2. For each candle (from index 200 onward):
       a. Build DataFrames from data up to (not including) current candle.
       b. Generate signal.
       c. If signal is BUY/SELL, open a simulated trade.
       d. Update any open trades (check SL/TP).
    3. Collect all closed trades, compute metrics.
    """
    cfg = get_settings()
    fee_pct = cfg.backtest_fee_pct / 100
    slippage_pct = cfg.backtest_slippage_pct / 100

    # Load raw candles ??? all TFs needed for signal generation
    df_1d  = _load(symbol, "1d",  start_ms, end_ms)
    df_12h = _load(symbol, "12h", start_ms, end_ms)
    df_4h  = _load(symbol, "4h",  start_ms, end_ms)
    df_1h  = _load(symbol, "1h",  start_ms, end_ms)
    df_30m = _load(symbol, "30m", start_ms, end_ms)
    df_1w  = _load(symbol, "1w",  start_ms, end_ms)

    if df_30m.empty or len(df_30m) < 200:
        raise ValueError("Not enough 30M candle data to run backtest (need 200+ candles)")

    # Pre-compute indicators on the FULL datasets once.
    # EMA/ATR/RSI are causal (depend only on past data), so slicing a pre-computed
    # series gives the same values as computing from scratch up to that point.
    # This transforms the loop from O(n??) to O(n), dramatically reducing runtime.
    df_1d  = add_indicators(df_1d)  if not df_1d.empty  else df_1d
    df_12h = add_indicators(df_12h) if not df_12h.empty else df_12h
    df_4h  = add_indicators(df_4h)  if not df_4h.empty  else df_4h
    df_1h  = add_indicators(df_1h)  if not df_1h.empty  else df_1h
    df_30m = add_indicators(df_30m) if not df_30m.empty else df_30m
    df_1w  = add_indicators(df_1w)  if not df_1w.empty  else df_1w

    closed_trades: list[dict] = []
    open_trade: OpenTrade | None = None
    # Simulated resting limit order (entry refinement) ??? at most one at a time,
    # mirroring the one-open-trade rule.
    pending_order: dict | None = None
    capital = initial_capital
    daily_loss = 0.0
    daily_loss_date = ""
    # Max 96 candles open (48 hours on 30m TF) ??? stagnating trades exit at close
    MAX_TRADE_CANDLES = 96
    # Signal generation cadence (candles). Live evaluates every candle close;
    # default 2 halves runtime at once-per-hour resolution.
    SIGNAL_STEP = max(1, cfg.backtest_signal_step)
    # Cooldown candles after a stop-loss hit ??? 20 candles = 10 hours on 30m TF.
    SL_COOLDOWN = 20
    sl_cooldown_remaining = 0
    # Zone blacklist: maps price_level (rounded) -> expiry candle index.
    # Prevents re-entering a zone that just stopped us out for 48 candles (24h).
    ZONE_BLACKLIST_CANDLES = 48
    zone_blacklist: dict[int, int] = {}
    # Max trades per day to limit over-trading in choppy sessions.
    MAX_DAILY_TRADES = 3
    daily_trades = 0
    blocked_count = 0
    hold_count = 0
    filtered_count = 0
    drift_skipped_count = 0
    reentry_blocked_count = 0
    # direction -> [(candle_time, stop_loss)] of levels already entered, used
    # by the zone re-entry guard. In-memory: the sim has no DB to query.
    recent_levels: dict[str, list] = {"BUY": [], "SELL": []}
    # Same pre-trade quality gate the live watcher runs ??? without it the
    # backtest trades a different (looser) strategy than live.
    sig_filter = SignalFilter()

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
    ) as progress:
        task = progress.add_task(
            f"Backtesting {symbol} ({len(df_30m)} candles)",
            total=len(df_30m) - 200,
        )

        for i in range(200, len(df_30m)):
            candle = df_30m.iloc[i]
            candle_time = _to_ms(df_30m.index[i])
            candle_date = str(df_30m.index[i].date())

            # Reset daily trackers
            if candle_date != daily_loss_date:
                daily_loss = 0.0
                daily_trades = 0
                daily_loss_date = candle_date

            # Expire zone blacklist entries
            zone_blacklist = {k: v for k, v in zone_blacklist.items() if v > i}

            # ?????? Update open trade ?????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
            if open_trade is not None:
                high = float(candle["high"])
                low  = float(candle["low"])
                close = float(candle["close"])

                risk = abs(open_trade.entry_price - open_trade.original_sl) if open_trade.original_sl else abs(open_trade.entry_price - open_trade.stop_loss)

                # Update MFE / MAE
                if open_trade.direction == "BUY":
                    open_trade.mfe = max(open_trade.mfe, high - open_trade.entry_price)
                    open_trade.mae = max(open_trade.mae, open_trade.entry_price - low)
                else:
                    open_trade.mfe = max(open_trade.mfe, open_trade.entry_price - low)
                    open_trade.mae = max(open_trade.mae, high - open_trade.entry_price)

                # Ratchet stop (replaces one-shot breakeven trail).
                # Uses the PREVIOUS candle's extremes: trailing off the current
                # candle's high/low and then testing the new stop against that
                # same candle is intra-candle lookahead (the high that justifies
                # the trail may occur after the low that hits it).
                if cfg.mtf_ratchet_enabled and open_trade.ratchet_levels:
                    from app.ta.exit_engine import apply_ratchet
                    prev = df_30m.iloc[i - 1]
                    atr = float(prev.get("atr_14", 0)) or 1.0
                    new_sl, new_level = apply_ratchet(
                        direction=open_trade.direction,
                        original_risk=risk,
                        current_sl=open_trade.stop_loss,
                        candle_high=float(prev["high"]),
                        candle_low=float(prev["low"]),
                        atr=atr,
                        ratchet_level=open_trade.ratchet_level,
                        ratchet_levels=open_trade.ratchet_levels,
                        entry=open_trade.entry_price,
                    )
                    open_trade.stop_loss = new_sl
                    open_trade.ratchet_level = new_level

                if open_trade.direction == "BUY":
                    hit_sl = low <= open_trade.stop_loss
                    hit_tp = high >= open_trade.take_profit
                else:
                    hit_sl = high >= open_trade.stop_loss
                    hit_tp = low <= open_trade.take_profit

                # Partial take-profit at +partial_tp_r R. Skipped when the stop
                # or the full TP also sits inside this candle (conservative /
                # the full exit supersedes).
                if (
                    cfg.partial_tp_enabled
                    and not open_trade.partial_taken
                    and not hit_sl
                    and not hit_tp
                    and risk > 0
                ):
                    p_target = (
                        open_trade.entry_price + cfg.partial_tp_r * risk
                        if open_trade.direction == "BUY"
                        else open_trade.entry_price - cfg.partial_tp_r * risk
                    )
                    p_reached = (
                        high >= p_target
                        if open_trade.direction == "BUY"
                        else low <= p_target
                    )
                    if p_reached:
                        closed_size = open_trade.position_size * cfg.partial_tp_fraction
                        p_raw = (
                            (p_target - open_trade.entry_price) * closed_size
                            if open_trade.direction == "BUY"
                            else (open_trade.entry_price - p_target) * closed_size
                        )
                        p_pnl = p_raw - open_trade.entry_price * closed_size * fee_pct * 2
                        capital += p_pnl
                        daily_loss += min(0, p_pnl)
                        open_trade.position_size -= closed_size
                        open_trade.partial_taken = True
                        open_trade.partial_pnl = p_pnl

                # Max duration exit ??? close stagnating trade at current close price
                candles_open = i - open_trade.open_candle_idx
                if candles_open >= MAX_TRADE_CANDLES and not hit_tp and not hit_sl:
                    hit_sl = True  # treat as a forced exit
                    open_trade.stop_loss = close  # exit at close
                    exit_reason_override = "timeout"
                else:
                    exit_reason_override = None

                if hit_tp or hit_sl:
                    if hit_tp and hit_sl and not exit_reason_override:
                        # Both levels inside one candle ??? assume the level nearer
                        # the candle OPEN was struck first; ties go to the stop.
                        # (The old TP-priority rule was optimistic and inflated
                        # profit factors.)
                        open_px = float(candle["open"])
                        tp_first = (
                            abs(open_px - open_trade.take_profit)
                            < abs(open_px - open_trade.stop_loss)
                        )
                        hit_tp = tp_first

                    exit_price  = open_trade.take_profit if hit_tp and not exit_reason_override else open_trade.stop_loss
                    exit_reason = exit_reason_override if exit_reason_override else (
                        "tp_hit" if hit_tp else
                        ("ratchet_sl" if open_trade.ratchet_level > 0 else "sl_hit")
                    )

                    # Stop-type exits are market orders ??? apply adverse slippage.
                    # TP exits are resting limit orders (no slippage).
                    if exit_reason != "tp_hit":
                        if open_trade.direction == "BUY":
                            exit_price *= (1 - slippage_pct)
                        else:
                            exit_price *= (1 + slippage_pct)

                    if open_trade.direction == "BUY":
                        raw_pnl = (exit_price - open_trade.entry_price) * open_trade.position_size
                    else:
                        raw_pnl = (open_trade.entry_price - exit_price) * open_trade.position_size

                    # Fee on notional value (entry + exit), not on P&L
                    fee = open_trade.entry_price * open_trade.position_size * fee_pct * 2
                    pnl_increment = raw_pnl - fee
                    # Total trade PnL includes any partial TP already realized;
                    # capital only takes the increment (partial added earlier).
                    pnl = pnl_increment + open_trade.partial_pnl
                    pnl_pct = pnl / capital * 100

                    capital += pnl_increment
                    daily_loss += min(0, pnl_increment)

                    closed_trades.append({
                        "symbol":                  symbol,
                        "direction":               open_trade.direction,
                        "entry_price":             open_trade.entry_price,
                        "exit_price":              round(exit_price, 4),
                        "stop_loss":               open_trade.stop_loss,
                        "take_profit":             open_trade.take_profit,
                        "position_size":           open_trade.position_size,
                        "pnl":                     round(pnl, 2),
                        "pnl_pct":                 round(pnl_pct, 4),
                        "entry_time":              open_trade.entry_time,
                        "exit_time":               candle_time,
                        "exit_reason":             exit_reason,
                        "max_favorable_excursion": round(open_trade.mfe, 4),
                        "max_adverse_excursion":   round(open_trade.mae, 4),
                        "signal_confidence":       open_trade.signal_confidence,
                        "zone_score":              open_trade.zone_score,
                        "setup_type":              open_trade.setup_type,
                        "features":                open_trade.features,
                    })
                    # Blacklist the entry price zone on non-TP exits
                    if exit_reason != "tp_hit":
                        zone_key = round(open_trade.entry_price / 100) * 100
                        zone_blacklist[zone_key] = i + ZONE_BLACKLIST_CANDLES
                        sl_cooldown_remaining = SL_COOLDOWN
                    open_trade = None

            # ?????? Pending limit order: fill or expire ???????????????????????????????????????????????????????????????????????????
            # Fills are checked from the candle AFTER placement (the signal
            # fired at this candle's open; same-candle fills would be
            # optimistic). Limit fills execute at the limit price ??? no
            # market slippage.
            if pending_order is not None and open_trade is None:
                if i >= pending_order["expiry_idx"]:
                    pending_order = None
                elif i > pending_order["placed_idx"]:
                    limit = pending_order["limit_price"]
                    touched = (
                        float(candle["low"]) <= limit
                        if pending_order["direction"] == "BUY"
                        else float(candle["high"]) >= limit
                    )
                    if touched:
                        daily_trades += 1
                        from app.ta.exit_engine import build_ratchet_levels
                        ratchet_lvls = (
                            build_ratchet_levels(
                                limit, pending_order["stop_loss"],
                                pending_order["direction"],
                            )
                            if cfg.mtf_ratchet_enabled else []
                        )
                        open_trade = OpenTrade(
                            direction=pending_order["direction"],
                            entry_price=round(limit, 4),
                            stop_loss=pending_order["stop_loss"],
                            take_profit=pending_order["take_profit"],
                            position_size=pending_order["position_size"],
                            entry_time=candle_time,
                            original_sl=pending_order["stop_loss"],
                            open_candle_idx=i,
                            ratchet_level=0,
                            ratchet_levels=ratchet_lvls,
                            signal_confidence=pending_order["signal_confidence"],
                            zone_score=pending_order["zone_score"],
                            setup_type=pending_order["setup_type"],
                            features=pending_order["features"],
                        )
                        pending_order = None
                        progress.advance(task)
                        continue

            # ?????? SL cooldown gate ????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
            if sl_cooldown_remaining > 0:
                # A stop-out also cancels any resting order
                pending_order = None
                sl_cooldown_remaining -= 1
                progress.advance(task)
                continue

            # ?????? Max daily loss gate ???????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
            max_daily_loss = -capital * (cfg.default_max_daily_loss_pct / 100)
            if daily_loss <= max_daily_loss:
                progress.advance(task)
                continue

            # ?????? Max daily trades gate ?????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
            if daily_trades >= MAX_DAILY_TRADES:
                progress.advance(task)
                continue

            # ?????? Only one open trade / pending order at a time ?????????????????????????????????????????????
            if open_trade is not None or pending_order is not None:
                progress.advance(task)
                continue

            # ?????? Signal step ??? skip expensive signal generation on odd candles ???
            if i % SIGNAL_STEP != 0:
                progress.advance(task)
                continue

            # ?????? Build lookahead-safe DataFrames ???????????????????????????????????????????????????????????????????????????????????????
            # We use data only up to index i (exclusive of current candle)
            hist_30m = df_30m.iloc[:i]
            hist_1h  = _slice_to(df_1h,  candle_time)
            hist_4h  = _slice_to(df_4h,  candle_time)
            hist_12h = _slice_to(df_12h, candle_time)
            hist_1d  = _slice_to(df_1d,  candle_time)
            hist_1w  = _slice_to(df_1w,  candle_time)

            try:
                sig = generate_signal(
                    symbol=symbol,
                    df_1d=hist_1d,
                    df_4h=hist_4h,
                    df_1h=hist_1h,
                    df_30m=hist_30m,
                    capital=capital,
                    data_quality="ok",
                    df_12h=hist_12h,
                    df_1w=hist_1w,
                )
            except Exception as exc:
                logger.debug("Signal error at candle %d: %s", i, exc)
                progress.advance(task)
                continue

            if sig.signal == "HOLD":
                hold_count += 1
                progress.advance(task)
                continue
            if sig.signal == "BLOCKED":
                blocked_count += 1
                progress.advance(task)
                continue

            # Live-parity quality gate ??? the exact filter pipeline the live
            # watcher applies before opening a trade (zone rating, daily bias,
            # premium/discount, regime, time-of-day, candle rejection, ...).
            passed, _filter_reason = sig_filter.evaluate(
                sig, now_ms=candle_time, df_30m=hist_30m
            )
            if not passed:
                filtered_count += 1
                progress.advance(task)
                continue

            # ?????? Limit-or-market entry ?????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
            # Same rule as PositionManager.submit(): if price still needs to
            # retrace to the signal's entry level, rest a limit there instead
            # of chasing at market.
            cur_px = float(hist_30m["close"].iloc[-1])
            gap = cfg.entry_limit_min_gap_pct / 100
            needs_retrace = cfg.entry_limit_enabled and cur_px > 0 and (
                sig.entry_price < cur_px * (1 - gap)
                if sig.signal == "BUY"
                else sig.entry_price > cur_px * (1 + gap)
            )

            # Market entries pay slippage; limit fills execute at the limit
            entry = sig.entry_price
            if not needs_retrace:
                # Drift gate ??? parity with PositionManager._entry_drift_blocked
                # and LiveExecutor.open_trade. sig.entry_price is a candle-close
                # level; by fill time price has drifted to cur_px. Measure R:R
                # from cur_px and skip market entries where drift has crushed it
                # below live_min_rr, so backtests stop banking idealized fills
                # live can never reach (the paper/live gap driver).
                if cfg.paper_drift_gate_enabled and cur_px > 0:
                    sl_dist = abs(cur_px - sig.stop_loss)
                    wrong_side = (
                        cur_px <= sig.stop_loss if sig.signal == "BUY"
                        else cur_px >= sig.stop_loss
                    )
                    reward = (
                        sig.take_profit - cur_px if sig.signal == "BUY"
                        else cur_px - sig.take_profit
                    )
                    if sl_dist == 0 or wrong_side or reward / sl_dist < cfg.live_min_rr:
                        drift_skipped_count += 1
                        progress.advance(task)
                        continue
                # A market order fills at the market. sig.entry_price is a
                # zone *level*, and price has already drifted to cur_px by the
                # time this fires ??? filling at the level books trades that
                # never existed (82% of paper's entries to 2026-07-30 sat
                # outside the candle's traded range). The gate above decides
                # whether to enter; this decides at what price.
                if cfg.realistic_entry_fill and cur_px > 0:
                    entry = cur_px
                if sig.signal == "BUY":
                    entry *= (1 + slippage_pct)
                else:
                    entry *= (1 - slippage_pct)

            # ?????? Zone blacklist gate ???????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
            entry_zone_key = round(entry / 100) * 100
            if entry_zone_key in zone_blacklist:
                progress.advance(task)
                continue

            # Dynamic leverage + risk-based sizing
            lev      = dynamic_leverage(sig, recent_trades=closed_trades,
                                        max_leverage=cfg.max_leverage)
            # Volatility-scaled sizing (parity with live PositionManager): shrink
            # risk on high daily-ATR% coins instead of vetoing them.
            vol_factor = volatility_size_factor(
                sig.daily_atr_pct,
                full_atr_pct=cfg.vol_size_full_atr_pct,
                slope=cfg.vol_size_slope,
                floor=cfg.vol_size_floor,
            )
            pos_size = position_size(capital, risk_pct * vol_factor, entry, sig.stop_loss,
                                     leverage=lev)
            if pos_size <= 0 or sig.risk_reward < cfg.min_rr_ratio:
                progress.advance(task)
                continue

            # Zone re-entry guard ??? parity with PositionManager.can_open. One
            # supply/demand level re-firing after its trade closes produced 19
            # repeated (symbol, entry) groups carrying 58% of paper PnL.
            if (cfg.zone_reentry_guard_enabled and sig.stop_loss > 0
                    and cfg.zone_reentry_cooldown_hours > 0):
                cutoff = candle_time - int(cfg.zone_reentry_cooldown_hours * 3_600_000)
                tol = cfg.zone_reentry_level_tol_pct / 100
                recent_levels[sig.signal] = [
                    (t, s) for (t, s) in recent_levels[sig.signal] if t >= cutoff
                ]
                if any(abs(s - sig.stop_loss) / s <= tol
                       for (t, s) in recent_levels[sig.signal] if s > 0):
                    reentry_blocked_count += 1
                    progress.advance(task)
                    continue
                recent_levels[sig.signal].append((candle_time, sig.stop_loss))

            entry_features = {
                    "signal":           sig.signal,
                    "h4_bias":          sig.h4_bias,
                    "h1_confirmation":  sig.h1_confirmation,
                    "zone_score":       sig.zone_score,
                    "liquidity_sweep":  int(sig.liquidity_sweep),
                    "premium_discount": sig.premium_discount,
                    "risk_reward":      sig.risk_reward,
                    "market_regime":    sig.market_regime,
                    # Rule-engine-derived indicators
                    "rsi_14":           float(hist_30m["rsi_14"].iloc[-1]) if "rsi_14" in hist_30m.columns else 50.0,
                    "volume_ratio":     _vol_ratio(hist_30m),
                    "above_ema50":      int(float(hist_30m["above_ema50"].iloc[-1])) if "above_ema50" in hist_30m.columns else 0,
                    "above_ema200":     int(float(hist_30m["above_ema200"].iloc[-1])) if "above_ema200" in hist_30m.columns else 0,
                    "body_atr_ratio":   float(hist_30m["body_atr_ratio"].iloc[-1]) if "body_atr_ratio" in hist_30m.columns else 1.0,
                    "atr_expansion":    _atr_expansion(hist_30m),
                    "ema_alignment":    _ema_alignment(hist_30m),
                    "price_velocity":   _price_velocity(hist_30m),
                    # Raw independent features (now stored so models can learn from them)
                    "roc_10":           _roc_n(hist_30m, 10),
                    "roc_20":           _roc_n(hist_30m, 20),
                    "vol_trend":        _vol_trend_slope(hist_30m),
                    "htf_rsi_daily":    float(hist_1d["rsi_14"].iloc[-1]) if not hist_1d.empty and "rsi_14" in hist_1d.columns else 50.0,
            }

            if needs_retrace:
                pending_order = {
                    "direction":         sig.signal,
                    "limit_price":       round(sig.entry_price, 4),
                    "stop_loss":         sig.stop_loss,
                    "take_profit":       sig.take_profit,
                    "position_size":     round(pos_size, 6),
                    "signal_confidence": sig.confidence,
                    "zone_score":        sig.zone_score,
                    "setup_type":        sig.setup_type,
                    "features":          entry_features,
                    "placed_idx":        i,
                    "expiry_idx":        i + cfg.entry_limit_expiry_candles,
                }
                progress.advance(task)
                continue

            daily_trades += 1
            from app.ta.exit_engine import build_ratchet_levels
            ratchet_lvls = (
                build_ratchet_levels(entry, sig.stop_loss, sig.signal)
                if cfg.mtf_ratchet_enabled else []
            )
            open_trade = OpenTrade(
                direction=sig.signal,
                entry_price=round(entry, 4),
                stop_loss=sig.stop_loss,
                take_profit=sig.take_profit,
                position_size=round(pos_size, 6),
                entry_time=candle_time,
                original_sl=sig.stop_loss,
                open_candle_idx=i,
                ratchet_level=0,
                ratchet_levels=ratchet_lvls,
                signal_confidence=sig.confidence,
                zone_score=sig.zone_score,
                setup_type=sig.setup_type,
                features=entry_features,
            )
            progress.advance(task)

    # Close any open trade at last candle price
    if open_trade is not None:
        last_close = float(df_30m["close"].iloc[-1])
        last_time  = _to_ms(df_30m.index[-1])
        if open_trade.direction == "BUY":
            raw_pnl = (last_close - open_trade.entry_price) * open_trade.position_size
        else:
            raw_pnl = (open_trade.entry_price - last_close) * open_trade.position_size
        fee = open_trade.entry_price * open_trade.position_size * fee_pct * 2
        pnl = raw_pnl - fee + open_trade.partial_pnl
        closed_trades.append({
            "symbol":                  symbol,
            "direction":               open_trade.direction,
            "entry_price":             open_trade.entry_price,
            "exit_price":              round(last_close, 4),
            "stop_loss":               open_trade.stop_loss,
            "take_profit":             open_trade.take_profit,
            "position_size":           open_trade.position_size,
            "pnl":                     round(pnl, 2),
            "pnl_pct":                 round(pnl / capital * 100, 4),
            "entry_time":              open_trade.entry_time,
            "exit_time":               last_time,
            "exit_reason":             "end_of_data",
            "max_favorable_excursion": round(open_trade.mfe, 4),
            "max_adverse_excursion":   round(open_trade.mae, 4),
            "signal_confidence":       open_trade.signal_confidence,
            "zone_score":              open_trade.zone_score,
            "setup_type":              open_trade.setup_type,
            "features":                open_trade.features,
        })

    metrics = compute_metrics(closed_trades, initial_capital)
    metrics["blocked_count"] = blocked_count
    metrics["hold_count"] = hold_count
    metrics["filtered_count"] = filtered_count
    metrics["drift_skipped_count"] = drift_skipped_count
    metrics["reentry_blocked_count"] = reentry_blocked_count

    # Persist results
    run_id = save_backtest_run({
        "symbol":          symbol,
        "start_time":      start_ms,
        "end_time":        end_ms,
        "initial_capital": initial_capital,
        "final_capital":   metrics["final_capital"],
        "total_trades":    metrics["total_trades"],
        "win_rate":        metrics["win_rate"],
        "profit_factor":   metrics["profit_factor"],
        "max_drawdown":    metrics["max_drawdown_pct"],
        "net_profit":      metrics["net_profit"],
        "metrics":         metrics,
        # v2: conservative same-candle SL/TP, prev-candle ratchet, exit
        # slippage, live SignalFilter parity. Distinguishes runs from the
        # older optimistic engine in backtest_runs.
        "model_version":   ENGINE_VERSION,
    })

    for t in closed_trades:
        t["backtest_run_id"] = run_id
    save_backtest_trades(closed_trades)

    return {"run_id": run_id, "metrics": metrics, "trades": closed_trades}


# ?????? Helpers ?????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????

def _atr_expansion(df: pd.DataFrame, window: int = 50) -> float:
    """Current ATR / rolling mean ATR over `window` bars. Returns 1.0 if unavailable."""
    if df.empty or "atr_14" not in df.columns:
        return 1.0
    try:
        atr_series = df["atr_14"].dropna()
        if len(atr_series) < 5:
            return 1.0
        atr_now = float(atr_series.iloc[-1])
        atr_avg = float(atr_series.tail(window).mean())
        return round(atr_now / atr_avg, 4) if atr_avg > 0 else 1.0
    except Exception:
        return 1.0


def _ema_alignment(df: pd.DataFrame) -> float:
    """Number of EMAs in bullish order (0-3: ema9>ema21, ema21>ema50, ema50>ema200)."""
    if df.empty:
        return 1.5
    try:
        e9  = float(df["ema_9"].iloc[-1])
        e21 = float(df["ema_21"].iloc[-1])
        e50 = float(df["ema_50"].iloc[-1])
        e200= float(df["ema_200"].iloc[-1])
        return float(int(e9 > e21) + int(e21 > e50) + int(e50 > e200))
    except Exception:
        return 1.5


def _price_velocity(df: pd.DataFrame) -> float:
    """Normalized 3-bar price change: (close - close[3]) / atr_14. Clipped to [-5, 5]."""
    if df.empty or len(df) < 4 or "atr_14" not in df.columns:
        return 0.0
    try:
        c_now = float(df["close"].iloc[-1])
        c_3   = float(df["close"].iloc[-4])
        atr   = float(df["atr_14"].iloc[-1])
        if atr <= 0:
            return 0.0
        return float(max(-5.0, min(5.0, (c_now - c_3) / atr)))
    except Exception:
        return 0.0


def _vol_ratio(df: pd.DataFrame) -> float:
    """Current volume / 20-period volume MA. Returns 1.0 if unavailable."""
    if df.empty or "volume" not in df.columns or "vol_ma_20" not in df.columns:
        return 1.0
    try:
        vol = float(df["volume"].iloc[-1])
        vma = float(df["vol_ma_20"].iloc[-1])
        return round(vol / vma, 4) if vma > 0 else 1.0
    except Exception:
        return 1.0


def _roc_n(df: pd.DataFrame, period: int) -> float:
    """Rate of change over `period` bars, normalized by ATR. Clipped [-5, 5]."""
    if df.empty or len(df) < period + 1 or "atr_14" not in df.columns:
        return 0.0
    try:
        c_now = float(df["close"].iloc[-1])
        c_n   = float(df["close"].iloc[-(period + 1)])
        atr   = float(df["atr_14"].iloc[-1])
        return float(max(-5.0, min(5.0, (c_now - c_n) / atr))) if atr > 0 else 0.0
    except Exception:
        return 0.0


def _vol_trend_slope(df: pd.DataFrame, window: int = 10) -> float:
    """Normalized linear slope of volume over last `window` bars. Range [-1, 1]."""
    if df.empty or len(df) < window or "volume" not in df.columns:
        return 0.0
    try:
        import numpy as _np
        vols   = df["volume"].iloc[-window:].values.astype(float)
        avg_v  = float(_np.mean(vols))
        if avg_v <= 0:
            return 0.0
        slope = float(_np.polyfit(_np.arange(len(vols)), vols, 1)[0])
        return float(max(-1.0, min(1.0, slope / avg_v)))
    except Exception:
        return 0.0


def _load(symbol: str, tf: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    # Add extra lookback so indicators warm up properly
    lookback_ms = _TF_LOOKBACK.get(tf, 300) * _tf_ms(tf)
    return get_candles(symbol, tf, start_ms=start_ms - lookback_ms, end_ms=end_ms)


def _slice_to(df: pd.DataFrame, ts_ms: int) -> pd.DataFrame:
    """Return only rows with open_time < ts_ms."""
    if df.empty:
        return df
    ts = pd.Timestamp(ts_ms, unit="ms", tz="UTC")
    return df[df.index < ts]


def _to_ms(idx) -> int:
    return int(idx.timestamp() * 1000)


def _tf_ms(tf: str) -> int:
    from app.utils.timeframes import tf_to_ms
    return tf_to_ms(tf)

