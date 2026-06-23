"""
Universe scanner — ranks all symbols in the DB by current signal opportunity.

Can be used as a standalone CLI tool or imported by the watcher for auto-selection.

CLI usage:
    python scan_universe.py
    python scan_universe.py --top 6
    python scan_universe.py --direction SELL
    python scan_universe.py --min-gap 40
    python scan_universe.py --validated-only     # only symbols with backtest history

Importable API:
    from scan_universe import scan_and_rank, select_watchlist, auto_backtest_new_symbols
    ranked  = scan_and_rank()                           # all symbols
    symbols = select_watchlist(n=0, validated_only=True) # all validated
    new     = auto_backtest_new_symbols()               # backtest unvalidated coins
"""
from __future__ import annotations

import warnings; warnings.filterwarnings('ignore')
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from app.backtesting.engine import ENGINE_VERSION
from app.config import get_settings
from app.data.repository import get_candles, get_active_model
from app.db.connection import get_conn
from app.ta.indicators import add_indicators
from app.ta.mtf_scorer import run_prefilter
from app.ta.regime import classify_regime
from app.ta.zones import detect_zones

cfg = get_settings()

# Aligned with the live SignalFilter: distribution entries are BLOCKED there,
# so a coin in distribution must not look attractive to the rotation.
REGIME_RANK = {
    "bullish_trend":              5,
    "bearish_trend":              5,
    "accumulation":               4,
    "distribution":               0,
    "trap_zone":                  3,
    "squeeze":                    1,
    "high_volatility_liquidation":1,
    "choppy":                     0,
}

# A symbol is "validated" when it has at least this many profitable runs
# from the CURRENT engine version (old optimistic-engine runs don't count).
MIN_PROFITABLE_RUNS = 1

MIN_30M_CANDLES = 5_000   # ~3 months of 30m bars


# ── Core scan function (importable) ──────────────────────────────────────────

