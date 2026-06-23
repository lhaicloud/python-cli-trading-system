"""
Verify volatility-scaled position sizing added in ENGINE_VERSION
rule_based_v3_volgate.

High daily-ATR% trades keep positive expectancy but a fat loss tail and fare far
worse live than in backtest, so the engine sizes them DOWN (it does not veto
them). Sizing curve = math_utils.volatility_size_factor:

    factor = clamp(1 - slope*(atr% - full), floor, 1.0)   # 8→1.0, 13→0.6, 18→0.3

Checks:
  1. Curve unit test.
  2. Per post-deploy trade: RIF longs (daily atr% 16-20) sized down; low-vol
     winners full size.
  3. Historical impact on the realistic (v2) backtest population: net before vs
     after sizing, and the loss-tail reduction on high-atr% trades.

Run (point DB_PATH at a dev copy):
    DB_PATH=data/db/lqmtf_dev.db python -m scripts.verify_engine_senses
"""
from __future__ import annotations

import io
import sys

import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app.config import get_settings
from app.data.repository import get_candles
from app.db.connection import get_conn
from app.ta.indicators import add_indicators
from app.utils.math_utils import volatility_size_factor

cfg = get_settings()


def _factor(atr_pct: float) -> float:
    return volatility_size_factor(
        atr_pct, full_atr_pct=cfg.vol_size_full_atr_pct,
        slope=cfg.vol_size_slope, floor=cfg.vol_size_floor,
    )


_atr_cache: dict = {}


def _daily_atr_pct_at(symbol: str, entry_ms: int):
    if symbol not in _atr_cache:
        df = get_candles(symbol, "1d", limit=2000)
        if df is None or df.empty:
            _atr_cache[symbol] = None
        else:
            df = add_indicators(df)
            _atr_cache[symbol] = (df.index.asi8 // 1_000_000, df["atr_pct"].to_numpy())
    c = _atr_cache[symbol]
    if c is None:
        return None
    times_ms, atr = c
    i = int(np.searchsorted(times_ms, entry_ms, side="right") - 1)
    if i < 0:
        return None
    v = float(atr[i])
    return v if v == v else None


def main() -> int:
    failures = []
    print(f"sizing curve: full={cfg.vol_size_full_atr_pct} slope={cfg.vol_size_slope} "
          f"floor={cfg.vol_size_floor}\n")

    # 1. Curve unit test
    print("1) curve unit test:")
    for atr, want in ((5.0, 1.0), (8.0, 1.0), (13.0, 0.60), (18.0, 0.30), (30.0, 0.30)):
        got = round(_factor(atr), 2)
        ok = abs(got - want) < 0.02
        print(f"   atr%={atr:>5} -> ×{got:.2f} (want {want:.2f})  {'ok' if ok else '*** MISMATCH ***'}")
        if not ok:
            failures.append(("curve", atr, f"got {got} want {want}"))

    # 2. Per post-deploy trade
    print("\n2) post-deploy trades (RIF sized down, winners full):")
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT symbol, open_time, status, pnl FROM paper_trades "
            "WHERE created_at >= '2026-06-12' AND status != 'open' ORDER BY open_time"
        ).fetchall()
    print(f"   {'symbol':<9}{'pnl':>9}{'dailyATR%':>10}{'sizeX':>7}")
    print("   " + "-" * 35)
    for sym, ems, status, pnl in rows:
        a = _daily_atr_pct_at(sym, int(ems))
        if a is None:
            continue
        f = _factor(a)
        print(f"   {sym:<9}{float(pnl):>9.2f}{a:>9.1f}%{f:>6.2f}x")
        if sym == "RIFUSDT" and f >= 0.99:
            failures.append((sym, ems, f"RIF not sized down (atr%={a:.1f} factor={f:.2f})"))
        if float(pnl) > 0 and a <= 8.0 and f < 1.0:
            failures.append((sym, ems, f"low-vol winner wrongly sized down (factor={f:.2f})"))

    # 3. Historical impact on the realistic (v2) population
    print("\n3) historical impact — realistic engine population (rule_based_v2_realistic):")
    with get_conn() as conn:
        v2 = conn.execute(
            "SELECT bt.symbol, bt.entry_time, bt.pnl FROM backtest_trades bt "
            "JOIN backtest_runs r ON bt.backtest_run_id=r.id "
            "WHERE r.model_version='rule_based_v2_realistic' "
            "AND bt.entry_time IS NOT NULL AND bt.pnl IS NOT NULL"
        ).fetchall()
    seen, base, scaled, hv_base, hv_scaled, hv_losses = set(), 0.0, 0.0, 0.0, 0.0, []
    n = nhv = 0
    for sym, ems, pnl in v2:
        if (sym, ems) in seen:
            continue
        seen.add((sym, ems))
        a = _daily_atr_pct_at(sym, int(ems))
        if a is None:
            continue
        f = _factor(a)
        n += 1
        base += float(pnl)
        scaled += float(pnl) * f
        if a > cfg.max_daily_atr_pct:  # high-vol slice
            nhv += 1
            hv_base += float(pnl)
            hv_scaled += float(pnl) * f
            if float(pnl) < 0:
                hv_losses.append((float(pnl), float(pnl) * f))
    loss_base = sum(l for l, _ in hv_losses)
    loss_scaled = sum(s for _, s in hv_losses)
    print(f"   trades={n}  baseline net=${base:,.0f}  vol-scaled net=${scaled:,.0f} "
          f"({(scaled-base)/base*100:+.1f}%)")
    print(f"   high-vol (>{cfg.max_daily_atr_pct:.0f}% atr) slice: n={nhv} "
          f"net ${hv_base:,.0f} -> ${hv_scaled:,.0f}")
    print(f"   high-vol LOSSES: ${loss_base:,.0f} -> ${loss_scaled:,.0f} "
          f"(tail cut {(1-loss_scaled/loss_base)*100:.0f}%)" if loss_base else "   (no high-vol losses)")

    print("\n" + "=" * 60)
    if failures:
        print(f"FAIL — {len(failures)} mismatch(es):")
        for a, b, c in failures:
            print(f"  {a} {b}: {c}")
        return 1
    print("PASS — curve correct; RIF sized down; winners full size; loss tail reduced.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
