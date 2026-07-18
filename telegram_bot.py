"""
MEME HUNTER - Telegram Bot Commands
==================================
Interactive Telegram bot for controlling the trading bot.
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, Optional
from dataclasses import dataclass
import aiohttp

from models import Position, Portfolio

logger = logging.getLogger(__name__)


class TelegramBot:
    """
    Telegram Bot Handler
    Handles commands and messages from Telegram.
    """

    def __init__(self, token: str, chat_id: str, trading_engine=None):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.trading_engine = trading_engine

        # Command handlers
        self.commands = {
            "/start": self.cmd_start,
            "/help": self.cmd_help,
            "/status": self.cmd_status,
            "/positions": self.cmd_positions,
            "/balance": self.cmd_balance,
            "/pnl": self.cmd_pnl,
            "/close": self.cmd_close,
            "/dca": self.cmd_dca,
            "/history": self.cmd_history,
            "/learning": self.cmd_learning,
            "/stop": self.cmd_stop,
            "/resume": self.cmd_resume,
            "/config": self.cmd_config,
            "/whales": self.cmd_whales,
        }

        # State
        self.running = False
        self.update_offset = 0

    async def send_message(self, text: str, parse_mode: str = "Markdown"):
        """Send message to Telegram"""
        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=10) as resp:
                    if resp.status != 200:
                        error = await resp.text()
                        logger.error(f"Telegram send failed: {error}")
        except Exception as e:
            logger.error(f"Telegram error: {e}")

    async def send_alert(self, title: str, message: str, emoji: str = "📊"):
        """Send formatted alert"""
        text = f"{emoji} *{title}*\n\n{message}"
        await self.send_message(text)

    async def start(self):
        """Start polling for updates"""
        self.running = True

        await self.send_alert(
            "🟢 MEME HUNTER ONLINE",
            f"Bot started at {datetime.now().strftime('%H:%M:%S')}\n\n"
            f"Use /help for available commands.",
            "🚀"
        )

        while self.running:
            try:
                await self._poll_updates()
            except Exception as e:
                logger.error(f"Poll error: {e}")
                await asyncio.sleep(5)

    async def stop(self):
        """Stop polling"""
        self.running = False
        await self.send_alert("🔴 MEME HUNTER OFFLINE", "Bot stopped.", "⛔")

    async def _poll_updates(self):
        """Poll for Telegram updates"""
        url = f"{self.base_url}/getUpdates"
        params = {"offset": self.update_offset, "timeout": 30}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=35) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        updates = data.get("result", [])

                        for update in updates:
                            await self._handle_update(update)
                            self.update_offset = update["update_id"] + 1
                    else:
                        await asyncio.sleep(5)
        except asyncio.TimeoutError:
            pass  # Normal timeout, continue
        except Exception as e:
            logger.error(f"Poll error: {e}")
            await asyncio.sleep(5)

    async def _handle_update(self, update: Dict):
        """Handle incoming update"""
        if "message" not in update:
            return

        message = update["message"]
        text = message.get("text", "")
        chat_id = str(message.get("chat", {}).get("id", ""))

        # Only respond to authorized chat
        if chat_id != self.chat_id:
            return

        # Parse command
        parts = text.split()
        command = parts[0] if parts else ""

        if command in self.commands:
            handler = self.commands[command]
            await handler(parts[1:] if len(parts) > 1 else [])
        else:
            await self.send_message("Unknown command. Use /help for available commands.")

    # ====================
    # COMMAND HANDLERS
    # ====================

    async def cmd_start(self, args):
        """Start the bot"""
        await self.send_alert(
            "🚀 MEME HUNTER",
            "Welcome! Bot is running.\n"
            "Use /status to check portfolio.\n"
            "Use /help for all commands.",
            "🤖"
        )

    async def cmd_help(self, args):
        """Show help"""
        help_text = """
*📚 Available Commands:*

