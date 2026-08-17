# MEME HUNTER – Deep Audit + What Professional Sniper Frameworks Actually Do

**Date:** 2026-07-29  
**Mode:** Paper trading 1 SOL, no private key loaded, live public prices  
**Author:** Agent audit after code read + external research

---

## PART 1 – What your repo *actually* does today

### 1.1 Discovery pipeline

You have 3 scanner classes, but production uses 2:

- **PumpPortalScanner**: `wss://pumpportal.fun/api/data` with `subscribeNewToken` + `subscribeTrade`. You buffer 10 sec per mint, collect `traderPublicKey` for real unique wallets, track buy/sell counts. You calculate early behavior proxies: `wash_trading_score` (buy_traders ∩ sell_traders), `mechanicality_score` (repeated SOL amount), `bundle_risk_score` (top3 buyer share), `creator_sold` flag. This is inspired correctly by MELT / bundle research.

- **DexScreenerScanner**: Despite name, it polls `https://api.geckoterminal.com/api/v2/networks/solana/new_pools?page=1` every 30s. Parses H1 transactions, market_cap_usd/200 → SOL. Filters `require SOL quote`. 429 backoff 60s.

- **SolanaTrackerScanner**: exists but not wired in `meme_hunter_bot.py`. Would add `pumpfun:curve:30/50` and `graduated`.

**Deduplication:** `scanned_mints` set per process lifetime + `active_signals` dict with max score merge.

