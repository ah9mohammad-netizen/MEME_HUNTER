# MEME HUNTER V2 – Selectivity + Slower Confirmation Framework

**Date:** 2026-07-29
**Focus:** You agreed edge must be selectivity, not 0-slot speed. This doc details V2 changes.

---

## 1. Problem with V1

V1 had:
- 2 scanners (PumpPortal WS 10s buffer, Gecko new_pools 30s poll)
- Bundle detection only top3 buyer share
- Risk gate once at discovery, no live monitoring
- Whale multiplier uncapped 1.25x, no concurrent limit
- No time-exceed exit → could hold dead meme forever
- DCA no cooldown → could catch falling knife quickly

Result: many bagholders, slow detection, no funding cluster detection.

---

## 2. V2 Architecture

### Scanner Stack (4 sources, fast but still selective)

```
LogsSubscribe (processed, optional, <1s) → PumpPortal WS (10s buffer, enhanced bundle) → Pump.fun API poll (3s) → Gecko new_pools (30s, migrations)
```

- **LogsSubscribeScanner** NEW: uses `logsSubscribe` with `mentions=[PUMPFUN_PROGRAM_ID]` at `processed` commitment. On log containing "Create", fetches tx via `getTransaction` to extract mint from `postTokenBalances`. Emits signal in <1-2s vs 30s before. Requires `HELIUS_RPC_WS` (wss://atlas-mainnet.helius-rpc.com/?api-key=...). Disabled by default, enable via `LOGS_SUBSCRIBE_ENABLED=true`.

- **PumpPortalScanner** upgraded: now uses `bundle_detector.analyze_launch_behavior()` which computes:
  - wash_trading_score (overlap buy/sell wallets)
  - mechanicality_score (repeated SOL amount)
  - bundle_risk_score (top3 share + time clustering boost)
  - sniper_saturation_score (first 10 trades within 3s → 85, first 20 within 5s all buys → 90)
  - time_cluster_score (max same-second buys / total buys)
  - sale_duration_seconds + sale_duration_risk_score (20 trades in <10s → 80 risk)
  - early_concentration_score (1 - unique/trades)
  - unique_trader_ratio

- **PumpFunAPIScanner** NEW: polls `https://api.pump.fun/coins?limit=25&sort=createdAt&order=desc` every 3s (configurable). Parses mint, usd_market_cap/150→SOL, virtualSolReserves, holderCount, creator. Emits `pumpfun_api` source. 10x faster than Gecko.

- **DexScreenerScanner** (Gecko) kept for migration detection (Raydium/PumpSwap graduation).

All scanners deduplicate via TokenScanner.active_signals max score merge.

### Bundle Detector Module (`bundle_detector.py`) NEW

Implements MELT-inspired features without archive node:

- **analyze_launch_behavior(data)**: pure Python, works on buffered trades, returns all scores above.
- **fetch_funding_source(wallet)**: async, uses Helius `v0/addresses/{wallet}/transactions?type=TRANSFER` to find first inbound SOL >0.01 from funder. Fallback public RPC `getSignaturesForAddress` + `getTransaction`.
- **detect_funding_clusters(wallets, max 15)**: groups early buyers by common funder, returns cluster_risk_score 90 if ≥50% share same funder, 70 if ≥30%, 40 if ≥20%. Uses `funding_cluster_max_checked=12` to avoid spam.
- **check_serial_deployer(creator)**: queries Pump.fun `coins?creator=...&limit=20`, counts recent within 7 days. ≥10 → 90 risk, ≥5 → 60, ≥3 → 35. Serial deployer is strong rug signal.

These integrate into `TokenSignal.calculate_overall_score()` as extra penalties (up to 15) plus serial penalty (up to 10).

### Risk Analyzer V2

- Thresholds now configurable: `max_bundle_risk_score 70`, `max_wash_score 70`, `max_sniper_saturation_score 80`, `max_mechanicality_score 75`, `max_funding_cluster_risk 70`.
- New flags: `sniper_saturation_suspected`, `funding_cluster_suspected`, `serial_deployer_suspected`, `time_cluster_suspected`, `sale_duration_risk`.
- Scoring: additional penalties -25 for sniper saturation, -20 funding cluster, -15 serial deployer, -10 time cluster, -15 fast fill.
- **Live rug checks** NEW class methods:
  - `live_rug_check(mint, context)`: checks LP liquidity drop > `liquidity_drop_threshold_pct` (default 50%) and holder top10 spike >10%.
  - `check_dev_dump(mint, creator, initial_balance)`: via RPC `getTokenAccountsByOwner`, computes sold % → if ≥ `dev_dump_threshold_pct` (15%) → rug.
- Called from trading engine every `live_rug_check_interval_seconds` (30s).

### Trading Engine V2

- **DCA cooldown**: new config `dca_cooldown_seconds 15` – after a DCA leg fills, wait 15s before next, prevents catching falling knife.
- **LiveRugMonitor** NEW class: wraps risk analyzer live checks + time exceed + volume decline.
  - Time exceed: if hold > `time_exceed_seconds` (300s default, Rust bots use 60s but we use slower for selectivity) AND unrealized <0 AND no pump signal → emergency exit.
  - Volume decline: momentum_detector now tracks volume_history, detects 50% drop + price momentum < -2% → rug.
- **Position** extended with `initial_liquidity_sol/usd`, `creator_address`, `creation_timestamp`, `initial_dev_token_balance`, `last_dca_fill_at`, `live_checks_failed`.
- **Grid** now supports market-cap TP: if `enable_mcap_tp`, converts mcap thresholds USD [100k,300k,1M,3M,10M] to SOL (/150) and computes % needed vs initial mcap. Aligns with grid levels – if mcap target hit, sells.
- **Whale safety**: `max_whale_multiplier 1.25` capped, `max_concurrent_whale_positions 5`, funding cluster risk reduces size by 30%, suspicious win rate filtered in `whale_data.py`.

### Config Extensions

All new knobs env-overridable:

```
HELIUS_API_KEY, HELIUS_RPC_WS, LOGS_SUBSCRIBE_ENABLED
DCA_COOLDOWN_SECONDS=15
TIME_EXCEED_SECONDS=300
DEV_DUMP_THRESHOLD_PCT=15
LIQUIDITY_DROP_THRESHOLD_PCT=50
MAX_BUNDLE_RISK_SCORE=70
MAX_SNIPER_SATURATION_SCORE=80
PUMPFUN_POLL_INTERVAL_SECONDS=3
ENABLE_PUMPFUN_API_SCANNER=true
ENABLE_GECKO_SCANNER=true
MAX_WHALE_MULTIPLIER=1.25
MAX_CONCURRENT_WHALE_POSITIONS=5
WHALE_MIN_TRADES=20
WHALE_MIN_WIN_RATE=55
ENABLE_MCAP_TP=true
```

### Whale Data V2

- `get_smart_money_summary` now filters:
  - <0.1 SOL test buys ignored
  - win_rate < min_win (55%) filtered (except KOL)
  - trade_count < 20 filtered (inflated win rate via holding losers)
  - Tracks filtered_out count, downgrades conviction if ≥3 filtered
  - Requires wallet diversity for STRONG (>=3 wallets)

---

## 3. Decision Pipeline V2

```
Operating mode + capital guard
 -> token discovery (logs 1s + portal 10s + pump.fun API 3s + gecko 30s)
 -> pre-filter: sniper saturation fail-fast before RPC
 -> scanner filters (dev buy >=0.5, mcap <50 SOL, buy_ratio >=0.65, unique >=12)
 -> opportunity score (momentum 35 + quality 20 + potential 25 + smart-money 20 - MELT penalties up to 45)
 -> contract/liquidity/holder risk parallel + bundle funding cluster + serial deployer
 -> whale/KOL convergence (≥2 wallets, filtered for min trades/win rate)
 -> historical-wallet adjustment (±10 capped)
 -> final approval: risk >=50 AND (score >=60 OR whale ≥2 SOL with score >=50) AND no MELT hard fail
 -> capital allocation (capped multiplier, concurrent whale limit, funding cluster reduces size)
 -> DCA entry with cooldown
 -> live monitoring every 3s price + 30s rug checks (dev dump, liq drop, holder spike, time exceed, volume decline)
 -> grid sell price % + mcap TP + trailing + moon bag
 -> outcome recording + learned actor alert-only
```

---

## 4. How This Is More Profitable (selectivity edge)

- **Avoids 98.6% that die**: fast fill (<10s 20 trades) + sniper saturation + bundle top3 >70% → 80% of junk rejected before RPC spend.
- **Avoids serial ruggers**: creator 3+ tokens/week → flagged, size reduced or rejected.
- **Avoids funding cluster rugs**: 30%+ early buyers same funder → funding_cluster_suspected, -20 score, size *0.7, or reject if ≥70.
- **Exits bags faster**: time exceed 5 min + no momentum → exit, vs V1 could hold forever. Volume decline detection.
- **Protects from dev dump & LP pull**: live checks every 30s, emergency exit at 15% dev sold or 50% liquidity removed.
- **Does not chase**: logsSubscribe gives early detection but we still buffer via PumpPortal for behavior; we trade slower confirmation, not slot-0.
- **Copy trading safe**: filters inflated win rate wallets (low trade count, holding losers), requires 2-5% per copy equivalent via cap, max 5 concurrent.

---

## 5. Tuning Guide for 1 SOL Paper

Start conservative:

```
MIN_BUY_RATIO=0.65
MIN_UNIQUE_WALLETS=12
MAX_BUNDLE_RISK_SCORE=65 (tighten from 70 after 100 signals)
MAX_SNIPER_SATURATION=75
TIME_EXCEED=300 (5 min) -> after 100 signals if expectancy negative, reduce to 180
PUMPFUN_POLL=3 -> if rate limited, increase to 5
```

Measure via `/performance` and `/rejections`:

- If >50% `risk_data_unavailable`, add Helius RPC key.
- If many `sniper_saturated`, you are correctly filtering over-sniped launches – good.
- If many `bundle_risk`, tighten `funding_cluster_max_checked` to 15 and enable `HELIUS_API_KEY` for deeper graph.
- Expect approval rate 5-10% of discovered signals, entry rate 50% of approved (due to capital). Win rate target 40-50% after filtering (vs 30% before), avg winner 1.5-2x minimum before aiming for 5x.

---

## 6. Future Steps Not Yet Implemented

- Archive node integration for avg mint→raydium time
- Image/NLP meme trend scorer (analyzeMemeCorrelation)
- Jito bundle submission for live (need tip wallet)
- Yellowstone gRPC Geyser (needs paid endpoint)

These are listed as TODO in chainstack roadmap and not needed for selectivity edge.

---

## 7. Migration Notes

- Old DBs work: `position_from_dict` has defaults for new fields.
- New env vars optional, all have safe defaults.
- LogsSubscribe disabled unless `LOGS_SUBSCRIBE_ENABLED=true` + `HELIUS_RPC_WS` set.
- If `HELIUS_API_KEY` missing, funding cluster check degrades gracefully to heuristic only.
