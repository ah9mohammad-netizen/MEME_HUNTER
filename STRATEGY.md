# MEME HUNTER - Trading Strategy Guide

## 🎯 Strategy Overview

MEME HUNTER uses a systematic approach to meme coin trading that combines multiple entry/exit strategies with real-time market intelligence.

---

## 📊 Token Scoring System

Each discovered token is scored 0-100 based on multiple factors:

### Component Scores

| Factor | Weight | Description | Score Logic |
|--------|--------|-------------|-------------|
| **Dev Buy** | 20% | SOL spent by dev at launch | >2 SOL = 20pts, >1 SOL = 15pts, >0.5 SOL = 10pts |
| **Buy Ratio** | 20% | Buys / Total trades | >80% = 20pts, >60% = 15pts, >50% = 10pts |
| **Unique Wallets** | 15% | Active traders | >50 = 15pts, >20 = 10pts, >10 = 5pts |
| **Volume 24h** | 15% | Trading activity | >$100k = 15pts, >$10k = 10pts, >$1k = 5pts |
| **Liquidity** | 10% | Available liquidity | >$100k = 10pts, >$10k = 7pts, >$1k = 3pts |
| **Holder Growth** | 10% | New holder rate | >20%/hr = 10pts, >10% = 7pts, >5% = 3pts |
| **Social Score** | 10% | Twitter/Telegram buzz | Has both = 10pts, has one = 5pts |

### Bonus Points

| Bonus | Points | Condition |
|-------|--------|-----------|
| **Whale Alert** | +20 | Top trader wallet bought >2 SOL |
| **KOL Mention** | +5 each | Tracked KOL bought the token |
| **Pump.fun Graduating** | +10 | Token hitting Raydium |
| **Organic Chart** | +10 | Steady climb, not airdrop pattern |

---

## 🚀 Entry Strategy: DCA (Dollar Cost Averaging)

### Why DCA?

Meme coins are volatile. Buying all at once at a bad entry can result in significant losses. DCA helps:

1. **Reduce entry risk** - Average out price volatility
2. **Preserve capital** - Don't overcommit on uncertain trades
3. **Psychological comfort** - Easier to hold through dips

### DCA Configuration

```
Total Position Budget: 0.1 SOL
DCA Entries: 3 legs
Spacing: 10% between legs
Increment: 1.5x each leg

Entry Breakdown (with the default 1.5x increment):
├── Leg 1: 21.05% = 0.021 SOL @ current price
├── Leg 2: 31.58% = 0.032 SOL @ -10% from Leg 1
└── Leg 3: 47.37% = 0.047 SOL @ -20% from Leg 1

The exact values scale with the approved budget. CapitalManager approves the
budget before any leg is sent; failed legs are not counted as invested.
```

### Entry Decision Tree

```
                    ┌─────────────────────┐
                    │  New Token Found     │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │  Risk Analysis      │
                    │  Score >= 50?       │
                    └──────────┬──────────┘
                               │
              ┌────────────────┴────────────────┐
              │ NO                              │ YES
              ▼                                 ▼
    ┌─────────────────┐              ┌─────────────────────┐
    │ Reject Token    │              │ Whale/KOL Alert?   │
    │ (too risky)     │              └──────────┬──────────┘
    └─────────────────┘                         │
                                     ┌──────────┴──────────┐
                                     │ YES                 │ NO
                                     ▼                     ▼
                          ┌─────────────┐        ┌────────────────┐
                          │ STRONG BUY  │        │ Normal Entry   │
                          │ Open max    │        │ Score >= 70?   │
                          │ position    │        └───────┬────────┘
                          └─────────────┘                │
                                              ┌────────┴────────┐
                                              │ YES             │ NO
                                              ▼                  ▼
                                    ┌───────────────┐  ┌───────────────┐
                                    │ Large Position│  │ Small Position│
                                    │ 75% of max    │  │ 50% of max    │
                                    └───────────────┘  └───────────────┘
```

---

## 📈 Exit Strategy: Grid Selling