*Portfolio:*
/status - Full portfolio status
/positions - List active positions
/balance - Check SOL balance
/pnl - Show P&L summary

*Trading:*
/close [symbol] - Close position
/dca [symbol] [SOL] - Add DCA to position
/stop - Pause trading

*Records:*
/history - Recent fills saved in SQLite
/learning - Learned wallet/token statistics
/resume - Resume trading

*Info:*
/config - Show current config
/whales - Show tracked whales

*Support:*
/help - Show this message
"""
        await self.send_message(help_text)

    async def cmd_status(self, args):
        """Show portfolio status"""
        if not self.trading_engine:
            await self.send_message("Trading engine not initialized")
            return

        summary = self.trading_engine.get_portfolio_summary()

        status = f"""
*📊 PORTFOLIO STATUS*

💰 Wallet SOL: `{summary['wallet_balance_sol']:.4f}`
🟢 Available for entries: `{summary['available_sol']:.4f} SOL`
🔒 Committed: `{summary['committed_sol']:.4f} SOL`
📦 Positions: {summary['active_positions']}
💵 Invested: `{summary['total_invested_sol']:.4f} SOL`
📈 Unrealized PnL: `{summary['unrealized_pnl_sol']:+.4f} SOL`
✅ Realized PnL: `{summary['realized_pnl_sol']:+.4f} SOL`
🎯 Total PnL: `{summary['total_pnl_sol']:+.4f} SOL`
"""
        await self.send_message(status)

    async def cmd_positions(self, args):
        """List active positions"""
        if not self.trading_engine:
            await self.send_message("Trading engine not initialized")
            return

        positions = self.trading_engine.active_positions

        if not positions:
            await self.send_message("📭 No active positions")
            return

        lines = ["*📦 ACTIVE POSITIONS*\n"]

        for mint, pos in positions.items():
            pnl_emoji = "🟢" if pos.unrealized_pnl_pct >= 0 else "🔴"

            lines.append(
                f"{pnl_emoji} *{pos.token_symbol}*\n"
                f"   Entry: `{pos.entry_price:.8f}`\n"
                f"   PnL: `{pos.unrealized_pnl_pct:+.1f}%`\n"
                f"   Grid: {pos.grid_sold_pct:.0f}% sold\n"
                f"   Moon Bag: {pos.moon_bag_active}\n"
            )

        await self.send_message("\n".join(lines))

    async def cmd_balance(self, args):
        """Show live wallet and allocation balance."""
        if not self.trading_engine:
            await self.send_message("Trading engine not initialized")
            return
        capital = self.trading_engine.capital_manager
        await capital.refresh()
        summary = self.trading_engine.get_portfolio_summary()
        await self.send_message(
            f"*💰 BALANCE*\n\n"
            f"Wallet: `{summary['wallet_balance_sol']:.4f} SOL`\n"
            f"Available: `{summary['available_sol']:.4f} SOL`\n"
            f"Committed: `{summary['committed_sol']:.4f} SOL`\n"
            f"Reserve: `{summary['min_sol_reserve']:.4f} SOL`\n"
            f"Per-meme cap: `{summary['position_cap_sol']:.4f} SOL`"
        )

    async def cmd_pnl(self, args):
        """Show P&L"""
        if not self.trading_engine:
            await self.send_message("Trading engine not initialized")
            return

        summary = self.trading_engine.get_portfolio_summary()

        pnl_text = f"""
*💰 P&L REPORT*

*Unrealized:* `{summary['unrealized_pnl_sol']:+.4f} SOL`
*Realized:* `{summary['realized_pnl_sol']:+.4f} SOL`
*Total:* `{summary['total_pnl_sol']:+.4f} SOL`

