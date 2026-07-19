"""
MEME HUNTER - Main Bot
======================
The main bot that orchestrates all components for meme coin hunting.

Optimized for Railway deployment with lightweight data fetching.
"""

import asyncio
import logging
import signal
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path

from config import Config, config, TradingConfig
from models import TokenSignal, TokenStatus, Position, Portfolio
from token_scanner import TokenScanner, PumpPortalScanner, DexScreenerScanner, ScanFilters
from risk_analyzer import RiskAnalyzer, WhaleTracker
from capital_manager import CapitalManager
from learning import TradeLearner
from state_store import StateStore
from wallet_store import WalletStore
from storage_paths import default_trade_history_path, default_wallets_path
from trading_engine import TradingEngine
from solana_client import SolanaTradingClient, PaperTradingClient, PumpFunTrader
from whale_data import (
    whale_fetcher,
    trending_fetcher,
    get_whale_activity_for_token,
    update_whale_list_from_gmgn,
)
from telegram_bot import init_telegram

logger = logging.getLogger(__name__)


class MemeHunterBot:
    """
    Main meme hunting bot

    Optimized for deployment on Railway with:
    - Lightweight data fetching (aggressive caching)
    - Telegram as the only UI
    - Minimal API calls
    """

    def __init__(self, config_path: str = None):
        # Load JSON first, then let Railway environment variables override it.
        if config_path:
            Config.load_from_file(config_path)
        self._load_config()

        # Two independent SQLite files: trade history/signals and wallets.
        # STATE_DB_PATH remains accepted as a legacy alias for trade history.
        trade_path = config.TRADING.trade_history_db_path
        if config.TRADING.state_db_path != "trade_history.db":
            trade_path = config.TRADING.state_db_path
        if trade_path == "trade_history.db":
            trade_path = default_trade_history_path()
        wallet_path = config.TRADING.wallets_db_path
        if wallet_path == "wallets_list.db":
            wallet_path = default_wallets_path()
        self.store = StateStore(trade_path)
        self.wallet_store = WalletStore(wallet_path)

        # Paper mode is the default. It uses live public prices for realistic
        # fills, but never loads a key or broadcasts a transaction.
        self.paper_mode = bool(config.TRADING.paper_trading)
        if self.paper_mode:
            price_client = SolanaTradingClient(
                private_key=None, rpc_endpoint=config.TRADING.rpc_endpoint
            )
            self.client = PaperTradingClient(
                starting_balance_sol=config.TRADING.paper_starting_balance_sol,
                price_client=price_client,
                trade_store=self.store,
            )
        else:
            self.client = SolanaTradingClient(
                private_key=config.TRADING.wallet_private_key,
                rpc_endpoint=config.TRADING.rpc_endpoint,
            )

        self.pump_trader = PumpFunTrader(self.client)
        self.scanner = TokenScanner()
        self.risk_analyzer = RiskAnalyzer()
        self.whale_tracker = WhaleTracker(config.KOL_WALLETS)
        for wallet in config.KOL_WALLETS:
            whale_fetcher.add_wallet(
                wallet.address, wallet.name, wallet.tier,
                wallet.min_buy_sol, copy_trade=wallet.copy_trade,
            )

        # Load the wallet list from its own durable file.
        whale_fetcher.store = self.wallet_store
        for candidate in self.wallet_store.get_wallet_candidates(limit=100):
            whale_fetcher.add_wallet(
                candidate["address"], candidate.get("name", "Tracked wallet"),
                candidate.get("source", "learned"),
                candidate.get("min_buy_threshold", 0.5), copy_trade=False,
            )
        self.capital_manager = CapitalManager(self.client)
        self.learner = TradeLearner(
            self.store, whale_fetcher, wallet_store=self.wallet_store
        )
        self.trading_engine = TradingEngine(
            self.client, capital_manager=self.capital_manager,
            store=self.store, learner=self.learner,
        )

        # Portfolio tracking (kept for compatibility with older callers).
        self.portfolio = Portfolio()

        # Telegram bot
        self.telegram = None
        telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
        telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if telegram_token and telegram_chat_id:
            self.telegram = init_telegram(telegram_token, telegram_chat_id, self.trading_engine)

        # State
        self.running = False
        self.paused = False
        self.approved_tokens: Dict[str, TokenSignal] = {}
        self.scanned_mints: set = set()

        # Price tracking (minimal)
        self.current_prices: Dict[str, float] = {}

        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _load_config(self):
        """Load capital, risk and persistence settings from environment."""
        cfg = config.TRADING
        env_map = {
            "MAX_POSITION_PER_COIN": ("max_position_per_coin", float),
            "ALLOCATION_PER_MEME_PCT": ("allocation_per_meme_pct", float),
            "MAX_PORTFOLIO_ALLOCATION_PCT": ("max_portfolio_allocation_pct", float),
            "MIN_SOL_RESERVE": ("min_sol_reserve", float),
            "MIN_TRADE_SOL": ("min_trade_sol", float),
            "MAX_COINS_TRACKED": ("max_coins_tracked", int),
            "DCA_ENTRIES": ("dca_entries", int),
            "DCA_SPACING_PCT": ("dca_spacing_pct", float),
            "DCA_WAIT_FOR_DIPS": ("dca_wait_for_dips", lambda value: str(value).lower() in {"1", "true", "yes"}),
            "GRID_LEVELS": ("grid_levels", int),
            "STOP_LOSS_PCT": ("stop_loss_pct", float),
            "TRAILING_STOP_PCT": ("trailing_stop_pct", float),
            "TRAILING_STOP_ACTIVATION_PCT": ("trailing_stop_activation_pct", float),
            "TRAILING_STOP_DISTANCE_PCT": ("trailing_stop_distance_pct", float),
            "MOON_BAG_PCT": ("moon_bag_pct", float),
            "PAPER_FEE_BPS": ("paper_fee_bps", int),
            "PAPER_SLIPPAGE_BPS": ("paper_slippage_bps", int),
            "MIN_CONFIRMING_WHALES": ("min_confirming_whales", int),
            "LEARNED_WALLET_MIN_PROFIT_SOL": ("learned_wallet_min_profit_sol", float),
            "TRADE_HISTORY_DB_PATH": ("trade_history_db_path", str),
            "WALLETS_DB_PATH": ("wallets_db_path", str),
            "PAPER_STARTING_BALANCE_SOL": ("paper_starting_balance_sol", float),
        }
        for env_name, (attribute, converter) in env_map.items():
            value = os.getenv(env_name)
            if value is not None:
                try:
                    setattr(cfg, attribute, converter(value))
                except ValueError:
                    logger.warning("Ignoring invalid %s=%r", env_name, value)
        if os.getenv("RPC_ENDPOINT"):
            cfg.rpc_endpoint = os.getenv("RPC_ENDPOINT")
        if os.getenv("WALLET_PRIVATE_KEY"):
            cfg.wallet_private_key = os.getenv("WALLET_PRIVATE_KEY")
        legacy_trade_path = os.getenv("STATE_DB_PATH") or os.getenv("DB_PATH")
        if legacy_trade_path:
            cfg.trade_history_db_path = legacy_trade_path
            cfg.state_db_path = legacy_trade_path
        if os.getenv("PAPER_TRADING") is not None:
            cfg.paper_trading = os.getenv("PAPER_TRADING", "true").lower() in {"1", "true", "yes"}
        if os.getenv("AUTO_LEARN_WALLETS") is not None:
            cfg.auto_learn_wallets = os.getenv("AUTO_LEARN_WALLETS", "true").lower() in {"1", "true", "yes"}
        logger.info(
            "Configuration loaded: mode=%s, trade_db=%s, wallet_db=%s",
            "paper" if cfg.paper_trading else "live",
            cfg.trade_history_db_path,
            cfg.wallets_db_path,
        )

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        logger.info("Shutdown signal received, cleaning up...")
        self.running = False

    async def _health_monitor(self):
        """Tiny internal Railway health endpoint; Telegram remains the only UI."""
        port = int(os.getenv("PORT", "8080"))
        try:
            async def handle(reader, writer):
                try:
                    await reader.read(1024)
                    body = b"ok"
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
                        + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                        + body
                    )
                    await writer.drain()
                finally:
                    writer.close()
                    await writer.wait_closed()

            server = await asyncio.start_server(handle, "0.0.0.0", port)
            logger.info("Health endpoint listening on port %s", port)
            while self.running:
                await asyncio.sleep(5)
            server.close()
            await server.wait_closed()
        except Exception as exc:
            logger.warning("Health endpoint unavailable: %s", exc)

    async def start(self):
        """Start the bot"""
        logger.info("=" * 60)
        logger.info("🚀 MEME HUNTER BOT STARTING")
        logger.info("=" * 60)

        # Check wallet
        balance = await self.client.get_balance()
        if self.paper_mode:
            logger.info(f"Paper balance: {balance:.4f} SOL (no live transactions)")
        else:
            logger.info(f"Wallet balance: {balance:.4f} SOL")
            if balance < 0.1:
                logger.warning("Low SOL balance! Ensure you have at least 0.1 SOL for trading")

        # Initialize scanners
        filters = ScanFilters(
            min_dev_buy_sol=config.TRADING.min_dev_buy_sol,
            max_market_cap_sol=50.0,
            min_liquidity_sol=config.TRADING.min_liquidity_usd / 200,
            min_unique_wallets=config.TRADING.min_unique_wallets,
            min_buy_ratio=config.TRADING.min_buy_ratio
        )

        # Add scanners
        self.scanner.add_scanner(PumpPortalScanner(filters))
        self.scanner.add_scanner(DexScreenerScanner(filters))

        # Set the capital baseline. With a 1 SOL wallet and defaults this
        # permits roughly 0.1 SOL per meme and keeps at least 0.05 SOL free.
        self.capital_manager.set_balance(balance)
        if self.paper_mode:
            # Keep the allocation baseline at the configured opening balance,
            # even after paper profits/losses or a restart.
            self.capital_manager.reference_balance_sol = config.TRADING.paper_starting_balance_sol
        self.portfolio.starting_balance_sol = config.TRADING.paper_starting_balance_sol if self.paper_mode else balance
        self.portfolio.current_balance_sol = balance

        # Start main loop
        self.running = True

        # Start Telegram bot if configured
        if self.telegram:
            asyncio.create_task(self.telegram.start())

        # Start main tasks
        tasks = [
            self._health_monitor(),
            self._run_scanner(),
            self._run_price_monitor(),
            self._run_position_monitor(),
            self._run_balance_monitor(),
            self._run_whale_updater(),      # Lightweight: updates whale list periodically
            self._run_status_reporting()
        ]

        await asyncio.gather(*tasks)

    async def _run_whale_updater(self):
        """Periodically update whale list from GMGN"""
        logger.info("Starting whale updater...")

        first_run = True
        while self.running:
            try:
                # Run once promptly, then only every 30 minutes. This task is
                # independent from scanning, so GMGN latency cannot block trades.
                if not first_run:
                    await asyncio.sleep(1800)
                first_run = False
                if not self.paused:
                    result = await update_whale_list_from_gmgn(limit=50)
                    logger.info(f"Whale list updated: {result['count']} wallets")
            except Exception as e:
                logger.error(f"Whale updater error: {e}")
                await asyncio.sleep(30)

    async def _run_scanner(self):
        """Run the token scanner"""
        logger.info("Starting token scanner...")

        try:
            await self.scanner.start(self._handle_token_signal)
        except Exception as e:
            logger.error(f"Scanner error: {e}")

    async def _handle_token_signal(self, signal: TokenSignal):
        """Handle discovered token signal - lightweight version"""
        if signal.mint in self.scanned_mints:
            return

        self.scanned_mints.add(signal.mint)

        logger.info(
            f"🔍 NEW SIGNAL: {signal.name} ({signal.symbol})\n"
            f"   Mint: {signal.mint[:16]}...\n"
            f"   Dev Buy: {signal.dev_buy_sol:.2f} SOL\n"
            f"   Market Cap: ${signal.market_cap_sol * 200:.2f}\n"
            f"   Buy Ratio: {signal.buy_ratio:.1%}\n"
            f"   Score: {signal.overall_score:.1f}"
        )
        self.store.record_signal(
            mint=signal.mint, symbol=signal.symbol, name=signal.name,
            score=signal.overall_score, dev_buy_sol=signal.dev_buy_sol,
            market_cap_sol=signal.market_cap_sol, liquidity_sol=signal.liquidity_sol,
            buy_ratio=signal.buy_ratio, unique_wallets=signal.unique_wallets,
            decision="discovered", reason="initial scanner signal",
        )

        # Skip if score too low or paused
        if self.paused:
            logger.info(f"   ⏸️ Bot paused, skipping")
            return

        if signal.overall_score < 40:
            logger.info(f"   ⏭️ Score too low, skipping")
            return

        # Perform risk analysis
        risk_report = await self.risk_analyzer.analyze(
            signal.mint, behavior_data=signal.behavior_data
        )

        logger.info(
            f"   📊 Risk Analysis:\n"
            f"   Risk Score: {risk_report.overall_score:.1f}/100\n"
            f"   Recommendation: {risk_report.get_recommendation()}"
        )

        if risk_report.warnings:
            for warning in risk_report.warnings[:3]:
                logger.info(f"   {warning}")

        # Check whale activity - USE LIGHTWEIGHT FETCHER
        whale_summary = {
            "total_sol": 0.0, "total_buys": 0, "wallet_count": 0,
            "all_buys": [], "top_buyer": None,
        }
        try:
            whale_summary = await get_whale_activity_for_token(signal.mint)

            if whale_summary["total_sol"] > 0:
                logger.info(
                    f"   🐋 Whale Activity:\n"
                    f"   Total Buys: {whale_summary['total_buys']}\n"
                    f"   Total SOL: {whale_summary['total_sol']:.2f}\n"
                    f"   Signal: {whale_summary['signal']}\n"
                    f"   Conviction: {whale_summary['conviction']}"
                )
                # Require convergence from multiple distinct wallets for a
                # whale alert; one large wallet is evidence, not confirmation.
                signal.is_whale_alert = (
                    whale_summary["total_sol"] > 1
                    and whale_summary.get("wallet_count", 0) >= config.TRADING.min_confirming_whales
                )
                if whale_summary['top_buyer']:
                    signal.whale_address = whale_summary['top_buyer']['wallet']
                    signal.kol_wallets = [b['wallet'] for b in whale_summary['all_buys']]
        except Exception as e:
            logger.warning(f"Whale check failed (non-critical): {e}")

        # Recalculate the bounded opportunity score now that smart-money
        # context is known.
        signal.calculate_overall_score()

        # Use persisted actor outcomes as a small, explainable adjustment.
        # Historical evidence never overrides the risk gate.
        if signal.kol_wallets:
            adjustment = self.learner.confidence_adjustment(signal.kol_wallets)
            signal.overall_score = max(0.0, signal.overall_score + adjustment)
            if adjustment:
                logger.info("   🧠 Historical actor adjustment: %+0.1f", adjustment)

        # Decision making
        should_trade = False

        if risk_report.is_tradeable(min_score=50):
            if signal.overall_score >= 60:
                should_trade = True
            elif signal.is_whale_alert and whale_summary["total_sol"] >= 2:
                should_trade = True

        decision = "approved" if should_trade else "rejected"
        self.store.record_signal(
            mint=signal.mint, symbol=signal.symbol, name=signal.name,
            score=signal.overall_score, dev_buy_sol=signal.dev_buy_sol,
            market_cap_sol=signal.market_cap_sol, liquidity_sol=signal.liquidity_sol,
            buy_ratio=signal.buy_ratio, unique_wallets=signal.unique_wallets,
            risk_score=risk_report.overall_score,
            whale_sol=whale_summary.get("total_sol", 0.0), decision=decision,
            reason=risk_report.get_recommendation(),
            metadata={
                "whale_count": whale_summary.get("total_buys", 0),
                "whale_wallet_count": whale_summary.get("wallet_count", 0),
                "behavior": signal.behavior_data,
            },
        )

        if should_trade:
            logger.info(f"   ✅ APPROVED FOR TRADING")
            self.approved_tokens[signal.mint] = signal

            # Send Telegram alert
            if self.telegram:
                await self.telegram.alert_new_signal({
                    "name": signal.name,
                    "symbol": signal.symbol,
                    "overall_score": signal.overall_score,
                    "dev_buy_sol": signal.dev_buy_sol,
                    "market_cap_sol": signal.market_cap_sol,
                    "buy_ratio": signal.buy_ratio,
                    "is_whale_alert": signal.is_whale_alert
                })

            # Auto-open position if strong signal
            if signal.overall_score >= 70 or (signal.is_whale_alert and whale_summary["total_sol"] > 2):
                logger.info(f"   🚀 STRONG SIGNAL - Opening position...")
                await self._open_position(signal)
        else:
            logger.info(f"   ❌ REJECTED - Risk too high or insufficient conviction")

    async def _open_position(self, signal: TokenSignal):
        """Open a trading position"""
        if signal.mint in self.trading_engine.active_positions:
            return

        # Check if we can open new position
        if len(self.trading_engine.active_positions) >= config.TRADING.max_coins_tracked:
            logger.warning("Max positions reached!")
            return

        # Calculate a request from conviction; CapitalManager applies the hard
        # per-meme percentage/absolute cap and checks the live free balance.
        base_size = self.capital_manager.position_cap_sol()
        if signal.overall_score >= 80:
            size = base_size
        elif signal.overall_score >= 70:
            size = base_size * 0.75
        else:
            size = base_size * 0.5
        if signal.is_whale_alert:
            size *= 1.25

        position = await self.trading_engine.open_position(signal, sol_budget=size)

        if position:
            logger.info(
                f"   💰 Position Opened:\n"
                f"   Entry: {position.entry_price:.8f} SOL\n"
                f"   Invested: {position.total_invested_sol:.4f} SOL\n"
                f"   Tokens: {position.total_tokens:.2f}\n"
                f"   Stop Loss: {position.stop_loss_price:.8f} SOL"
            )

    async def _run_balance_monitor(self):
        """Reconcile free SOL and persist a portfolio snapshot periodically."""
        while self.running:
            try:
                await self.capital_manager.refresh()
                summary = self.trading_engine.get_portfolio_summary()
                self.portfolio.current_balance_sol = summary["wallet_balance_sol"]
                await asyncio.sleep(30)
            except Exception as exc:
                logger.warning("Balance monitor error: %s", exc)
                await asyncio.sleep(30)

    async def _run_price_monitor(self):
        """Monitor token prices"""
        logger.info("Starting price monitor...")

        while self.running:
            try:
                # Update prices for active positions
                for mint in list(self.trading_engine.active_positions.keys()):
                    price = await self.client.get_token_price(mint)
                    if price > 0:
                        self.current_prices[mint] = price

                await asyncio.sleep(5)  # Update every 5 seconds

            except Exception as e:
                logger.error(f"Price monitor error: {e}")
                await asyncio.sleep(10)

    async def _run_position_monitor(self):
        """Monitor and manage positions"""
        logger.info("Starting position monitor...")

        while self.running:
            try:
                if self.trading_engine.active_positions:
                    await self.trading_engine.monitor_positions(self.current_prices)

                await asyncio.sleep(3)  # Check every 3 seconds

            except Exception as e:
                logger.error(f"Position monitor error: {e}")
                await asyncio.sleep(10)

    async def _run_telegram_alerts(self):
        """Send periodic Telegram alerts"""
        # Now handled by telegram_bot directly
        # This method kept for backwards compatibility
        pass

    async def _run_status_reporting(self):
        """Print periodic status reports"""
        while self.running:
            await asyncio.sleep(60)  # Report every minute

            summary = self.trading_engine.get_portfolio_summary()

            logger.info("=" * 60)
            logger.info("📊 PORTFOLIO STATUS")
            logger.info("=" * 60)
            logger.info(f"Active Positions: {summary['active_positions']}")
            logger.info(f"Total Invested: {summary['total_invested_sol']:.4f} SOL")
            logger.info(f"Unrealized PnL: {summary['unrealized_pnl_sol']:.4f} SOL")
            logger.info(f"Realized PnL: {summary['realized_pnl_sol']:.4f} SOL")
            logger.info(f"Total PnL: {summary['total_pnl_sol']:.4f} SOL")

            if summary['positions']:
                logger.info("\nPosition Details:")
                for pos in summary['positions']:
                    logger.info(
                        f"  {pos['symbol']}: {pos['pnl_pct']:+.1f}% "
                        f"(Grid: {pos['grid_sold_pct']:.0f}% sold)"
                    )

            logger.info("=" * 60)

    async def close_all_positions(self):
        """Emergency close all positions"""
        logger.warning("Closing all positions...")

        for mint in list(self.trading_engine.active_positions.keys()):
            await self.trading_engine.close_position(mint, "Emergency close all")