**Gap vs pros:** Pro bots do not poll 30s. They use `logsSubscribe` at `processed` commitment filtering for Pump.fun `Create` discriminator (8-byte Anchor) and Raydium `initialize2`, or Yellowstone gRPC Geyser / ShredStream for <100ms detection [2](https://docs.chainstack.com/docs/solana-creating-a-pumpfun-bot)[5](https://allenhark.com/blog/so-you-wanna-build-pumpfun-sniper). Your 10s buffer is *good* for filtering but impossible for slot-0 sniping. That is actually correct per your own research doc – you should not define yourself as competing millisecond sniper.

### 1.2 Scoring

`TokenSignal.calculate_overall_score()` bounded 0-100:

- Momentum 35pts: `buy_ratio*15 + min(unique/50,1)*10 + min(trades/100,1)*10`
- Quality 20pts: `dev_buy/2 + liquidity/50 + gmgn_quality_score capped`
- Potential 25pts: `(1 - mcap/50)*25` – lower mcap = higher
- Smart-money 20pts capped: `kol*5 + whale_alert 10`
- Penalty up to 20: max of wash/bundle/mechanical /100*20

Below 40 → `score_filtered` and queued for recheck. Score 70+ or whale >2 SOL auto-opens.

**Strength:** bounded, no longer >100 exploit.

**Weakness:** `dev_buy_known=false` for Gecko pools gives 0 quality, but then you rely on optional GMGN enrichment (30-70 score only, 50/hr limit). So many new pools sit in `launch_quality_data_unavailable`.

### 1.3 Risk gate

`RiskAnalyzer.analyze()` parallel:

- RugCheck `api.rugcheck.xyz/v1/tokens/{mint}/report` → mint/freeze authority, exploitable
- GoPlus Solana `api.gopluslabs.io/api/v1/solana/token_security?contract_addresses=` → honeypot, buy/sell tax, holder top10 fallback
- DexScreener pair liquidity $USD >5k → LP lock heuristic
- `getTokenLargestAccounts` public RPC → top10 concentration
- Dev wallet stub

Weighted: contract 35%, liquidity 25%, holders 40%. Penalties: wash -25, bundle -20, mechanical -10, creator_sold -20. Fail-closed if any required check unavailable → score capped to 25 and `is_tradeable()=false`. Hard rejects: honeypot, rugged, >30% top10, developer_cluster, wash/bundle/creator_sold proxy.

This matches pro screening checklist: **mint+freeze revoked, LP burned/locked, top holders <30%**, honeypot simulation [4](https://www.bestsniperbot.net/how-to-snipe-meme-coins)[6](https://coincodecap.com/10-best-sniper-bots-for-solana-memecoins). Good.

**Operational weakness:** public RPC rate limits holder calls, Gecko liquidity uses first pair not SOL pair, RugCheck endpoint changed over time, no caching of failures, no live re-check after entry.

### 1.4 Whale / KOL

`whale_data.py` is the real implementation, `risk_analyzer.WhaleTracker` is legacy.

- Requires `GMGN_API_KEY` + `GMGN_CLI_PATH=gmgn-cli` for read-only CLI feed. Without key, KOL/smart-money activity **silently disabled** after one warning.
- When enabled: refreshes 1/min not per wallet (good). Calls `gmgn-cli track kol --chain sol --side buy --limit 100` + `smartmoney`, parses `base_address`/`maker`, filters side=buy only, approximates SOL from USD if needed.
- Leaderboard: `https://gmgn.ai/defi/quotation/v1/rank/sol/wallets/7d?orderby=pnl_7d&limit=` [reference code in your file]
- Smart-money summary: total SOL thresholds 2/5/10 = WEAK/MODERATE/STRONG. Alert requires >1 SOL and `wallet_count >= min_confirming_whales (2)`. Assist approval requires >=2 SOL + risk pass.
- Learned wallets: if profitable close > `learned_wallet_min_profit_sol` (0.01), actors from position persisted to `wallets_list.db` as **alert-only** `copy_trade=false`. Confidence adjustment: after >=2 observations, +5 if win>=60%, -5 if win<40%, capped ±10.

This is aligned with research on adversarial copy trading: wallet quality, coin quality, timing must be separate decisions [MEME_BOT_RESEARCH.md]. You correctly do not blindly copy.

**Gap vs pros:** Trojan Bot claims $21.4B lifetime volume, built-in anti-rug auto-sell, copy any wallet with DCA & limit [1](https://github.com/jcoulaud/pumpfun-sniping-bot). GMGN web+TG supports 0.1-0.5 SOL per copy, min buy filter to ignore 0.01 SOL test buys, max 5-8 concurrent positions [8](https://memegateway.com/academy/gmgn-copy-trading-guide/). You lack those guardrails in code (you have caps but not concurrent per source). Also no funding-graph clustering – MELT shows 36.5% supply held by bundled coordinated accounts hidden as independent [1](https://arxiv.org/html/2602.13480v2). Your bundle proxy is only top3 buyer share, not funding source graph.

### 1.5 Capital & execution

`CapitalManager`:
- `reference_balance_sol` captured at startup (1 SOL paper) stays fixed unless refreshed; paper mode keeps it at `paper_starting_balance_sol` so size does not compound dramatically – conservative by design.
- `max_exposure_sol = ref * 80% = 0.8 SOL`
- `position_cap_sol = min(ref * 10% , 0.1) = 0.1 SOL`
- `available = min(wallet - reserve(0.05) - reserved, exposure - committed - reserved)`
- `reserve_for_entry` atomic with lock, checks `min_trade_sol 0.005`.

`DCAExecutor`:
- 3 entries default, multiplier 1.5 → 21.05%/31.58%/47.37%
- `dca_wait_for_dips=true`: only leg1 immediately, leg2 at -10%, leg3 -20%. Pending SOL stays reserved in committed. Once grid sold >0, pending cancelled – prevents averaging into profitable exit.
- `GridSeller`: 5 levels, first_tp 50%, spacing 15% → +50%,+65%,+80%,+95%,+110%. Sell 25% + 13.75%*4 = 80%, moon bag 20%. Emergency exit sells all including moon bag with 1000 bps slippage.
- `MomentumDetector` records 100 price points, detects pump via 5 vs previous 5 avg, local max detection but not used to trigger sell – only logged.

`PaperTradingClient`:
- Starting 1 SOL, fee 100 bps (1%) + slippage 50 bps (0.5%) explicit. `get_token_price` uses DexScreener `priceNative` cached 30s. Holdings reconstructed from `trade_history.db` on restart – good.
- Reality gap: meme volatility >10% in seconds, 30s cache + fee model still optimistic vs priority fees, Jito tips, failed txs noted in `CURRENT_STRATEGY.md`.

`StateStore` SQLite WAL:
- `positions`, `trade_events`, `signals`, `signal_observations` (one row per strategy_type), `portfolio_snapshots`, `token_outcomes`.
- Cost-basis replay corrects legacy P&L where entry_price average was unsafe after partial sells.
- `get_strategy_performance` reports approval, entry, win, expectancy, max_multiple, drawdown, hold, fees – exactly recommended benchmark in `MEME_BOT_RESEARCH`.

**Telegram:** full commands `/status /positions /balance /pnl /close /dca /history /signals /learning /performance /rejections /stop /resume /config /whales`. Alerts throttled.

### 1.6 What is happening now?

You are in safe paper mode, collecting observations. Your own docs say no audited live P&L in any open-source repo and loss mitigation is the first goal, not profit guarantee. Current implementation has improved accounting, fail-closed risk, alert-only learning – all recommended.

Remaining gaps visible in CURRENT_STRATEGY doc: bundle/funding relationships heuristic only, wash/mechanical proxies not proof, wallet evidence incomplete, paper fees not full priority-fee routing effects.

---

## PART 2 – What professional sniper frameworks actually run (GitHub + socials)

We reviewed 4 tiers:

### Tier A – Research-backed risk detection (your doc already summarized)

- **MELT / MemeTrans** [3](https://arxiv.org/abs/2602.13480): 41,470 Solana launches, 200M+ txs, 122 features, bundle traces linking coordinated accounts. Average 36.5% supply hidden via bundles. ML reduces loss 56.1%. Features: Context (SOL price, weekday), holding concentration, market activity (tx_num, trader_num, wash), bundle stats, time-series. **Lesson:** bundle graph > top-holder %.
- **Manipulation-resistant copy trading** (WWW 2026 paper): decomposes into coin evaluation, wallet selection, timing. Reports ~14% wallet returns, ~3% copier per investment after friction – not account return. Features: mean return, volatility, t-stat, time since first/last trade, purchase price/amount, bot indicators, launch candle structure, organic vs mechanical comments.
- **A Midsummer Meme's Dream**: 34,988 memes across 4 chains, 82.8% of >100% returns showed artificial growth via wash + LP inflation.
- **Solidus Labs**: 98.6% of 7M Pump.fun tokens collapsed < $1k liquidity.
- **CoinGecko Apr 2026**: 73.3% profitable wallets but counting realized only, excluding bagholders, $1-500 tier.

### Tier B – Execution infra

- **Helius Solana MEV Report**: No global public mempool, Jito bundles + private mempools, 90M arbitrages, 65k SOL sandwich extraction in 30 days. Normal Railway + public RPC cannot compete millisecond-wise.
- **Chainstack docs** [2]: bot defaults to `logsSubscribe` deriving associated bonding curve on the fly, supports `blockSubscribe` + Geyser Yellowstone gRPC. Each .yaml is a bot instance, uses Anchor IDL, 6 decimal tokens. Learning examples: `listen_logsubscribe_abc.py`, `compare_listeners.py`, `get_bonding_curve_status.py`, `listen_logsubscribe migration → PumpSwap`.
- **AllenHark 0-slot sniper** [10](https://allenhark.com/products/pumpfun-sniper): same-slot execution. Stack: ShredStream gRPC (`shredstream_uri + x_token`), direct TPU + Relay (`relay.allenhark.com/v1/sendTx`), pre-signed tx templates, blockhash cache background thread, multi-wallet rotation to bypass per-wallet caps, creator-history scoring parallel to buy path (0ms). Free binary charges 0.001 SOL per buy/sell, 40 SOL source license removes fee. Says slot 0 vs slot 2 = 20-60% upside loss.
- **Dysnix HFT guide** [10]: real setups filter `SubscribeRequestFilterAccounts` with memcmp for pool_state, Jito ShredStream proxy, protobuf decoding, 100-150ms earlier than RPC = earlier block.

### Tier C – GitHub bot implementations (unverified P&L, but structural reference)

- **chainstacklabs/pumpfun-bonkfun-bot** [2]: 271 commits, roadmap done: logsSubscribe, dual subscription, tx retries, bonding curve progress, PumpSwap migration listening, Geyser, take-profit/stop-loss, token minting. Still TODO: market-cap based selling, copy trading, token analysis, archive node integration. **Worth studying** for adapter pattern.
- **tony-42069/trader-tony-v4** [1]: Rust, 193 commits, risk analysis, copy trading REST `/api/copy/register`, dashboard, demo mode with quote-based P&L, position/state APIs, explicit strategy activation. No audited P&L.
- **0xalberto / cutupdev Rust snipers** [8]: Engine structure: `core/token.rs, tx.rs`, `engine/swap.rs`, `engine/monitor/helius.rs yellowstone.rs`, `dex/pumpfun.rs raydium.rs meteora.rs`, `services/jito.rs nozomi.rs zeroslot.rs nextblock.rs`. Strategy: entry on user purchases of new tokens, exit TP/SL + time_limit 60s if volume not increasing. Config: `JITO_BLOCK_ENGINE_URL, JITO_TIP_VALUE, TIME_EXCEED, TOKEN_AMOUNT, TP=3x SL=0.5`.
- **nssanta/Solana-Raydium-Jito-Sniper** [9]: Python asyncio + solders, real-time scanning via `logsSubscribe` detecting `InitializeInstruction2` on Raydium V4, MEV protection via Jito bundles, configurable filters liquidity + launch time.
- **jcoulaud/pumpfun-sniping-bot**: reverse sniper – creates token via OpenAI metadata, detects sniper buys, sells into them. Profit_log.json.
- **HZCX404/memecoin-trading-bots**: multi-bot toolkit sniping/volume/bundling/copy/arbitrage (27 stars) – modular but volume/bundler can be manipulation, do not copy.
- **Others (origami, ninjadevtrack)**: stars but no audited P&L, binary/password flow, not safe to run with wallet.

### Tier D – Telegram / commercial bots (feature sets, not evidence)

- **Trojan, BullX Neo, GMGN.ai, Banana Gun, MEVx, Maestro, SolTradingBot** [6]: Ranked by volume. Trojan $21.4B+ lifetime, 1% fee up to 45% Arena cashback, anti-rug auto-sell, copy, DCA, limit, trailing. GMGN best for wallet analytics + copy [1](https://github.com/jcoulaud/pumpfun-sniping-bot). Banana Gun honeypot simulation + MEV protection.
- Copy trading guides [8]: Do NOT copy blind – require min buy filter 0.5-1 SOL, max 2-5% capital per copy, max 5-8 concurrent per wallet, stop loss mandatory -50%, auto-sell. Check ranking scam: bots hold losers forever to inflate win rate, then pump via other wallets and rug copy traders [6](https://www.reddit.com/r/solana/comments/1h07gy1/copy_trading_on_gmgnai_doesnt_actually_work/).
- Manual alpha flow (BlackHatWorld 2024) [1]: DexScreener new pairs Solana <6h, sort by age, 1s candle, mcap, lock/burn 100%, socials $299 DexScreener fee signal, rugcheck.xyz check, BonkBot high priority + MEV Protect Turbo.
- **DeFade** (Reddit 2026): 14 modules – rug probability 0-100%, bundle detection via funding source+timing, sniper bot detection first 120s, insider graph, dev wallet serial deployer detection, whale + smart money tracker, copy trade detection, LP burn/lock, timeline, AI summary Claude. Free at defade.org, Phantom dApp.
- **Goodcrypto Sniper** [4]: Combines DEX Screener + on-chain activity, price, volume, liquidity, buying pressure, experienced buyer participation, social traction, auto-buy market/trailing, take-profit/stop-loss.
- **Best sniper frameworks** [9](https://www.bestsniperbot.net/how-to-snipe-meme-coins): Connect non-custodial, choose Pump.fun + Bonk.fun, safety filters revoked authorities + cap dev/top holder + bundle + honeypot simulation, small buy size, TP ladder + stop + trailing + dev-dump trigger set BEFORE entry, most memes fail – play small screened bets.

---

## PART 3 – Comparison: YOURS vs PROFESSIONALS

| Component | YOURS (MEME_HUNTER) | PRO RUST 0-SLOT (AllenHark/Chainstack/0xalberto) | PRO COPY/ANALYTICS (Trojan/GMGN/DeFade) |
|---|---|---|---|
| **Goal** | Selective filtering, paper validation first | Same-slot inclusion, beat UI | Follow proven wallets, analytics |
| **Discovery** | PumpPortal WS 10s buffer + Gecko new_pools poll 30s | Yellowstone gRPC / ShredStream / logsSubscribe processed, Anchor discriminator parsing | GMGN leaderboard rank/sol/wallets/7d every 5 min, KOL scan, Twitter/Discord NLP |
| **Latency hot path** | Python asyncio, 30s price cache | Rust no GC, pre-signed tx, blockhash cache thread, <1ms, direct TPU + Jito/Nextblock/0slot tip ladder | N/A – copy delay is risk |
| **Risk checks** | RugCheck + GoPlus + DexScreener liquidity + largest accounts, fail-closed, bundle proxy top3 | Creator history scoring parallel to buy (0ms), mint/freeze/dev concentration/liquidity floor | Bundle detection via funding source + timing graph, sniper detection first 120s, dev serial deployer, copy trade mirroring detection |
| **Holder analysis** | top10 >30% reject, fallback GoPlus holders | Visual bubble map clustering, interconnected wallets same second | Top10, dusting cult metric, thousands small holders = organic |
| **Entry** | Overall score 40/60/70 thresholds + whale ≥2 SOL confirmation | Filter pass = immediate buy with wallet rotation, often 0.05-0.1 SOL | Min buy filter 0.5-1 SOL to ignore test buys, fixed/max amount multiplier |
| **Sizing** | per-meme cap 0.1 SOL, 50-125% of cap based on conviction, exposure 0.8 SOL, reserve 0.05 | Per-token buy size, slippage cap, configurable per yaml instance | 2-5% portfolio per copy, max 5-8 concurrent per wallet |
| **DCA** | 3 legs 21/31/47% with dip targets -10/-20% wait_for_dips | Usually NO DCA – one-shot snipe, time limit 60s exit if no volume increase | DCA bot separate (BullX, Photon) |
| **Exit** | Grid 50/65/80/95/110% sells 80%, moon 20%, SL -30%, trailing act +50% dist -15% | TP 3x, SL 0.5, time_exceed 60s, market-cap based selling, volume decline exit | Partial exits 50% at 2x, 25% at 5-10x, 25% runner + trailing, auto-sell mirror wallet |
| **Anti-rug live** | Only at discovery, no live monitoring | Dev-dump listener, LP removal detection | Auto-sell on rug signals (Trojan) |
| **MEV** | None, public RPC | Jito bundles, Nozomi, ZeroSlot, NextBlock, bloXroute BDN |
| **Persistence** | SQLite WAL, cost-basis replay, observation per strategy_type, fee-aware | pnl/profit_log.json | Web dashboard, trade history, PnL tracking |
| **Learning** | Alert-only learned wallets, capped adjustment, explainable | Archive node historical analysis, accounts that consistently print tokens, avg mint→raydium time (TODO in chainstack) | Smart Money tracker win rate, fee-aware |

**Conclusion:** You are NOT building a 0-slot Rust sniper – you are building a selective, paper-first hunter – which is exactly what MELT authors recommend. That is correct for Railway + 1 SOL.

---

## PART 4 – How to make it as profitable as possible (without lying about profits)

### 0. Mindset shift (from research)

- 98.6% of Pump.fun tokens die < $1k liq (Solidus). Aim to **avoid losers**, not pick winners.
- Exceptional historical return = likely manipulation [MELT]. Measure wash, bundle, funding.
- Copy trading is adversarial. 3% copier return per investment ≠ account return. Wallet P&L can be inflated by holding losers forever.
- Execution fees determine edge. GMGN default 0.006 priority fee *2 trades = 0.012 SOL – kills small account. Lower to 0.0002-0.00075 but risk MEV front-run [Reddit GMGN thread].
- Profitable paper % (73% in CoinGecko Apr 2026) excludes unresolved bagholders and uses stale prices – need per-fill fee-aware accounting (you already do).

### 1. Fix immediate data-quality bugs (1 day)

- **Whale data**: if `GMGN_API_KEY` missing, set `whale_enabled=false` metric in `/status` and clearly log `whale confirmation unavailable` – otherwise you think filter works.
- **Holder fallback**: GoPlus top10 is percent already *100 – you sum correctly but keep source in report.
- **DexScreenerScanner**: rename to GeckoScanner, add Birdeye launch alerts as secondary source for volume spikes.
- **Price monitor**: your 30s cache is okay for paper but for live add alternative Jupiter price + fallback Birdeye – if price =0 skip monitor tick, don't trigger false stop.
- **Capital double-count**: `committed = remaining_cost + pending` and `reserved` added separately; ensure `snapshot` doesn't double count reserved committed overlap – audit one line.

### 2. Upgrade risk to MELT-style bundle detection (3-5 days) – highest ROI

Current `bundle_risk_score` = top3 buyer share – good proxy but weak vs 36.5% hidden via funding.

Implement lightweight bundle graph without full archive node:

```python
# For each early buyer in first 50 txs:
# - fetch funding source via getTransactions? Need creator of ATA?
# Use Helius/Birdeye API: get wallet funding -> first SOL transfer from same funder within 24h
# Cluster if funder same or buy within same second
# If cluster holds >20% supply → flag bundle_risk
```

Even if you can't get full graph, add:

- **Sniper saturation**: if first 50 trades all same amount (mechanical) or all within 2 sec → likely over-sniped → reject.
- **Sale duration**: MELT shows short bonding curve duration = higher risk. Track `time from first trade to now` – if <30s and already 50% curve, mark high risk.
- **Creator history**: if creator deployed >3 tokens in 7 days → serial deployer → penalize -20.
- Add these to `behavior_data` and risk penalty.

Tools: DeFade's 14 modules [5] already does bundle detection via funding source+timing – use DeFade.org API or replicate logic via Helius `getSignaturesForAddress` → parse funding.

### 3. Add live anti-rug monitors (2 days)

Your risk check runs once at discovery. Pros re-check every 30s after entry:

- **Dev sell listener**: Poll `getTokenLargestAccounts` every 30s for active positions – if dev wallet (creator_address) balance drops >10% → emergency exit.
- **LP removal**: Re-check DexScreener liquidity – if liquidity_usd drops >50% or LP unlocked removed → exit.
- **Top holder spike**: if new wallet enters top10 with >5% within 5 min → potential bundle dump → exit.

Add to `_run_position_monitor` before DCA pending.

### 4. Improve entry/exit economics

- **DCA**: Keep wait_for_dips true – your doc correctly says it prevents averaging into mid-run. But add cooldown: after leg2 fill, require 15 sec and price stabilization before leg3 – avoid catching falling knife.
- **Grid**: Keep current 80/20 split for paper, but add alternative **market-cap based TP** for migrated tokens: sell 25% at 2x mcap, 25% at 5x, etc. Your config `first_tp_pct 50%` is fine for bonding curve stage, not Raydium stage where 2x is 100%.
- **Trailing**: activation +50% and distance 15% is reasonable. Professional staircase [Reddit FIRE bot] changed: TP1 +20% 30% pos, TP2 +50% 20%, breakeven after TP1, trail 30%. Test both in `signal_observations` – your performance table already supports comparison.
- **Time-based exit**: Add `TIME_EXCEED` like Rust bots – if position open > 5 min and unrealized <0% and volume declining → close. Prevents holding dead memes.

### 5. Make copy trading safe (if you keep it)

From GMGN guide [8]:
- Min buy filter 0.5 SOL ignore test buys (you do)
- Use fixed amount 0.1-0.5 SOL per copy until verified, not % of whale buy (you use whale multiplier 1.25 – risky, cap it)
- Max 5-8 concurrent copies per source, set in CapitalManager
- Always set SL -50% per copy trade
- Vet wallets: require >=20 trades, win rate 60%+, avg hold <2h, not just 7D PnL – add checks in `whale_fetcher.fetch_top_traders` filter out wallets with <10 closed positions.
- Detect phantom win rate: if wallet holds >20 open positions with large unrealized loss – ignore (GMGN API provides positions).

### 6. Infrastructure – don't try to be 0-slot from Railway, but improve

- Add `logsSubscribe` at `processed` for Pump.fun program `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P` via Helius/QuickNode WSS – this will give you 1-2s detection instead of 10s buffer + 30s poll. Keep PumpPortal as fallback.
- For live execution later: use Helius + Jito Block Engine bundle (`https://ny.mainnet.block-engine.jito.wtf`) with tip 0.0001-0.01 SOL ladder, priority fee dynamic per congestion.
- Multi-RPC pool: primary Helius, secondary Shyft/QuickNode – rotate on failure.
- Co-location not possible on Railway – accept 80-250ms penalty, but compensate by **selective filtering** not speed.

### 7. Walk-forward validation framework (your own recommendation)

Before claiming improvement:
- Record at least 100 signals, 20 entries, per strategy_type expectancy, max drawdown, time-to-entry/exit (you already have tables)
- Never tune on same tokens used to report – split by time: train on older, test on later untouched period
- Compare `normal_scanner` vs `new_pair` vs `migration` vs `smart_money` vs `kol` via `/performance` – cut strategy types with negative expectancy
- Add `/rejections` reason breakdown – if >50% `risk_data_unavailable`, fix APIs first.

---

## PART 5 – Actionable code patches you can implement now (pseudo)

1. **Rename & add monitor**:
```python
# in token_scanner.py new class LogsSubscribeScanner
# ws_url = HELIUS WSS
# RpcTransactionLogsConfig commitment=processed
# filter mentions Pump.fun program
# extract mint from logs "Program log: ..." -> parse create ix
# emit TokenSignal immediately (no 10s buffer)
```

2. **Bundle graph stub**:
```python
async def detect_bundle(early_buyers: List[str]) -> float:
   funding = {}
   for wallet in early_buyers[:20]:
       sigs = await helius.get_signatures(wallet, limit=5)
       # first native transfer source
       source = parse_funder(sigs)
       funding.setdefault(source, []).append(wallet)
   max_cluster = max(len(v) for v in funding.values())
   return min(100, max_cluster/len(early_buyers)*100*2)  # proxy
```

3. **Live dev sell check** in trading_engine.monitor_positions:
```python
if position.creator_address:
   bal = await client.get_token_balance(position.creator_address, mint)
   if bal < initial_dev_balance*0.9: emergency_exit("dev dump")
```

4. **Cap whale multiplier**: in meme_hunter_bot.py replace `size *=1.25` with `size = min(size*1.25, position_cap*1.0)` and add max concurrent whale positions check.

5. **Time exceed exit**:
```python
if (now - position.opened_at).seconds > TIME_EXCEED and position.unrealized_pnl_pct < 0:
   await close_position(mint, "time exceed")
```

---

## PART 6 – Telegram / UX improvements

- Show in `/status`: whether GMGN key present, bundle proxy avg last 10 signals, API health.
- `/performance` already good – add filter by age: last 24h vs 7d.
- `/rejections` reason `launch_quality_data_unavailable` should trigger auto GMGN enrichment retry once per signal.

---

## PART 7 – Risk & honesty

- No open-source repo found in research review provides audited net-of-fees live P&L to justify copying with real capital (MEME_BOT_RESEARCH conclusion). Your paper 1 SOL is ideal to avoid this trap.
- Even pro bots report 30-40% win rate, average profit 5-10x on winners but 60-70% lose (xcryptobot 2026) [1](https://xcryptobot.com/blog/best-ai-trading-bot-solana-meme-coins-2026). Expect negative expectancy until bundle filter improves.
- Never import main wallet private key into any bot binary you didn't audit – especially password-protected release flows [MEME_BOT_RESEARCH].
- If you ever go live: dedicated trading wallet, small initial size, defined exit before entry, tip ladder tested.

---

## TL;DR Framework Comparison

**To be profitable, you don't need to be fastest – you need to be most selective.**

- **Fastest (Rust 0-slot)**: wins by slot 0 inclusion, needs Geyser + Jito + co-location, exits in 60s. Costly infra, coded in Rust, no GC pauses. Not suitable for Railway Python.
- **Smart-money (GMGN/Trojan)**: wins by following whales with filters, but adversarial, needs anti-inflated win rate checks, min buy filter, stop loss mandatory. Your alert-only learning is correct safer path.
- **Your edge (MELT-inspired)**: Use behavioral traces – bundle, wash, mechanical, funding cluster, dev history, sale duration – to filter out 98% of launches. Trade only when ≥2 distinct whales confirm, risk pass, dev buy 0.5-2 SOL, liquidity $5k+, unique wallets growing, no cluster. Then DCA dip-aware + grid 50/65/80/95/110 + trailing + 20% moonbag + time exceed.

Your current implementation already implements 70% of tier B (risk fail-closed, fee-aware accounting, strategy attribution, learning). The biggest missing piece is **funding-graph bundle detection** and **live dev/LP monitor** – add those before sizing up.

---

### References (key used)

- Chainstack Pump.fun bot logsSubscribe + Geyser architecture [2](https://docs.chainstack.com/docs/solana-creating-a-pumpfun-bot)
- AllenHark 0-slot Sniper specs & tips [10](https://allenhark.com/products/pumpfun-sniper)
- MELT dataset 41k launches bundle 36.5% hidden [1](https://arxiv.org/html/2602.13480v2)
- Top Solana sniper bot comparison 2026 incl Trojan $21B [6](https://coincodecap.com/10-best-sniper-bots-for-solana-memecoins)
- GMGN copy trading safety guide min buy filter, 2-5% per trade [8](https://memegateway.com/academy/gmgn-copy-trading-guide/)
- Reddit r/solana copy trading fees & inflated win rate [6](https://www.reddit.com/r/solana/comments/1h07gy1/copy_trading_on_gmgnai_doesnt_actually_work/)
- Best sniper filter checklist revoked authorities + bundle + honeypot [9](https://www.bestsniperbot.net/how-to-snipe-meme-coins)
- DeFade 14-module analyzer bundle detection [5](https://www.reddit.com/r/solana/comments/1r5r6fz/built_defade_solana_memecoin_analyzer_with_bundle/)
- HFT infrastructure logsSubscribe processed + Yellowstone [10](https://dysnix.com/blog/solana-rpc-strategy-and-infrastructure-for-hft-bots)
- xcryptobot win rate 30-40% avg 5-10x [1](https://xcryptobot.com/blog/best-ai-trading-bot-solana-meme-coins-2026)

---

**Next step for you:** Start with PART 4 #2 (bundle graph) and #3 (live dev/LP monitor). Keep paper mode 1 SOL for at least 200 signals. Use `/performance` to cut losing strategy types. Only after positive expectancy net of fees (100 bps + 50 bps + priority) consider live.

Good hunting – stay selective, not fast. 🦈
