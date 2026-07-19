# Current Strategy and Decision Layers

**Status:** paper trading; simulated opening balance `1.0 SOL`.

This document records the strategy as it is currently implemented. It is a
baseline for review, not a claim that the strategy is profitable.

## Decision pipeline

```text
Operating mode and capital guard
  -> token discovery
  -> scanner filters
  -> opportunity score
  -> contract/liquidity/holder risk analysis
  -> whale/KOL confirmation
  -> historical-wallet adjustment
  -> approval/rejection
  -> capital allocation
  -> DCA entry
  -> stop/grid/moon-bag management
  -> outcome recording and wallet learning
```

## 1. Operating and capital gates

Paper mode does not load a private key or broadcast transactions. It uses live
public prices for simulated fills.

Default one-SOL limits:

| Rule | Default |
|---|---:|
| Allocation per meme | 10% of starting capital |
| Absolute position cap | 0.10 SOL |
| Maximum open exposure | 80% of starting capital |
| Minimum SOL reserve | 0.05 SOL |
| Maximum open positions | 10 |
| Minimum trade | 0.005 SOL |

The capital manager checks the simulated/live free balance, active cost basis,
reserved capital and configured limits before an entry.

## 2. Discovery and scanner filters

Sources:

- Pump.fun/PumpPortal WebSocket
- DexScreener Solana pair polling

Default filters passed to the scanners:

- developer buy at least `0.5 SOL`
- market cap below roughly `50 SOL` equivalent
- minimum buy ratio `60%`
- minimum unique wallets `10`
- minimum liquidity roughly `25 SOL` equivalent for the configured USD/SOL approximation

Signals are deduplicated by mint during the process lifetime.

## 3. Opportunity score

`TokenSignal.calculate_overall_score()` combines:

- buy ratio: up to 20 points
- unique wallets: up to 15 points
- total trades: up to 15 points
- developer buy: up to 20 points
- liquidity: up to 10 points
- lower market cap potential: up to approximately 100 points
- KOL mention: +5 each
- whale alert: +20

The score is bounded to 0–100. Signals below `40` are rejected before the
expensive risk analysis. Behavior proxies can subtract up to 20 points.

## 4. Risk gate

Risk checks run in parallel and are cached for approximately 60 seconds:

- contract/mint/freeze authority and exploitability
- honeypot and tax checks
- liquidity and LP information
- holder concentration
- developer wallet/cluster behavior
- launch behavior proxies: wash trading, mechanical trade repetition,
  coordinated early-wallet concentration and early creator selling

Required contract, honeypot, liquidity and holder checks must be available;
otherwise the risk gate fails closed.

Risk score weighting:

- contract: 35%
- liquidity: 25%
- holder distribution: 40%

Hard rejection conditions:

- honeypot
- rugged status
- high concentration
- developer cluster
- risk score below 50

## 5. Whale/KOL gate

Tracked wallet history is cached for approximately 60 seconds. Top-trader
refresh is much less frequent. Up to 50 wallets can be checked concurrently
with a concurrency limit of five.

Smart-money classification is based on tracked buys for the token:

| Tracked buys | Classification |
|---:|---|
| 0 SOL | NONE |
| <2 SOL | MINIMAL |
| 2–5 SOL | WEAK |
| 5–10 SOL | MODERATE |
| >=10 SOL | STRONG |

A whale alert is activated above `1 SOL` of tracked buying and at least two
distinct confirming wallets by default. A whale-assisted approval requires at
least `2 SOL` of tracked buying and a passing risk gate.

## 6. Final approval

Normal approval:

```text
risk score >= 50 AND opportunity score >= 60
```

Whale-assisted approval:

```text
risk score >= 50 AND whale purchases >= 2 SOL AND confirming wallets >= 2
```

Automatic opening occurs when:

```text
opportunity score >= 70 OR whale purchases > 2 SOL
```

## 7. Sizing and entry

Requested size depends on conviction:

- score 80+: 100% of the per-meme cap
- score 70–79: 75% of the per-meme cap
- lower whale-assisted approval: 50% of the per-meme cap
- whale alert multiplies the request by 1.25

The capital manager can reduce the request or reject it.

The default DCA budget distribution uses a `1.5x` increment:

- leg 1: 21.05%
- leg 2: 31.58%
- leg 3: 47.37%

The first leg opens the position. When `dca_wait_for_dips=true`, later legs
remain pending and are filled only when their lower target is reached. Their
capital remains reserved in the exposure calculation.

## 8. Position management

Stop loss:

- 30% below average entry by default

Grid:

| Level | Target | Portion of original position |
|---:|---:|---:|
| 1 | +50% | 25% |
| 2 | +65% | 13.75% |
| 3 | +80% | 13.75% |
| 4 | +95% | 13.75% |
| 5 | +110% | 13.75% |

The grid sells 80% and reserves 20% as a moon bag. A manual close or risk exit
sells the remaining tokens, including the moon bag.

Trailing protection now has separate activation and distance settings. The
default is activation at +50% and a 15% trailing distance.

## 9. Recording and learning

The trade-history database records signals, fills, positions, snapshots, P&L
and closed-token outcomes. The wallet database records configured/discovered
wallets and learned actor evidence.

A profitable closed position can promote associated wallets to the learned
list. Learned wallets are alert-only and copy-trading is disabled. After at
least two observations, historical wallet performance can adjust a future
signal by a small capped amount:

- win rate >=60%: +5
- win rate <40%: -5
- total adjustment capped at +/-10

The current learner does not automatically rewrite DCA, grid, stop-loss or
scanner parameters.

## Remaining implementation gaps to keep visible

1. Bundle/funding relationships are still heuristic proxies until a dedicated
   on-chain bundle/funding data source is added.
2. Wash-trading and mechanicality scores are early-window indicators, not
   proof of manipulation.
3. Wallet evidence comes from associated tracked actors, not a complete
   on-chain reconstruction of every actor in a token.
4. Paper mode models configurable fee and slippage assumptions but not every
   priority-fee, routing or failed-transaction effect.
5. The learner adjusts signal context only; it does not yet automatically
   retrain or change strategy parameters.

These gaps should be addressed deliberately, one at a time, after paper
trading has collected enough forward observations.
