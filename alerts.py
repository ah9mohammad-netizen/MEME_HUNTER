"""
MEME HUNTER - Telegram Alerts
=============================
Telegram notification system for the trading bot.
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass
import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class AlertMessage:
    """Alert message structure"""
    type: str  # 'trade', 'signal', 'position', 'error', 'status'
    title: str
    message: str
    data: Dict = None

    def to_telegram_format(self) -> str:
        """Format message for Telegram"""
        emoji = {
            'trade': '💰',
            'signal': '🔍',
            'position': '📊',
            'error': '⚠️',
            'status': '📈',
            'whale': '🐋',
            'profit': '🎯',
            'loss': '📉'
        }.get(self.type, '📌')

        return f"{emoji} *{self.title}*\n\n{self.message}"


class TelegramAlerts:
    """
    Telegram alerts handler
    """

    def __init__(self, bot_token: str = None, chat_id: str = None):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = bool(bot_token and chat_id)

        if not self.enabled:
            logger.warning("Telegram alerts disabled - missing bot token or chat ID")

    async def send(self, alert: AlertMessage):
        """Send alert to Telegram"""
        if not self.enabled:
            return

        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

            payload = {
                "chat_id": self.chat_id,
                "text": alert.to_telegram_format(),
                "parse_mode": "Markdown",
                "disable_web_page_preview": True
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=10) as resp:
                    if resp.status != 200:
                        error = await resp.text()
                        logger.error(f"Failed to send Telegram alert: {error}")
                    else:
                        logger.debug(f"Telegram alert sent: {alert.title}")

        except Exception as e:
            logger.error(f"Telegram send error: {e}")

    async def send_trade_alert(self, trade_type: str, symbol: str, amount: float,
                               price: float, pnl: float = None):
        """Send trade notification"""
        trade_type_emoji = "🟢 BUY" if trade_type == "buy" else "🔴 SELL"

        message = f"""
{trade_type_emoji} *{symbol}*

💵 Amount: `{amount:.4f} SOL`
💰 Price: `{price:.8f} SOL`
"""
        if pnl is not None:
            pnl_emoji = "🟢" if pnl >= 0 else "🔴"
            message += f"{pnl_emoji} PnL: `{pnl:+.4f} SOL`"

        alert = AlertMessage(
            type='trade',
            title=f"{trade_type.upper()} {symbol}",
            message=message
        )

        await self.send(alert)

    async def send_signal_alert(self, signal: Dict):
        """Send new trading signal notification"""
        message = f"""
📊 *Token Analysis*

🏷️ {signal.get('name', 'Unknown')} ({signal.get('symbol', '?')})
🪙 Dev Buy: `{signal.get('dev_buy_sol', 0):.2f} SOL`
📈 Market Cap: `${signal.get('market_cap_sol', 0) * 200:,.2f}`
📊 Score: `{signal.get('overall_score', 0):.1f}/100`
"""

        if signal.get('is_whale_alert'):
            message += "\n🐋 *WHALE ALERT*"

        alert = AlertMessage(
            type='signal',
            title=f"🔍 New Signal: {signal.get('name', 'Unknown')}",
            message=message
        )

        await self.send(alert)

    async def send_position_alert(self, position: Dict):
        """Send position update"""
        pnl_pct = position.get('unrealized_pnl_pct', 0)
        emoji = "🟢" if pnl_pct >= 0 else "🔴"

        message = f"""
📊 *Position Update*

🏷️ {position.get('symbol', 'Unknown')}
💵 Entry: `{position.get('entry_price', 0):.8f} SOL`
📈 Current: `{position.get('current_price', 0):.8f} SOL`
{emoji} PnL: `{pnl_pct:+.1f}%`
📦 Grid Sold: `{position.get('grid_sold_pct', 0):.0f}%`
"""

        alert = AlertMessage(
            type='position',
            title=f"📊 {position.get('symbol')} Update",
            message=message
        )

        await self.send(alert)

    async def send_profit_alert(self, symbol: str, profit_sol: float,
                               profit_pct: float, reason: str = "Grid level hit"):
        """Send profit notification"""
        message = f"""
🎉 *PROFIT TAKEN*

🏷️ {symbol}
💰 Profit: `{profit_sol:.4f} SOL`
📈 Return: `{profit_pct:+.1f}%`
📋 Reason: {reason}
🕐 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

        alert = AlertMessage(
            type='profit',
            title=f"🎯 {symbol} +{profit_pct:.0f}%",
            message=message
        )

        await self.send(alert)

    async def send_loss_alert(self, symbol: str, loss_sol: float,
                             reason: str = "Stop loss"):
        """Send loss notification"""
        message = f"""
📉 *LOSS*

🏷️ {symbol}
💸 Loss: `{loss_sol:.4f} SOL`
📋 Reason: {reason}
🕐 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

        alert = AlertMessage(
            type='loss',
            title=f"📉 {symbol} Stopped Out",
            message=message
        )

        await self.send(alert)

    async def send_whale_alert(self, wallet_name: str, mint: str,
                               symbol: str, amount_sol: float, token_symbol: str):
        """Send whale activity alert"""
        message = f"""