### Grid Structure

Sell at predetermined profit levels to capture multiple peaks:

```
Grid Levels (example for 0.1 SOL entry):
┌─────────────────────────────────────────────────────┐
│  Level 5 │ 13.75% │ @ +110% │ Last grid level        │
├──────────┼────────┼─────────┼─────────────────────────┤
│  Level 4 │ 13.75% │ @ +95%  │ Secure more profit     │
├──────────┼────────┼─────────┼─────────────────────────┤
│  Level 3 │ 13.75% │ @ +80%  │ Solid profit secured    │
├──────────┼────────┼─────────┼─────────────────────────┤
│  Level 2 │ 13.75% │ @ +65%  │ Continue de-risking    │
├──────────┼────────┼─────────┼─────────────────────────┤
│  Level 1 │ 25%    │ @ +50%  │ Take some off table     │
└──────────┴──────┴─────────┴─────────────────────────┘
              ▲ Keep 20% as moon bag
```

### Dynamic Grid Adjustment

The grid isn't fixed! The bot adjusts based on momentum:

1. **Strong Momentum**: Skip some levels, aim higher
2. **Weak Momentum**: Take profit earlier, lower expectations
3. **Local Maximum Detected**: Accelerate sells

### Local Maximum Detection

```
Detection Logic:
┌─────────────────────────────────────────┐
│  Price History Analysis                 │
│                                         │
│  - Monitor price velocity                │
│  - Detect when acceleration turns neg   │
│  - Identify peak in last 5 candles      │
│                                         │
│  Peak Pattern:                          │
│  ↑↑↑↓↓ = Local max detected             │
│                                         │
│  Action:                               │
│  - Trigger sells at current levels      │
│  - Tighten trailing stop               │
└─────────────────────────────────────────┘
```

---

## 🛡️ Risk Management

### Stop Loss Rules

| Condition | Action | Rationale |
|-----------|--------|-----------|
| -30% from avg entry | HARD STOP | Cut losses fast |
| -15% with trailing stop | Trailing stop | Lock gains |
| +50% peak, then -15% | Trailing stop | Don't give back profits |

### Position Sizing

```
Position Size = f(conviction, risk)

High Conviction (>80 score) + Whale Alert:
└── 100% of max position (e.g., 0.1 SOL)

High Score (70-80):
└── 75% of max position

Good Score (60-70):
└── 50% of max position

Low Score (<60):
└── Reject or minimal (25%)

Max Positions = 10 coins
Total Exposure = 0.5-1.0 SOL (adjust to bankroll)
```

### Moon Bag Strategy

Hold a portion for potential multi-X runs:

```
For each position:
├── 80% available for grid selling
└── 20% held as "moon bag"

Moon Bag Rules:
├── Hold until 5x+ entry price
├── OR until trailing stop triggers
├── OR until dominant narrative changes
└── Sell 50% of moon bag at 10x
```

---

## 📊 Momentum Indicators

### Price Momentum

```
Momentum Score = f(recent_return, velocity, consistency)

Components:
├── 5-min return: +5% = 30pts
├── Price acceleration: +2% velocity = 25pts
├── Consistent uptrend: 4/5 candles up = 20pts
└── Volume surge: >1.5x avg volume = 25pts
```

### Volume Indicators

```
Volume Surge = Recent 5min Volume > 1.5x Avg 20min Volume

Interpretation:
├── Volume surge + price up = Bullish
├── Volume surge + price down = Distribution
└── Low volume + price move = Weak
```

---

## 🔄 Trading Flow

### Complete Trade Cycle