class Backtester:
    """
    Backtest trading strategies on historical data
    """

    def __init__(self):
        self.trades = []
        self.initial_balance = 10.0
        self.balance = self.initial_balance

    async def run_backtest(self, signals: List[Dict], price_data: Dict):
        """
        Run backtest on historical signals

        Args:
            signals: List of signal dicts with entry/exit points
            price_data: Historical price data
        """
        results = {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "total_pnl": 0.0,
            "max_drawdown": 0.0,
            "sharpe_ratio": 0.0
        }

        for signal in signals:
            entry_price = signal["entry_price"]
            exit_price = signal["exit_price"]
            position_size = signal["position_size"]
            direction = signal.get("direction", "long")

            if direction == "long":
                pnl_pct = (exit_price - entry_price) / entry_price
            else:
                pnl_pct = (entry_price - exit_price) / entry_price

            pnl_sol = position_size * pnl_pct

            results["total_trades"] += 1
            if pnl_sol > 0:
                results["winning_trades"] += 1
            else:
                results["losing_trades"] += 1

            results["total_pnl"] += pnl_sol
            self.balance += pnl_sol

        # Calculate metrics
        if results["total_trades"] > 0:
            results["win_rate"] = results["winning_trades"] / results["total_trades"]

        results["final_balance"] = self.balance
        results["total_return"] = (self.balance - self.initial_balance) / self.initial_balance * 100

        return results


