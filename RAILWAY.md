# MEME HUNTER - Railway Deployment Guide

## 🚂 One-Click Deploy to Railway

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app)

### Step 1: Connect GitHub
1. Go to [Railway](https://railway.app)
2. Connect your GitHub account
3. Fork this repository to your GitHub

### Step 2: Create New Project
1. Click "New Project" → "Deploy from GitHub repo"
2. Select `MEME_HUNTER` repository
3. Railway auto-detects Dockerfile

### Step 3: Configure Environment Variables

In Railway Dashboard → Variables, add:

```env
# Paper mode: no private key or live funds are used.
PAPER_TRADING=true
PAPER_STARTING_BALANCE_SOL=1.0
TELEGRAM_BOT_TOKEN=your_bot_token_from_BotFather
TELEGRAM_CHAT_ID=your_telegram_chat_id

# Solana RPC (use premium for reliability)
RPC_ENDPOINT=https://mainnet.helius-rpc.com/?api-key=YOUR_KEY
# Or: https://api.mainnet-beta.solana.com (free, rate limited)

# Capital safety (for a 1 SOL startup wallet)
ALLOCATION_PER_MEME_PCT=10
MAX_PORTFOLIO_ALLOCATION_PCT=80
MIN_SOL_RESERVE=0.05
MIN_TRADE_SOL=0.005
MAX_POSITION_PER_COIN=0.1
MAX_COINS_TRACKED=10

# Learning and two separate Railway-backed databases
AUTO_LEARN_WALLETS=true
LEARNED_WALLET_MIN_PROFIT_SOL=0.01
TRADE_HISTORY_DB_PATH=/data/trade_history.db
WALLETS_DB_PATH=/data/wallets_list.db

# Optional
LOG_LEVEL=INFO

# For live trading only, change PAPER_TRADING=false and add:
# WALLET_PRIVATE_KEY=your_base58_private_key
```

### Step 4: Add durable storage (strongly recommended)
Create the Railway storage requested for this paper-trading run and mount it at
`/data`. The bot uses two separate SQLite files:

```text
/data/trade_history.db  # signals, fills, positions, P&L and outcomes
/data/wallets_list.db   # whale/KOL definitions and learned actors
```

Set both variables exactly as shown above. If the files are not on persistent
storage, a redeploy can lose the paper-trading history and wallet list.

### Step 5: Deploy
1. Click "Deploy"
2. Watch logs in Railway dashboard
3. Bot starts automatically

---

## 📱 Telegram Setup

### 1. Create Bot
1. Open Telegram → Search `@BotFather`
2. Send `/newbot`
3. Follow prompts, get your **Bot Token**

### 2. Get Your Chat ID
1. Search `@userinfobot`
2. Send any message
3. Bot shows your **Chat ID** (numbers like `123456789`)

### 3. Test
Send `/start` to your bot - should receive welcome message!

---

## 🐋 Whale & KOL Data Sources

The bot fetches whale/KOL data from these sources:

| Source | Data | Frequency | Notes |
|--------|------|-----------|-------|
| **GMGN.ai** | Top trader wallets | On startup + every 30 min | Auto-updates whale list |
| **Pump.fun** | Dev buy info | Real-time via WebSocket | Built-in |
| **DexScreener** | Trending tokens | Polling every 5s | Lightweight |

### How Whale Tracking Works:

1. **Startup**: Fetches top 50 traders from GMGN leaderboard
2. **New Token**: Checks if any tracked wallet bought the token
3. **Alerts**: Sends Telegram notification for whale activity > 2 SOL

### To Add Custom Wallets:

Edit `whale_data.py` or add via the bot:
```python
whale_fetcher.whale_wallets["WALLET_ADDRESS"] = WhaleWallet(
    address="WALLET_ADDRESS",
    name="My Custom Whale",
    source="manual",
    min_buy_threshold=1.0
)
```

---

## ⚡ Performance Optimizations

The bot is designed to be **lightweight**:

### Caching Strategy:
```
Token Price: 30 second cache
Whale Trades: 60 second cache
Top Traders: 5 minute cache
Risk Check: 5 minute cache
```

### Rate Limiting:
- Max 30 API requests per 10 seconds
- WebSocket for real-time (no polling)
- Parallel fetching where possible

### What This Means:
- ✅ Low CPU/memory usage
- ✅ Works on free Railway tier
- ✅ No rate limit issues
- ⚠️ 30-60 second delay on whale data (acceptable)

---

## 🔧 Troubleshooting

### Bot Not Starting
```bash
# Check logs in Railway dashboard
# Common issues:
- PAPER_TRADING=false but WALLET_PRIVATE_KEY is missing/invalid
- TELEGRAM_* variables missing
```

### Telegram Not Responding
1. Bot token incorrect?
2. Chat ID correct?
3. Bot started via `/start`?

### No Signals Found
- RPC endpoint working?
- Network connectivity?
- Check logs for scanner errors

### Rate Limit Errors
- Use premium RPC (Helius, QuickNode)
- Reduce `MAX_COINS_TRACKED`
- Check GMGN isn't blocked

---

## 📊 Monitoring

### Railway Dashboard Shows:
- CPU/Memory usage
- Live logs
- Deployment status
- Environment variables

### Telegram Commands:
```
/status    - Wallet, reserves, exposure and P&L
/balance   - Live SOL allocation guard
/positions - Active trades
/pnl       - P&L summary
/history   - Saved DCA/grid fills
/learning  - Learned actor statistics
/help      - All commands
```

---

## 💰 Cost

| Component | Free Tier | Paid |
|-----------|-----------|------|
| Railway | 500 hours/month | From $5/month |
| Helius RPC | 100k credits | From $29/month |
| Telegram | Free | Free |

**Minimum viable**: Free Railway + free RPC (rate limited)

**Recommended**: $5 Railway + $29 Helius (reliable)

---

## 🔄 Updates

To update the bot:
1. Push to GitHub
2. Railway auto-deploys

To rollback:
- Railway Dashboard → Deployments → Previous version
