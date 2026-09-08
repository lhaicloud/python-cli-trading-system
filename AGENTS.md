# Repository Agent Instructions

## Governing specification

Read `GOAL.md` before making architectural, strategy, research, validation, risk, execution, or live-trading changes. `GOAL.md` is the governing specification for this repository.

## Core principles

- Existing strategies are hypotheses, not trusted components.
- Research integrity and capital protection take priority over win rate, trade frequency, or apparent profitability.
- Use completed candles only for candle-based signals.
- Separate signal generation from execution.
- Record signal price separately from executable price.
- Include fees, spread, slippage, funding, margin, and liquidation assumptions where applicable.
- Risk/data validation must fail closed.
- Never fabricate market history, trades, fills, backtests, or validation results.
- Never weaken mandatory promotion thresholds simply because no strategy passes.
- Default operating modes are read-only/research/paper/testnet. Never enable live execution automatically.

## Required workflow for substantial changes

1. Inspect the current implementation first.
2. Identify affected modules and live-safety implications.
3. Prefer isolated, testable changes over broad rewrites.
4. Preserve working behavior unless evidence supports replacing it.
5. Add or update verification scripts/tests.
6. Run relevant checks when execution access is available.
7. Document assumptions, limitations, and anything still unverified.
8. Do not claim success without evidence.

## Protected behavior

Never introduce:

- martingale
- grid recovery
- averaging down
- stop widening
- revenge-trading logic
- duplicate-entry bypasses
- owner/risk authorization bypasses

Do not commit credentials or secret material.

## Live trading

Live execution must remain opt-in and fail closed. `--yes`, `--live-execution`, CLI confirmation, or agent initiative must never substitute for explicit owner authorization and risk approval.

A strategy being `LIVE_ELIGIBLE` does not authorize live trading.

## Promotion

Use the mandatory promotion gates in `GOAL.md`. Any failed mandatory gate keeps a strategy disabled.

## Definition of done

A task is not complete merely because code was written. For applicable work, completion requires implementation, verification, failure-path handling, and documentation of unresolved evidence gaps. If an external limitation blocks a success criterion, identify the blocked criterion explicitly rather than representing partial completion as success.