def scan_and_rank(
    validated_only: bool = False,
    verbose: bool = False,
    pool: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Scan all DB symbols and return a ranked list by conviction score.

    Parameters
    ----------
    validated_only : If True, only include symbols with >= MIN_PROFITABLE_RUNS
                     profitable backtest runs from the current engine version.
    verbose        : Print per-symbol status as scanning progresses.
    pool           : Optional whitelist — only scan these symbols (e.g. the
                     curated rotation pool from coin_pool.json).

    Returns
    -------
    List of dicts sorted by conv_score descending. Each dict contains:
        symbol, decision, direction, long_score, short_score, gap,
        regime, ml, zone_near, zone_dist, price, conv_score,
        backtest_runs, profitable_runs
    """
    # All symbols with enough 30m data
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT symbol, COUNT(*) as n
            FROM candles WHERE timeframe='30m'
            GROUP BY symbol HAVING n >= ?
            ORDER BY symbol
        """, (MIN_30M_CANDLES,)).fetchall()

        # Backtest run history per symbol — CURRENT engine version only.
        # Runs from the old optimistic engine (rule_based_v1) had inflated
        # profit factors and don't count as validation.
        # A run counts as "profitable" only when it passes all three thresholds:
        # net_profit > min_profit, win_rate >= min_win_rate, total_trades >= min_trades.
        _c = cfg
        bt_stats = {
            r[0]: {"runs": r[1], "profitable": r[2]}
            for r in conn.execute("""
                SELECT symbol, COUNT(*) as runs,
                       SUM(CASE WHEN net_profit > ?
                                 AND win_rate   >= ?
                                 AND total_trades >= ?
                            THEN 1 ELSE 0 END) as profitable
                FROM backtest_runs
                WHERE model_version = ?
                GROUP BY symbol
            """, (_c.backtest_min_profit, _c.backtest_min_win_rate,
                  _c.backtest_min_trades, ENGINE_VERSION)).fetchall()
        }

    symbols = [r[0] for r in rows]
    if pool:
        pool_set = {s.upper() for s in pool}
        symbols = [s for s in symbols if s in pool_set]
    if validated_only:
        symbols = [
            s for s in symbols
            if bt_stats.get(s, {}).get("profitable", 0) >= MIN_PROFITABLE_RUNS
        ]

    results  = []

    for sym in symbols:
        try:
            df_30m = get_candles(sym, "30m", limit=500)
            df_1h  = get_candles(sym, "1h",  limit=500)
            df_4h  = get_candles(sym, "4h",  limit=500)
            df_1d  = get_candles(sym, "1d",  limit=300)
            df_12h = get_candles(sym, "12h", limit=200)
            df_1w  = get_candles(sym, "1w",  limit=104)

            if len(df_30m) < 50 or len(df_1d) < 50 or len(df_1w) < 52:
                if verbose: print(f"  {sym:<12} SKIP (insufficient data)")
                continue

            df_30m = add_indicators(df_30m)
            df_1h  = add_indicators(df_1h)
            df_4h  = add_indicators(df_4h)
            df_1d  = add_indicators(df_1d)
            df_12h = add_indicators(df_12h) if not df_12h.empty else df_12h
            df_1w  = add_indicators(df_1w)

            # ── Liquidity + volatility quality gate (prevention at source) ──
            # Keep RIF-like names off the watchlist: thin coins get noise-swept
            # stops, wild coins can't be governed by a zone stop. Calibrated on
            # post-deploy trades (every winner ≥ $38M/day & daily atr% ≤ 11.1).
            avg_qv_musd = 0.0
            if "quote_volume" in df_1d.columns:
                qv = df_1d["quote_volume"].dropna()
                if len(qv) > 0:
                    avg_qv_musd = float(qv.iloc[-20:].mean()) / 1e6
            daily_atr_pct = float(df_1d["atr_pct"].iloc[-1]) if "atr_pct" in df_1d.columns else 0.0

            if 0 < avg_qv_musd < cfg.min_daily_quote_volume_musd:
                if verbose:
                    print(f"  {sym:<12} SKIP (illiquid ${avg_qv_musd:,.0f}M/d "
                          f"< ${cfg.min_daily_quote_volume_musd:,.0f}M)")
                continue

            pf          = run_prefilter(df_1w, df_1d, df_12h, df_4h, df_1h, df_30m, cfg)
            regime_info = classify_regime(df_4h)
            regime      = regime_info["regime"]

            has_buy  = get_active_model(sym, "buy")  is not None
            has_sell = get_active_model(sym, "sell") is not None
            ml_tag   = ("buy+sell" if (has_buy and has_sell)
                        else ("buy" if has_buy else ("sell" if has_sell else "—")))

            current_price = float(df_30m["close"].iloc[-1])
            # Zones are computed on the fly (same calls as generate_signal) —
            # the `zones` DB table is never populated, so reading it back
            # made this score component permanently zero.
            all_zones     = (detect_zones(df_30m, lookback=300, timeframe="30m")
                             + detect_zones(df_4h,  lookback=200, timeframe="4h"))

            zone_near       = False
            nearest_zone_pct = None
            for z in all_zones:
                mid  = (z["zone_top"] + z["zone_bottom"]) / 2
                dist = abs(current_price - mid) / current_price * 100
                if nearest_zone_pct is None or dist < nearest_zone_pct:
                    nearest_zone_pct = dist
                if dist <= 3.0:
                    zone_near = True

            gap        = abs(pf.long_score - pf.short_score)
            regime_pts = REGIME_RANK.get(regime, 0)
            zone_bonus = 15 if zone_near else 0
            # Bonus for having ML models (more reliable signal confidence)
            ml_bonus   = 5 if (has_buy and has_sell) else (2 if (has_buy or has_sell) else 0)
            # Demote (don't hard-exclude) coins whose daily volatility exceeds the
            # entry gate — they're only tradeable on calmer days, so rank them last.
            vol_penalty = max(0.0, daily_atr_pct - cfg.max_daily_atr_pct) * 2.0
            conv_score = gap * 0.60 + regime_pts * 5 * 0.25 + zone_bonus * 0.15 + ml_bonus - vol_penalty

            bt_info = bt_stats.get(sym, {})

            result = {
                "symbol":          sym,
                "decision":        pf.decision,
                "direction":       pf.direction or "—",
                "long_score":      pf.long_score,
                "short_score":     pf.short_score,
                "gap":             gap,
                "regime":          regime,
                "regime_rank":     regime_pts,
                "ml":              ml_tag,
                "has_model":       has_buy or has_sell,
                "zone_near":       zone_near,
                "zone_dist":       f"{nearest_zone_pct:.1f}%" if nearest_zone_pct else "—",
                "price":           current_price,
                "liq_musd":        round(avg_qv_musd, 1),
                "daily_atr_pct":   round(daily_atr_pct, 1),
                "conv_score":      conv_score,
                "backtest_runs":   bt_info.get("runs", 0),
                "profitable_runs": bt_info.get("profitable", 0),
            }
            results.append(result)

            if verbose:
                status = (f"{pf.decision:<8} gap={gap:5.1f}  regime={regime:<22}"
                          f"  ml={ml_tag:<10}  bt={bt_info.get('runs',0)}runs"
                          f"  {'[zone]' if zone_near else ''}")
                print(f"  {sym:<12} {status}")

        except Exception as exc:
            if verbose:
                print(f"  {sym:<12} ERROR: {exc}")

    results.sort(key=lambda r: r["conv_score"], reverse=True)
    return results