*Trades:* {summary.get('total_trades', 0)}
*Win Rate:* {summary.get('win_rate', 0):.1f}%
"""
        await self.send_message(pnl_text)

    async def cmd_close(self, args):
        """Close a position"""
        if not args:
            await self.send_message("Usage: /close [symbol]\nExample: /close PEPE")
            return

        symbol = args[0].upper()

        if not self.trading_engine:
            await self.send_message("Trading engine not initialized")
            return

        # Find position by symbol
        mint = None
        for m, pos in self.trading_engine.active_positions.items():
            if pos.token_symbol.upper() == symbol:
                mint = m
                break

        if mint:
            await self.trading_engine.close_position(mint, "Manual close from Telegram")
            await self.send_alert(
                f"✅ CLOSED {symbol}",
                f"Position closed manually via Telegram.",
                "🟢"
            )
        else:
            await self.send_message(f"No position found for {symbol}")

    async def cmd_dca(self, args):
        """Add DCA to position"""
        if not args:
            await self.send_message("Usage: /dca [symbol] [SOL amount]\nExample: /dca PEPE 0.05")
            return

        symbol = args[0].upper()
        try:
            amount = float(args[1]) if len(args) > 1 else 0.05
        except ValueError:
            await self.send_message("Amount must be a number of SOL.")
            return
        if not self.trading_engine:
            await self.send_message("Trading engine not initialized")
            return
        mint = next(
            (m for m, pos in self.trading_engine.active_positions.items()
             if pos.token_symbol.upper() == symbol), None
        )
        if not mint:
            await self.send_message(f"No position found for {symbol}")
            return
        order = await self.trading_engine.add_dca(mint, amount)
        if order and order.status == "filled":
            await self.send_message(
                f"✅ DCA filled for {symbol}: `{order.amount_sol:.4f} SOL` "
                f"@ `{order.actual_price:.8f}`"
            )
        else:
            await self.send_message("DCA was not placed: balance or per-meme cap would be exceeded.")

    async def cmd_history(self, args):
        """Show recent persisted fills."""
        if not self.trading_engine:
            await self.send_message("Trading engine not initialized")
            return
        rows = self.trading_engine.store.get_trade_history(limit=10)
        if not rows:
            await self.send_message("📭 No fills have been recorded yet.")
            return
        lines = ["*🧾 RECENT FILLS*"]
        for row in rows:
            emoji = "🟢" if row["side"] == "buy" else "🔴"
            lines.append(
                f"{emoji} `{row['symbol']}` {row['side'].upper()} "
                f"{row['sol_amount']:.4f} SOL · {row['strategy']}\n"
                f"   {row['reason']} · {row['created_at']}"
            )
        await self.send_message("\n".join(lines))

    async def cmd_learning(self, args):
        """Show the outcome-learning summary and candidates."""
        if not self.trading_engine:
            await self.send_message("Trading engine not initialized")
            return
        summary = self.trading_engine.learner.summary()
        candidates = self.trading_engine.store.get_wallet_candidates(limit=5)
        lines = [
            "*🧠 LEARNING*",
            f"Closed tokens: `{summary['closed_tokens']}`",
            f"Profitable tokens: `{summary['profitable_tokens']}`",
            f"Learned wallets: `{summary['learned_wallets']}`",
            "",
            "Candidates are alert-only (never auto-copy):",
        ]
        lines.extend(
            f"• `{item['address'][:10]}…` {item['name']} — "
            f"{item['profitable_observations']} profitable observation(s)"
            for item in candidates
        )
        await self.send_message("\n".join(lines))

    async def cmd_stop(self, args):
        """Pause new entries; existing positions keep being managed."""
        if self.trading_engine:
            self.trading_engine.trading_paused = True
        await self.send_alert(
            "⏸️ TRADING PAUSED",
            "Bot is paused. No new trades will be made.\n"
            "Use /resume to continue.",
            "⏸️"
        )

    async def cmd_resume(self, args):
        """Resume new entries."""
        if self.trading_engine:
            self.trading_engine.trading_paused = False
        await self.send_alert(
            "▶️ TRADING RESUMED",
            "Bot is now active and will trade.",
            "▶️"
        )

    async def cmd_config(self, args):
        """Show config"""
        from config import config

        cfg_text = f"""