class PaperTrader(MemeHunterBot):
    """
    Paper trading mode - simulates trades without real execution
    """

    def __init__(self):
        super().__init__()
        self.paper_balance = 10.0  # Starting paper balance
        self.positions = {}  # Track paper positions
        self.trade_history = []

    async def execute_buy(self, mint: str, symbol: str, sol_amount: float) -> Dict:
        """Simulate buy"""
        price = await self.client.get_token_price(mint)
        tokens = sol_amount / price if price > 0 else 0

        self.paper_balance -= sol_amount
        self.positions[mint] = {
            "symbol": symbol,
            "tokens": tokens,
            "entry_price": price,
            "entry_time": datetime.now(),
            "invested": sol_amount
        }

        logger.info(f"📝 PAPER BUY: {tokens:.2f} {symbol} @ {price:.8f} SOL")

        return {
            "success": True,
            "tokens_received": tokens,
            "price": price
        }

    async def execute_sell(self, mint: str, percentage: int = 100) -> Dict:
        """Simulate sell"""
        if mint not in self.positions:
            return {"success": False, "error": "No position"}

        position = self.positions[mint]
        price = await self.client.get_token_price(mint)

        tokens_to_sell = position["tokens"] * (percentage / 100)
        sol_received = tokens_to_sell * price

        pnl = sol_received - position["invested"]

        self.paper_balance += sol_received
        position["tokens"] -= tokens_to_sell
        position["invested"] -= position["invested"] * (percentage / 100)

        if position["tokens"] <= 0.001:  # Dust threshold
            del self.positions[mint]

        self.trade_history.append({
            "type": "sell",
            "symbol": position["symbol"],
            "tokens": tokens_to_sell,
            "price": price,
            "sol_received": sol_received,
            "pnl": pnl,
            "time": datetime.now()
        })

        logger.info(f"📝 PAPER SELL: {tokens_to_sell:.2f} {position['symbol']} @ {price:.8f} SOL | PnL: {pnl:+.4f} SOL")

        return {
            "success": True,
            "sol_received": sol_received,
            "pnl": pnl,
            "price": price
        }

    def get_paper_balance(self) -> Dict:
        """Get current paper trading balance"""
        positions_value = 0
        for mint, pos in self.positions.items():
            price = self.current_prices.get(mint, pos["entry_price"])
            positions_value += pos["tokens"] * price

        total_value = self.paper_balance + positions_value

        return {
            "balance": self.paper_balance,
            "positions_value": positions_value,
            "total_value": total_value,
            "total_pnl": total_value - 10.0,
            "pnl_pct": ((total_value - 10.0) / 10.0) * 100,
            "positions": len(self.positions),
            "trades": len(self.trade_history)
        }


async def main():
    """Main entry point"""
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('meme_hunter.log')
        ]
    )

    # Initialize bot
    bot = MemeHunterBot()

    try:
        await bot.start()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot crashed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