def auto_backtest_new_symbols(
    months: int = 12,
    max_symbols: int = 0,
) -> list[str]:
    """
    Find symbols that have enough candle history but have never been backtested,
    run a standard backtest on each, and return the list of newly validated ones
    (net_profit > 0).

    This closes the gap between sync_universe() (which only backfills candles)
    and select_watchlist(validated_only=True) (which requires profitable backtest runs).

    Parameters
    ----------
    months      : How many months of history to cover in each backtest (default 12).
    max_symbols : Cap on how many new symbols to backtest per call (0 = no limit).
                  Useful to avoid long startup delays on a large new batch.
    """
    from app.backtesting.engine import run_backtest

    # Symbols already tested by the CURRENT engine version. Coins that were
    # only validated by the old optimistic engine get re-tested.
    with get_conn() as conn:
        tested = {
            r[0] for r in conn.execute(
                "SELECT DISTINCT symbol FROM backtest_runs WHERE model_version = ?",
                (ENGINE_VERSION,),
            ).fetchall()
        }
        # Symbols with enough candle data to backtest
        qualified = [
            r[0] for r in conn.execute("""
                SELECT symbol FROM candles
                WHERE timeframe='30m'
                GROUP BY symbol HAVING COUNT(*) >= ?
                ORDER BY symbol
            """, (MIN_30M_CANDLES,)).fetchall()
        ]

    to_test = [s for s in qualified if s not in tested]
    if max_symbols > 0:
        to_test = to_test[:max_symbols]

    if not to_test:
        print("  [AutoBacktest] All qualified symbols already have backtest history.")
        return []

    print(f"  [AutoBacktest] {len(to_test)} symbol(s) need backtesting: {to_test}")

    now_utc  = datetime.now(timezone.utc)
    end_ms   = int((now_utc - timedelta(days=1)).timestamp() * 1000)
    start_ms = int((now_utc - timedelta(days=30 * months)).timestamp() * 1000)

    newly_validated: list[str] = []

    for sym in to_test:
        try:
            print(f"  [AutoBacktest] Running {sym} ({months}m window)...")
            result   = run_backtest(sym, start_ms, end_ms)
            metrics  = result.get("metrics", {})
            net_p    = metrics.get("net_profit", 0)
            wr       = metrics.get("win_rate", 0)
            n_trades = metrics.get("total_trades", 0)
            _c = cfg
            passed = (
                net_p  >  _c.backtest_min_profit
                and wr >= _c.backtest_min_win_rate
                and n_trades >= _c.backtest_min_trades
            )
            status = "VALIDATED ✓" if passed else (
                f"rejected (net={net_p:+.2f} wr={wr:.0%} trades={n_trades})"
            )
            print(
                f"  [AutoBacktest] {sym}: {n_trades} trades  "
                f"net={net_p:+.2f}  wr={wr:.0%}  → {status}"
            )
            if passed:
                newly_validated.append(sym)
        except Exception as exc:
            print(f"  [AutoBacktest] {sym} skipped: {exc}")

    print(
        f"  [AutoBacktest] Complete — "
        f"{len(newly_validated)}/{len(to_test)} new symbols validated."
    )
    return newly_validated