🐋 *WHALE ACTIVITY*

👤 Wallet: `{wallet_name}`
🏷️ Token: {symbol}
💵 Amount: `{amount_sol:.2f} SOL`
📝 Mint: `{mint[:16]}...`
🕐 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

        alert = AlertMessage(
            type='whale',
            title=f"🐋 {wallet_name} → {symbol}",
            message=message
        )

        await self.send(alert)

    async def send_error_alert(self, error: str, context: str = ""):
        """Send error notification"""
        message = f"""
⚠️ *ERROR*

❌ {error}
📋 Context: {context}
🕐 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

        alert = AlertMessage(
            type='error',
            title="⚠️ Bot Error",
            message=message
        )

        await self.send(alert)

    async def send_status_alert(self, portfolio_value: float,
                               daily_pnl: float, positions: int):
        """Send periodic status update"""
        pnl_emoji = "🟢" if daily_pnl >= 0 else "🔴"

        message = f"""
📊 *BOT STATUS*

💰 Portfolio: `{portfolio_value:.4f} SOL`
{pnl_emoji} Daily PnL: `{daily_pnl:+.4f} SOL`
📦 Active Positions: {positions}
🕐 Updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}
"""

        alert = AlertMessage(
            type='status',
            title="📊 Bot Status",
            message=message
        )

        await self.send(alert)

    async def send_moon_bag_alert(self, symbol: str, entry_price: float,
                                 current_price: float, multiplier: float):
        """Send moon bag update"""
        message = f"""
🌙 *MOON BAG UPDATE*

🏷️ {symbol}
💵 Entry: `{entry_price:.8f} SOL`
📈 Current: `{current_price:.8f} SOL`
🚀 Multiplier: `{multiplier:.1f}x`
🕐 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

        alert = AlertMessage(
            type='signal',
            title=f"🌙 {symbol} @ {multiplier:.1f}x",
            message=message
        )

        await self.send(alert)


class AlertManager:
    """
    Manage alert throttling and batching
    """

    def __init__(self, telegram: TelegramAlerts = None):
        self.telegram = telegram
        self.last_alerts: Dict[str, datetime] = {}
        self.alert_cooldown = 60  # seconds between same-type alerts

        # Thresholds for alerts
        self.pnl_threshold_pct = 10.0  # Alert when PnL changes by 10%
        self.whale_threshold_sol = 5.0  # Alert for whale buys > 5 SOL

    async def throttled_send(self, alert_type: str, alert: AlertMessage):
        """Send alert with throttling"""
        now = datetime.now()

        if alert_type in self.last_alerts:
            time_since = (now - self.last_alerts[alert_type]).seconds
            if time_since < self.alert_cooldown:
                logger.debug(f"Alert {alert_type} throttled ({time_since}s since last)")
                return

        await self.telegram.send(alert)
        self.last_alerts[alert_type] = now

    async def alert_if_significant(self, position: Dict, previous_pnl: float):
        """Send alert if position PnL changed significantly"""
        current_pnl = position.get('unrealized_pnl_pct', 0)
        change = abs(current_pnl - previous_pnl)

        if change >= self.pnl_threshold_pct:
            await self.telegram.send_position_alert(position)

    async def alert_whale_if_big(self, amount_sol: float, **kwargs):
        """Alert if whale buy is significant"""
        if amount_sol >= self.whale_threshold_sol:
            await self.telegram.send_whale_alert(amount_sol=amount_sol, **kwargs)


# Discord webhook support (alternative to Telegram)
class DiscordAlerts:
    """
    Discord webhook alerts
    """

    def __init__(self, webhook_url: str = None):
        self.webhook_url = webhook_url
        self.enabled = bool(webhook_url)

    async def send(self, alert: AlertMessage):
        """Send alert to Discord webhook"""
        if not self.enabled:
            return

        try:
            # Map types to Discord colors
            colors = {
                'trade': 0x00FF00,      # Green
                'signal': 0xFFAA00,    # Orange
                'position': 0x00AAFF,   # Blue
                'error': 0xFF0000,      # Red
                'status': 0xAAAAAA,     # Gray
                'whale': 0xFF00FF,      # Magenta
                'profit': 0x00FF00,     # Green
                'loss': 0xFF0000        # Red
            }

            color = colors.get(alert.type, 0xFFFFFF)

            payload = {
                "embeds": [{
                    "title": alert.title,
                    "description": alert.message,
                    "color": color,
                    "timestamp": datetime.now().isoformat()
                }]
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(self.webhook_url, json=payload, timeout=10) as resp:
                    if resp.status not in [200, 204]:
                        logger.error(f"Discord webhook failed: {resp.status}")

        except Exception as e:
            logger.error(f"Discord send error: {e}")
