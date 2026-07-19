# Research Review: Solana Meme Sniping and Trading Bots

**Research date:** 2026-07-19

## Executive conclusion

There are many eye-catching claims, but very few independently verifiable,
fee-aware, live results. The strongest material found is not a commercial bot
profit screenshot. It is research showing that:

1. launch-stage behavioral and bundle data can materially improve risk
   selection;
2. wallet selection and timing can be modeled, but copy trading is adversarial;
3. execution infrastructure, fees, priority tips and slippage can determine
   whether a theoretical edge survives;
4. raw wallet dashboards and bot marketing should not be treated as audited
   performance.

No open-source repo found in this review provides an independently audited,
net-of-fees live P&L record that would justify copying its strategy with real
capital.

## Evidence hierarchy

### Tier A — reproducible research and data

#### 1. MELT / MemeTrans: launch-risk detection

- Paper: [MELT on arXiv](https://arxiv.org/abs/2602.13480)
- Code/data: [git-disl/MELT](https://github.com/git-disl/MELT)

MELT covers more than 41,000 Solana memecoin launches and more than 200 million
transactions. It creates 122 features covering context, holder concentration,
market activity, bundle traces and time series. The bundle traces attempt to
link coordinated accounts that appear separate on-chain. The authors report
that model-guided selection reduced simulated investment loss by up to 56.1%.

Important limitations:

- It is an offline risk-selection experiment, not a live trading bot.
- The dataset focuses on launches that migrated to a public DEX.
- The reported strategy uses historical labels and simulated selling windows,
  not Railway/Jupiter execution.
- The result is loss mitigation, not proof of positive net returns.

**Most useful lesson for MEME HUNTER:** early-holder concentration,
bundle/funding relationships, pre-migration behavior and coordinated accounts
are likely more useful than a simple top-holder percentage.

#### 2. Manipulation-resistant copy trading

- Paper: [Resisting Manipulative Bots in Meme Coin Copy Trading](https://arxiv.org/abs/2601.08641)

This 2026 WWW paper evaluates 6,000 meme-coin projects and decomposes the
problem into three tasks:

- coin evaluation
- wallet selection
- timing assessment

The proposed multi-agent method reports approximately 14% average returns for
selected smart-money wallets and approximately 3% estimated copier returns per
meme-coin investment under modeled market frictions.

The features are highly relevant to our wallet-learning design:

- mean return and return volatility
- number of previous trades
- t-statistic of mean return
- time since first and last trade
- time since token launch
- wallet purchase price, amount and quantity
- bot/manipulation indicators
- launch candlestick structure
- organic versus mechanical comments

Important limitations:

- This is a research evaluation, not verified production performance.
- Results depend on the historical sample, labels, prompts and friction model.
- A 3% return per copied investment is not a 3% account return and does not
  describe drawdown, capacity or operational costs.
- The paper itself treats copy trading as adversarial and exploitable.

**Most useful lesson:** wallet quality, token quality and entry timing should
be separate decisions. A profitable wallet alone is not sufficient.

#### 3. Market-manipulation study

- [A Midsummer Meme's Dream](https://arxiv.org/abs/2507.01963)

This study analyzes 34,988 meme coins across four chains. Among tokens that
returned more than 100%, 82.8% showed evidence of artificial growth, including
wash trading and liquidity-pool-based price inflation. The study also connects
artificial growth to later pump-and-dump and rug-pull extraction.

**Most useful lesson:** an exceptional historical return can be evidence of
manipulation rather than evidence of a good strategy. The bot should measure
wash trading, circular volume, concentrated early ownership and funding
clusters before treating momentum as organic.

#### 4. Population-level Pump.fun results

- [CoinGecko: Pump.fun Traders Are Making a Comeback](https://www.coingecko.com/research/publications/pump-fun-traders-are-making-a-comeback)

CoinGecko reports that the proportion of profitable Pump.fun/PumpSwap wallets
rose to 73.3% in April 2026, after much weaker results through 2025. However,
most profitable wallets were in the $1–$500 tier.

The methodology explicitly warns that it:

- counts realized P&L only;
- excludes unresolved bagholder losses;
- nets flows at wallet level rather than accounting precisely per token;
- does not filter bots or wash trading;
- uses potentially stale or distorted USD prices for illiquid tokens.

**Most useful lesson:** a high percentage of profitable wallets does not mean
that a small paper account can reproduce the result. We need per-token,
per-fill, fee-aware accounting in our own ledger.

#### 5. Solana fraud prevalence

- [Solidus Labs 2025 Solana Rug Pull Report](https://www.soliduslabs.com/reports/solana-rug-pulls-pump-dumps-crypto-compliance)

Solidus analyzed Pump.fun launches from January 2024 through March 2025 and
reported that only about 97,000 of more than 7 million tokens with at least
five trades retained more than $1,000 of liquidity. Its report classifies
approximately 98.6% of the observed Pump.fun cohort as collapsing into
pump-and-dump-like outcomes. It also reports soft-rug characteristics in
approximately 93% of the Raydium pools it examined.

The exact labels are methodology-dependent, but the implication is robust:
market survival and exit liquidity deserve greater weight than a launch-time
score alone.

### Tier B — execution and infrastructure evidence

#### 6. Solana MEV and execution

- [Helius Solana MEV Report](https://www.helius.dev/blog/solana-mev-report)

The report describes Solana's lack of a global public mempool, the importance
of validator relationships, Jito bundles and alternative private mempools, and
large-scale arbitrage/sandwich activity. It reports 90,445,905 detected
successful arbitrages over one year and a specific sandwich program that
extracted 65,880 SOL over 30 days.

These are not legitimate strategy returns to copy. They demonstrate that
specialized searchers may have a structural execution advantage that a normal
Railway process, public RPC and Jupiter request cannot match.

**Most useful lesson:** we should not define this project as a competing
millisecond launch sniper. Our realistic edge should be selective filtering,
risk avoidance, measured position sizing and slower confirmation—not trying to
beat validator-adjacent searchers.

## GitHub repositories worth studying

### Higher-value references

#### TraderTony V4

- [tony-42069/trader-tony-v4](https://github.com/tony-42069/trader-tony-v4)

A Rust Solana memecoin bot with risk analysis, copy trading, REST/WebSocket
interfaces, a dashboard and demo mode. The repository had 193 commits and
recent work on dry-run quote-based P&L and token-discovery redesign when
reviewed.

Worth studying:

- demo-mode accounting
- dry-run quote retrieval
- position/state APIs
- explicit strategy activation
- compilation and deployment workflow

Not established:

- no audited live P&L was found;
- repository activity and feature breadth are not proof of profitability.

#### MELT

- [git-disl/MELT](https://github.com/git-disl/MELT)

This is the most useful repository for our research phase. It contains feature
generation, datasets, model training and evaluation code rather than a
marketing-focused sniper bot. It is especially relevant for bundle traces,
coordinated wallets and high-risk launch labeling.

#### YZYLAB Solana trade bot PoC

- [YZYLAB/solana-trade-bot](https://github.com/YZYLAB/solana-trade-bot)

A proof-of-concept using Solana Tracker APIs with HTTP and WebSocket modes,
multiple DEX adapters, filters and position management. It is useful as an
adapter/infrastructure reference, not as verified alpha.

### Useful but unverified bot implementations

#### HZCX404 bot suite

- [HZCX404/memecoin-trading-bots](https://github.com/HZCX404/memecoin-trading-bots)

A multi-bot toolkit covering sniping, volume, bundling, copy trading and
arbitrage. The repository had 27 stars and seven commits when reviewed. Its
modular structure is interesting, but volume and bundler components can be
used for market manipulation. We should not copy those components or infer
profitability from the README.

#### Melvyncodes Memecoin Sniper

- [Melvyncodes/Memecoin-Sniper-bot](https://github.com/Melvyncodes/Memecoin-Sniper-bot)

A small Telegram/PumpPortal demo-style bot with GMGN filters, top-holder and
insider checks, buy limits and time/market-cap exits. It had two commits and
no independently reported performance. It is a useful checklist of filters,
not an evidence-backed strategy.

#### Origami shitcoin sniper

- [origami-xyz/shitcoin-sniper-bot](https://github.com/origami-xyz/shitcoin-sniper-bot)

It had 156 stars and 32 commits, but its last visible activity was in 2024,
and its README points users to a password-protected binary/release flow. It
has no audited P&L and should not be run with a wallet. It is mainly evidence
that marketing claims and GitHub stars are not enough.

#### Ninjadevtrack memecoin sniper

- [ninjadevtrack/memecoin-sniper-bot](https://github.com/ninjadevtrack/memecoin-sniper-bot)

This repo has generic Raydium/Jupiter sniping, take-profit/stop-loss and rug
checker claims, but only a small amount of visible activity and no verifiable
results. Treat it as a conceptual reference only.

## Social posts and marketing claims

### Claims that should not be treated as evidence

A Reddit thread titled “This bot generates hundreds of K per month” contains
claims of very large returns, but comments describe a strategy that can lose
70–80% of trades and depends on substantial capital and a few large winners.
There is no audited account statement or reproducible trade ledger in the
thread. [Thread and discussion](https://www.reddit.com/r/solana/comments/1ji53vq/this_bot_generates_hundreds_of_k_per_month/)

A separate 2026 Reddit discussion reports that copy-trading P&L dashboards may
omit fees, priority fees and bribes, and that a practitioner found their net
edge negligible after infrastructure costs. This is anecdotal, but it matches
the methodology warnings in CoinGecko's research. [Discussion](https://www.reddit.com/r/solana/comments/1sinbw2/im_might_just_give_up_on_the_solana_bots_or_maybe/)

Several commercial or GitHub marketing pages advertise “23x faster,” “AI
profit,” or “under 300ms” execution. Those are speed/features claims, not
net-return evidence. Faster execution can also increase adverse selection,
slippage, fees and the number of bad trades.

## What should influence MEME HUNTER

The research supports these future research directions, in priority order:

1. **Add bundle/funding-graph features.** Detect related early wallets,
   coordinated supply and common funding sources.
2. **Separate wallet, coin and timing decisions.** Do not let a wallet's
   headline P&L alone trigger a trade.
3. **Use per-token realized accounting.** Include failed fills, priority fees,
   simulated slippage, price impact and all transaction costs.
4. **Detect mechanical activity.** Flag circular volume, repeated trade sizes,
   bump/comment activity and unusually regular candles.
5. **Measure exit liquidity.** Liquidity depth, creator/early-holder sell
   pressure and post-migration behavior should be hard gates.
6. **Use walk-forward validation.** Train on older tokens and evaluate on a
   later, untouched period; never tune on the same tokens used to report
   results.
7. **Keep paper mode conservative.** The current 1 SOL paper balance should
   collect enough observations before any live mode is considered.

## Recommended benchmark for our own paper results

Before claiming an improvement, record at least:

- number of signals and fills;
- approval rate and rejection reasons;
- win rate per token, not only per sell fill;
- median and mean P&L;
- expectancy after simulated fees/slippage;
- maximum drawdown and losing streak;
- time-to-entry and time-to-exit;
- performance by scanner source, risk band, whale count and market-cap band;
- performance before and after each strategy change;
- results on an untouched forward period.

A strategy should not move from paper to live because of one large winner,
one wallet dashboard, a screenshot, or a high raw win rate.
