# MEME HUNTER 🤿🚀

**Advanced Solana Meme Coin Trading Bot with DCA Entry & Grid Sell Strategies**

> Find gems, DCA in systematically, and grid sell at local maximums. Built by degens, for degens.

---

## 🎯 Overview

MEME HUNTER currently defaults to **paper trading** with a simulated 1 SOL
balance. It uses live public market prices for realistic paper fills, but does
not load a private key or broadcast transactions. Live trading requires an
explicit `PAPER_TRADING=false` configuration.

MEME HUNTER is a comprehensive trading bot designed to discover high-potential meme coins on Solana and execute sophisticated trading strategies including:

- **🔍 Token Discovery** - Real-time scanning via Pump.fun, DexScreener, and SolanaTracker WebSockets
- **🐋 KOL & Whale Tracking** - Follow smart money and copy successful traders
- **📊 Risk Analysis** - Rug checks, honeypot detection, holder distribution analysis
- **💰 DCA Entry Strategy** - Systematic dollar-cost averaging to reduce entry risk
- **📈 Grid Selling** - Take profits at multiple local maximums
- **🌙 Moon Bag Management** - Hold a portion for potential 100x runs

---

## ⚙️ Features

### Token Discovery
- **Multi-source streaming**: Pump.fun WebSocket, DexScreener API, SolanaTracker
- **Real-time alerts**: Instant notifications for new tokens
- **Custom filters**: Dev buy threshold, market cap, liquidity, buy ratio

### Risk Analysis
- **RugCheck integration**: Contract security scanning
- **GoPlus API**: Honeypot detection
- **Holder analysis**: Concentration checks, developer cluster detection
- **DEXTscore**: Safety scoring

### Trading Strategies

#### DCA (Dollar Cost Averaging)
```
Entry Strategy (default 1.5x increment, sent as sequential fills):
├── Leg 1: 21.05% of approved budget @ current quote
├── Leg 2: 31.58% of approved budget (10% lower expected target)
└── Leg 3: 47.37% of approved budget (20% lower expected target)

The fill price returned by the swap is recorded; the target is not treated as a
filled price.
```

#### Grid Selling
```
Exit Strategy (default; 80% grid + 20% moon bag):
├── Level 1: 25% @ +50% profit (Take initial)
├── Level 2: 13.75% @ +65% profit
├── Level 3: 13.75% @ +80% profit
├── Level 4: 13.75% @ +95% profit
└── Level 5: 13.75% @ +110% profit
```

#### Risk Management
- **Stop Loss**: -30% from average entry
- **Trailing Stop**: Activate at +50% profit, trail by -15%
- **Moon Bag**: Hold 20% for potential multi-X gains

### Capital, persistence and learning
The bot manages **free SOL**, not just an in-memory number. With a 1 SOL startup
balance and the default settings it will:

- cap a new meme at `10%` of the startup balance (`0.10 SOL`), also subject to
  `MAX_POSITION_PER_COIN`;
- cap total open cost basis at `80%` (`0.80 SOL`) and keep `0.05 SOL` as a
  reserve for fees/emergencies;
- reconcile the live wallet balance (or simulated paper balance) every 30
  seconds and expose wallet, committed, reserved and entry-available amounts in Telegram;
- restore open positions after a process restart.

Every signal, successful DCA leg and sell fill is written to
`/data/trade_history.db` with token, side, strategy, amount, price, reason, fee
and transaction/paper signature. Position snapshots, portfolio snapshots and
token outcomes are also saved. The separate `/data/wallets_list.db` contains
whale/KOL definitions and learned actors. Use
`/history` and `/learning` in Telegram.

When a profitable position is closed, wallets associated with that token are
stored as learned candidates. They are added to the whale tracker as
**alert-only** wallets (`copy_trade=false`) rather than being blindly copied.
Their later outcomes can produce a small, capped signal adjustment only after
at least two observations. This keeps learning explainable and prevents one
lucky trade from changing the strategy.

---

## 🛠️ Installation

```bash
# Clone the repository
git clone https://github.com/your-repo/MEME_HUNTER.git
cd MEME_HUNTER

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or: venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt
```

---

## ⚡ Configuration

Create a `config.json` file:

```json
{
  "trading": {
    "wallet_private_key": "",
    "rpc_endpoint": "https://api.mainnet-beta.solana.com",
    "paper_trading": true,
    "paper_starting_balance_sol": 1.0,
    "trade_history_db_path": "/data/trade_history.db",
    "wallets_db_path": "/data/wallets_list.db",
    "allocation_per_meme_pct": 10.0,
    "max_portfolio_allocation_pct": 80.0,
    "min_sol_reserve": 0.05,
    "max_position_per_coin": 0.1,
    "max_coins_tracked": 10,
    "min_dev_buy_sol": 0.5,
    "dca_entries": 3,
    "dca_spacing_pct": 10.0,
    "grid_levels": 5,
    "first_tp_pct": 50.0,
    "moon_bag_pct": 20.0,
    "stop_loss_pct": 30.0,
    "trailing_stop_pct": 15.0
  },
  "kol_wallets": [
    {
      "address": "WALLET_ADDRESS",
      "name": "TopTrader",
      "type": "whale",
      "min_buy_sol": 0.5,
      "copy_trade": true
    }
  ],
  "weights": {
    "volume_24h": 15.0,
    "buy_ratio": 20.0,
    "unique_wallets": 15.0,
    "dev_buy": 20.0,
    "social_score": 10.0,
    "holder_growth": 10.0,
    "liquidity": 10.0
  }
}
```

