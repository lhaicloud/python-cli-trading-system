# Multi-Asset Perpetual Trading System Goal

## Primary objective

Evolve this repository into a robust, research-first, multi-asset Binance perpetual trading system. The system must support crypto perpetuals and Binance TradFi perpetuals such as XAUUSDT/XAGUSDT while treating crypto, precious metals, commodities, indices, equities, and forex-like derivatives as separate asset classes with separate metadata, costs, funding behavior, liquidity assumptions, validation, and risk limits.

Existing strategies are hypotheses, not trusted components. They may be enhanced, combined, replaced, disabled, or removed. Never optimize merely to make a strategy pass.

## Market data

Research mode should use public/read-only Binance data where possible and should not require API secrets. Required research inputs include:

- exchange metadata and symbol status
- contract type and asset classification
- tick size, step size, minimum quantity/notional where applicable
- historical trade klines
- historical mark-price klines
- historical index-price klines
- premium-index data
- funding-rate history and funding-info changes
- public bid/ask or book-ticker observations
- mark/index prices required for liquidation/margin research

Contract and funding settings must be treated as time-varying when Binance changes them. Do not assume current settings always applied historically.

## Signal integrity

All candle-based signals must:

- use completed candles only
- avoid look-ahead bias
- separate signal generation from execution
- record signal time/price and executable time/price
- reject stale data
- reject missing/invalid contract metadata
- reject abnormal spread/slippage
- reject excessive entry drift
- require sufficient net reward:risk after costs

Missing required information means FAIL CLOSED.

## Risk and governance

Required controls include:

- fixed-fractional risk
- volatility-adjusted sizing
- portfolio open-risk cap
- asset-class risk cap
- symbol/common-factor/correlation limits
- funding-cost limits
- daily/weekly/monthly loss stops
- losing-streak stop
- maximum-drawdown halt
- immutable trade intent including intent ID and strategy version
- duplicate-entry prevention
- liquidation-distance and margin checks where applicable

Prohibited behavior:

- martingale
- grid recovery
- averaging down
- widening stops
- revenge trading
- unauthorized manual overrides

Live execution must remain disabled by default and requires explicit owner authorization plus risk approval. Research, paper/testnet, and live configurations must remain separate. Never print, log, or commit secrets.

## Backtesting

Backtesting must be event driven or event-equivalent and execution-consistent. It must model, where applicable:

- next-executable-event fills
- fees
- bid/ask spread
- slippage
- funding cash flows
- mark-price behavior
- margin/liquidation checks
- partial exits
- stop movement rules
- time exits
- maintenance/weekend/low-liquidity behavior
- rejection logs

When stop and target are both touched in the same candle and ordering cannot be known, use the conservative outcome. Do not assume TP first.

## Validation

Use chronological development, validation, and untouched test periods. The untouched period must never be used for parameter selection.

Also require:

- walk-forward validation
- per-symbol analysis
- per-asset-class analysis
- regime analysis
- 1.5x and 2x transaction-cost stress
- entry-delay stress
- slippage stress
- parameter-neighborhood stability
- Monte Carlo trade-order analysis

Report at least expectancy, profit factor, maximum drawdown, losing streak, trade count, win rate, average win/loss, fees, funding, slippage/spread impact, median symbol expectancy, profitable-symbol share, and profit concentration.

## Mandatory promotion gates

Do not promote on win rate alone. Every mandatory gate must pass:

- positive expectancy after realistic costs
- profit factor >= 1.30
- >= 200 historical trades overall
- >= 100 untouched out-of-sample trades
- positive untouched OOS expectancy
- positive expectancy at 1.5x transaction costs
- positive expectancy in a majority of traded symbols
- no single symbol contributes > 40% of total strategy profit
- maximum drawdown is inside the declared strategy risk budget
- parameter/entry-delay/slippage robustness is acceptable
- 8-12 weeks of shadow/demo execution before live eligibility

Any failed mandatory gate keeps the strategy disabled.

Strategy lifecycle labels:

- REJECTED
- RESEARCH_ONLY
- VALIDATION
- SHADOW
- PAPER_APPROVED
- LIVE_ELIGIBLE

`LIVE_ELIGIBLE` is not live authorization.

## Short-history instruments

Newly listed contracts must not inherit evidence from older instruments. If Binance-native history is too short to satisfy promotion requirements, state that clearly and keep the strategy/instrument disabled. External reference markets may inform research but must not be represented as Binance execution history.

For XAUUSDT/XAGUSDT and other TradFi contracts, structural methodology/funding changes must be segmented rather than blindly pooled into one regime.

## Required deliverables

The repository should ultimately contain:

- read-only Binance research adapter
- instrument metadata/asset-class loader
- trade/mark/index/premium/funding downloaders
- bid/ask capture
- strategy interface and multiple independent strategy families
- realistic event-driven backtester
- cost/funding/margin/liquidation models
- portfolio risk/governance engine
- chronological and walk-forward validation
- Monte Carlo and robustness testing
- automatic strategy promotion report
- unit/integration verification
- clear research findings categorized as PROVEN BY CURRENT EVIDENCE, REJECTED, INSUFFICIENT EVIDENCE, or STILL UNVERIFIED

## Definition of success

Success means the research infrastructure and controls are complete and tested, realistic validation can be run, and strategy status is determined from evidence rather than preference. If no strategy passes the promotion gates, the correct successful outcome is a functioning research system with all strategies disabled.

Never claim profitability, robustness, proof, or production readiness without evidence that satisfies this document.