def select_watchlist(
    n: int = 0,
    validated_only: bool = True,
    fallback_symbols: list[str] | None = None,
    verbose: bool = True,
    pool: list[str] | None = None,
) -> list[str]:
    """
    Return symbols to watch, chosen by conviction score.

    Parameters
    ----------
    n                : Max symbols to return. 0 (default) = all that pass
                       the validation filter, no artificial cap.
    validated_only   : Prefer symbols with >= MIN_PROFITABLE_RUNS profitable
                       runs from the current engine version.
    fallback_symbols : Used when n > 0, no pool is given, and the scanner
                       finds fewer than N symbols. Ignored when n == 0.
    pool             : Optional whitelist (rotation pool). When set, the scan
                       is restricted to the pool and any padding comes from
                       the highest-conviction UNVALIDATED pool coins instead
                       of hardcoded fallbacks.
    verbose          : Print scanner output.

    Returns
    -------
    List of symbol strings, highest conviction first.
    """
    label = "all" if n == 0 else f"top {n}"
    if verbose:
        print(f"  [Scanner] Scanning {'pool' if pool else 'universe'} for {label} symbols"
              + (" (validated only)" if validated_only else "") + "...")

    if pool:
        # One scan of the whole pool; partition by validation so padding
        # comes from the same conviction ranking.
        ranked = scan_and_rank(validated_only=False, verbose=verbose, pool=pool)
        if validated_only:
            validated   = [r for r in ranked if r["profitable_runs"] >= MIN_PROFITABLE_RUNS]
            unvalidated = [r for r in ranked if r["profitable_runs"] < MIN_PROFITABLE_RUNS]
        else:
            validated, unvalidated = ranked, []

        if n == 0:
            selected = [r["symbol"] for r in validated]
        else:
            selected = [r["symbol"] for r in validated[:n]]
            for r in unvalidated:
                if len(selected) >= n:
                    break
                selected.append(r["symbol"])
                if verbose:
                    print(f"  [Scanner] Padded with unvalidated pool coin: {r['symbol']}")
    else:
        ranked = scan_and_rank(validated_only=validated_only, verbose=verbose)
        if n == 0:
            # Return every symbol that passed the filter — no artificial cap
            selected = [r["symbol"] for r in ranked]
        else:
            selected = [r["symbol"] for r in ranked[:n]]
            # Pad with fallback symbols only when a hard cap is set
            fallback = list(fallback_symbols or ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"])
            for sym in fallback:
                if len(selected) >= n:
                    break
                if sym not in selected:
                    selected.append(sym)
                    if verbose:
                        print(f"  [Scanner] Padded with fallback: {sym}")

    if verbose:
        print(f"  [Scanner] Selected watchlist ({len(selected)}): {selected}")

    return selected


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys, io, argparse
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    from dotenv import load_dotenv; load_dotenv()
    from app.db.migrations import run_migrations; run_migrations()

    parser = argparse.ArgumentParser(description="Scan all DB symbols for signal opportunities")
    parser.add_argument("--top",            type=int,   default=0,    help="Show only top N (0=all)")
    parser.add_argument("--direction",      type=str,   default="",   help="Filter: LONG or SHORT")
    parser.add_argument("--min-gap",        type=float, default=0.0,  help="Min MTF gap to include")
    parser.add_argument("--validated-only", action="store_true",      help="Only symbols with backtest history")
    args = parser.parse_args()

    print(f"\n{'='*80}")
    print(f"  LQ-MTF Universe Scanner  —  {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}")
    print(f"{'='*80}")

    results = scan_and_rank(validated_only=args.validated_only, verbose=True)

    if args.direction:
        results = [r for r in results
                   if r["direction"].upper() == args.direction.upper()
                   or r["decision"].upper() == args.direction.upper()]
    if args.min_gap:
        results = [r for r in results if r["gap"] >= args.min_gap]
    if args.top:
        results = results[:args.top]

    print(f"\n\n{'='*80}")
    print(f"  RANKED WATCHLIST  (sorted by conviction score)")
    print(f"{'='*80}")
    hdr = (f"  {'#':>3}  {'Symbol':<12} {'Decision':<9} {'Dir':<5} {'Gap':>5}"
           f"  {'Long':>5} {'Short':>5}  {'Regime':<22} {'ML':<10}"
           f"  {'BT':>4} {'Zone':>5} {'ZoneDist':>9}")
    print(hdr)
    print(f"  {'-'*3}  {'-'*12} {'-'*9} {'-'*5} {'-'*5}  {'-'*5} {'-'*5}  {'-'*22} {'-'*10}  {'-'*4} {'-'*5} {'-'*9}")

    for i, r in enumerate(results, 1):
        zone_tag = "YES" if r["zone_near"] else "—"
        print(
            f"  {i:>3}  {r['symbol']:<12} {r['decision']:<9} {r['direction']:<5}"
            f" {r['gap']:>5.1f}  {r['long_score']:>5.1f} {r['short_score']:>5.1f}"
            f"  {r['regime']:<22} {r['ml']:<10}"
            f"  {r['backtest_runs']:>4} {zone_tag:>5} {r['zone_dist']:>9}"
        )

    print(f"\n{'='*80}")
    print(f"  SUGGESTED WATCHLIST  (top actionable — gap > 30, validated)")
    print(f"{'='*80}")
    actionable = [r for r in results if r["decision"] in ("LONG", "SHORT") and r["gap"] >= 30][:6]
    if actionable:
        cmd = "python main.py live " + " ".join(f"--symbol {r['symbol']}" for r in actionable)
        print(f"\n  {cmd}\n")
        for r in actionable:
            ml_note = f"ML={r['ml']}" if r["ml"] != "—" else "rule-based only"
            print(f"  {r['symbol']:<12} {r['decision']} gap={r['gap']:.1f}  {r['regime']}  {ml_note}")
    else:
        print(f"\n  No strongly actionable signals — market-wide HOLD/BLOCKED")
        print(f"  Best candidates when zones align:")
        for r in results[:4]:
            print(f"  {r['symbol']:<12} gap={r['gap']:.1f}  {r['regime']}  {r['direction']}")

    print(f"\n  Total scanned: {len(results)}  |  "
          f"Actionable: {sum(1 for r in results if r['decision'] in ('LONG','SHORT'))}")
    print()