Or use environment variables:
```bash
export PAPER_TRADING=true
export PAPER_STARTING_BALANCE_SOL=1.0
export TRADE_HISTORY_DB_PATH=/data/trade_history.db
export WALLETS_DB_PATH=/data/wallets_list.db
# Only for live mode:
# export PAPER_TRADING=false
# export WALLET_PRIVATE_KEY="your_base58_key"
```

---

## 🚀 Usage

### Full Trading Bot
```bash
python meme_hunter_bot.py
```

### Paper Trading (Test Mode)
```python
from meme_hunter_bot import PaperTrader

paper = PaperTrader()
# Simulates all trades without real execution
```

### Backtesting
```python
from meme_hunter_bot import Backtester

backtester = Backtester()
results = await backtester.run_backtest(signals, price_data)
print(f"Win Rate: {results['win_rate']:.1%}")
print(f"Total Return: {results['total_return']:.1f}%")
```

---

## 📊 Scoring System

Tokens are scored based on multiple factors:

| Factor | Weight | Description |
|--------|--------|-------------|
| Dev Buy | 20% | SOL spent by dev at launch |
| Buy Ratio | 20% | % of buys vs sells |
| Unique Wallets | 15% | Trading activity spread |
| Volume | 15% | 24h trading volume |
| Potential | 10% | Lower market cap = higher potential |
| Liquidity | 10% | Available liquidity |
| KOL Mentions | +5/mention | Social validation |
| Whale Alert | +20 | Smart money activity |

---

## 🎨 Indicators Tracked

### Momentum Indicators
- Price velocity and acceleration
- Volume surges
- Consistent uptrends
- Local maximum detection

### On-Chain Indicators
- Whale/KOL wallet buys
- Holder growth rate
- LP lock status
- Developer wallet behavior

### Social Indicators
- Twitter/X mentions
- Telegram activity
- KOL recommendations

---

## 📁 Project Structure

```
MEME_HUNTER/
├── config.py           # Configuration classes
├── models.py           # Data models (Position, TokenSignal, etc.)
├── token_scanner.py    # WebSocket token discovery
├── risk_analyzer.py    # Security checks & whale tracking
├── trading_engine.py   # DCA, Grid, stops and trade accounting
├── capital_manager.py  # Per-meme allocation, reserve and balance guard
├── state_store.py      # Trade-history SQLite signals/trades/snapshots
├── wallet_store.py     # Separate whale/KOL SQLite list
├── learning.py         # Explainable profitable-actor learning
├── solana_client.py    # Solana DEX interactions
├── meme_hunter_bot.py  # Main bot orchestration
├── requirements.txt    # Dependencies
└── README.md          # This file
```

---

## ⚠️ Risk Warning

**THIS IS HIGH-RISK TRADING SOFTWARE**

- Meme coins are extremely volatile
- Never invest more than you can afford to lose
- Always DYOR before trading any token
- The bot cannot guarantee profits
- Backtest extensively before live trading
- Smart contracts can have vulnerabilities

---

## 🔧 API Requirements

### Free APIs
- Solana RPC (public endpoints)
- DexScreener API
- Jupiter Aggregator
- Pump.fun WebSocket (public)

### Premium APIs (Optional but Recommended)
- Helius RPC (faster, higher rate limits)
- GMGN.ai (whale tracking)
- Jupiter API key (higher quotas)
- SolanaTracker Pro

---

## 🧪 Testing

```bash
# Run paper trading tests
python -m pytest tests/ -v

# Test specific module
python -c "from risk_analyzer import RiskAnalyzer; print('OK')"
```

---

## 📝 Logging

Logs are saved to:
- Console (INFO level)
- `meme_hunter.log` (DEBUG level)

Sample output:
```
2026-07-18 10:30:15 - meme_hunter_bot - INFO - 🔍 NEW SIGNAL: PEPE2 (PEPE2)
   Dev Buy: 2.5 SOL
   Market Cap: $12,500
   Buy Ratio: 72.5%
   Score: 78.3

2026-07-18 10:30:18 - meme_hunter_bot - INFO - ✅ APPROVED FOR TRADING
   🚀 STRONG SIGNAL - Opening position...

2026-07-18 10:30:25 - meme_hunter_bot - INFO - 💰 Position Opened
   Entry: 0.00001234 SOL
   Invested: 0.075 SOL
   Stop Loss: 0.00000864 SOL
```

---

## 🤝 Contributing

1. Fork the repo
2. Create a feature branch
3. Make your changes
4. Submit a PR

---

## 📜 License

MIT License - Use at your own risk.

---

## 🔗 Resources

### Token Discovery
- [DexScreener](https://dexscreener.com)
- [Pump.fun](https://pump.fun)
- [Birdeye](https://birdeye.so)

### Analysis Tools
- [GMGN.ai](https://gmgn.ai) - Whale tracking
- [KOLScan](https://kolscan.io) - KOL monitoring
- [RugCheck](https://rugcheck.xyz) - Security audits
- [DEXTools](https://dextools.io) - Trading tools

### Trading Bots (References)
- [Trojan Bot](https://t.me/paris_trojanbot)
- [Banana Gun](https://banana.gg)
- [Maestro](https://t.me/MaestroSniperBot)

---

**Remember: This is not financial advice. Trade responsibly.** 🎲