*⚙️ CONFIGURATION*

*Position Sizing:*
Allocation per meme: `{config.TRADING.allocation_per_meme_pct}% of startup SOL`
Max per coin: `{config.TRADING.max_position_per_coin} SOL`
Max portfolio allocation: `{config.TRADING.max_portfolio_allocation_pct}%`
SOL reserve: `{config.TRADING.min_sol_reserve} SOL`
Max coins: `{config.TRADING.max_coins_tracked}`

*DCA Settings:*
Entries: `{config.TRADING.dca_entries}`
Spacing: `{config.TRADING.dca_spacing_pct}%`

*Grid Selling:*
Levels: `{config.TRADING.grid_levels}`
First TP: `{config.TRADING.first_tp_pct}%`
Moon Bag: `{config.TRADING.moon_bag_pct}%`

*Risk Management:*
Stop Loss: `{config.TRADING.stop_loss_pct}%`
Trailing Stop: `{config.TRADING.trailing_stop_pct}%`
"""
        await self.send_message(cfg_text)

    async def cmd_whales(self, args):
        """Show tracked whales"""
        from whale_data import whale_fetcher

        wallets = whale_fetcher.whale_wallets
        gmgn = [w for w in wallets.values() if w.source == "gmgn"]
        manual = [w for w in wallets.values() if w.source == "manual"]

        text = f"""
*🐋 TRACKED WHALES*

GMGN Top Traders: {len(gmgn)}
Manual: {len(manual)}
Total: {len(wallets)}
"""
        await self.send_message(text)

    # ====================
    # ALERTS
    # ====================

    async def alert_new_signal(self, signal: Dict):
        """Alert on new token signal"""
        await self.send_alert(
            f"🔍 NEW SIGNAL: {signal['name']}",
            f"Score: `{signal['overall_score']:.1f}/100`\n"
            f"Dev Buy: `{signal['dev_buy_sol']:.2f} SOL`\n"
            f"Market Cap: `${signal['market_cap_sol'] * 200:,.0f}`\n"
            f"Buy Ratio: `{signal['buy_ratio']:.0%}`"
        )

    async def alert_whale_buy(self, wallet: str, symbol: str, amount: float):
        """Alert on whale buy"""
        await self.send_alert(
            f"🐋 WHALE BUY: {symbol}",
            f"Wallet: `{wallet[:12]}...`\n"
            f"Amount: `{amount:.2f} SOL`",
            "🐋"
        )

    async def alert_position_update(self, position: Position):
        """Alert on position change"""
        emoji = "🟢" if position.unrealized_pnl_pct >= 0 else "🔴"
        await self.send_alert(
            f"{emoji} {position.token_symbol} Update",
            f"PnL: `{position.unrealized_pnl_pct:+.1f}%`\n"
            f"Grid: {position.grid_sold_pct:.0f}% sold"
        )

    async def alert_profit_taken(self, symbol: str, amount: float, pnl_pct: float):
        """Alert on profit"""
        await self.send_alert(
            f"🎯 PROFIT: {symbol}",
            f"Amount: `{amount:.4f} SOL`\n"
            f"Return: `{pnl_pct:+.1f}%`",
            "💰"
        )

    async def alert_stop_loss(self, symbol: str, loss: float):
        """Alert on stop loss"""
        await self.send_alert(
            f"📉 STOP LOSS: {symbol}",
            f"Loss: `{loss:.4f} SOL`",
            "🔴"
        )


# Singleton
telegram_bot: Optional[TelegramBot] = None


def init_telegram(token: str, chat_id: str, trading_engine=None) -> TelegramBot:
    """Initialize Telegram bot"""
    global telegram_bot
    telegram_bot = TelegramBot(token, chat_id, trading_engine)
    return telegram_bot