```
┌─────────────────────────────────────────────────────────────────┐
│  1. DISCOVER                                                     │
│  ├── Scan DexScreener/Pump.fun/WebSocket                        │
│  ├── Filter: dev buy > 0.5 SOL, mcap < $50k                     │
│  └── Emit signal if passes initial filters                      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  2. ANALYZE                                                      │
│  ├── Run risk analysis (rug, honeypot, holders)                  │
│  ├── Check whale/KOL activity                                    │
│  ├── Calculate overall score                                    │
│  └── Decision: Approve/Reject/Hold                               │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  3. ENTER (DCA)                                                  │
│  ├── Leg 1: 40% @ current price                                 │
│  ├── Wait for price dip                                          │
│  ├── Leg 2: 30% @ -10%                                          │
│  ├── Wait for price dip                                         │
│  └── Leg 3: 30% @ -20%                                          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  4. MANAGE                                                       │
│  ├── Monitor momentum indicators                                 │
│  ├── Detect local maximums                                      │
│  ├── Adjust grid if needed                                       │
│  ├── Update trailing stop                                       │
│  └── Watch for risk signals                                     │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  5. EXIT (Grid Sell)                                             │
│  ├── Level 1: Sell 25% @ +50%                                   │
│  ├── Level 2: Sell 15% @ +75%                                   │
│  ├── Level 3: Sell 15% @ +100%                                 │
│  ├── Level 4: Sell 15% @ +150%                                 │
│  └── Level 5: Hold for moon                                     │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  6. CLOSE                                                        │
│  ├── Stop loss triggered → Emergency exit                       │
│  ├── All grid levels hit → Close remaining                      │
│  ├── Moon bag hit → Sell 50% at 10x                            │
│  └── Stop trailing → Close moon bag                             │
└─────────────────────────────────────────────────────────────────┘
```

---

## 📋 Daily Checklist

Before trading:

- [ ] Check SOL balance (need >0.5 for gas + trades)
- [ ] Review whale/KOL wallets for activity
- [ ] Check market sentiment (BTC direction)
- [ ] Update blacklist (avoid repeat ruggs)
- [ ] Review yesterday's trades for learnings

During trading:

- [ ] Monitor active positions every 15 min
- [ ] Check for new whale alerts
- [ ] Watch for narrative shifts
- [ ] Log all trades with reasoning

After trading:

- [ ] Record P&L for each trade
- [ ] Analyze winners vs losers
- [ ] Update whale/KOL lists based on results
- [ ] Review strategy performance
- [ ] Adjust parameters if needed

---

## 🎯 Success Metrics

Track these to measure performance:

| Metric | Target | Acceptable | Red Flag |
|--------|--------|------------|----------|
| Win Rate | >60% | 50-60% | <50% |
| Avg Winner | 2x+ | 1.5-2x | <1.5x |
| Avg Loser | <0.7x | 0.7-0.85x | >0.85x |
| Best Trade | 10x+ | 5-10x | <5x |
| Max Drawdown | <20% | 20-30% | >30% |
| Daily Return | +2-5% | +1-2% | <1% |

---

## ⚠️ Red Flags (Avoid These!)

### Token Red Flags

- ❌ No Twitter/Telegram presence
- ❌ Dev wallet >30% of supply
- ❌ LP not burned/locked
- ❌ Mint authority NOT revoked
- ❌ Freeze authority NOT revoked
- ❌ <5 unique trading wallets
- ❌ Buy ratio <50%
- ❌ <$5k liquidity

### Trade Red Flags

- ❌ FOMO buying after +100%
- ❌ Ignoring stop loss
- ❌ Over-concentrated in one token
- ❌ Trading against BTC direction
- ❌ Holding through bad news

---

## 📚 References

### APIs & Data Sources

- **Pump.fun**: New token launches, bonding curve
- **GMGN.ai**: Whale tracking, copy trading
- **KOLScan**: KOL wallet monitoring
- **DexScreener**: Price data, pairs
- **RugCheck.xyz**: Security audits
- **GoPlus**: Honeypot detection

### Learning Resources

- Technical Analysis for Crypto (TradingView)
- On-Chain Analytics (Glassnode Academy)
- DeFi Security (Rekt News)

---

**Remember**: Even the best strategy loses sometimes. The goal is positive expected value over many trades. Stay disciplined, manage risk, and don't let emotions drive decisions.

Good hunting! 🦈
