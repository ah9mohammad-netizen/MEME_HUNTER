"""
MEME HUNTER - Token Scanner & Discovery
=======================================
Real-time token discovery via multiple WebSocket sources.
"""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass
from datetime import datetime
import aiohttp
import websockets

from models import TokenSignal, MarketData, HolderData

logger = logging.getLogger(__name__)


@dataclass
class ScanFilters:
    """Filters for token discovery"""
    min_dev_buy_sol: float = 0.3
    max_market_cap_sol: float = 100.0
    min_liquidity_sol: float = 1.0
    min_unique_wallets: int = 5
    min_buy_ratio: float = 0.5
    require_socials: bool = False

    # Bonding curve filters (for Pump.fun)
    min_curve_progress: float = 0.0
    max_curve_progress: float = 100.0


class TokenSource(ABC):
    """Abstract base class for token sources"""

    @abstractmethod
    async def connect(self):
        """Establish connection"""
        pass

    @abstractmethod
    async def disconnect(self):
        """Close connection"""
        pass

    @abstractmethod
    async def subscribe(self, callback: Callable[[TokenSignal], None]):
        """Subscribe to token updates"""
        pass


class PumpPortalScanner(TokenSource):
    """
    Pump.fun WebSocket scanner
    Streams new token launches and trades in real-time
    """

    def __init__(self, filters: ScanFilters = None):
        self.ws_url = "wss://pumpportal.fun/api/data"
        self.filters = filters or ScanFilters()
        self.ws = None
        self.running = False
        self.callbacks: List[Callable] = []
        self._token_buffer: Dict[str, Dict] = {}  # Buffer for accumulating token data
        self._buffer_duration = 10  # seconds to accumulate data

    async def connect(self):
        """Connect to Pump.fun WebSocket"""
        try:
            self.ws = await websockets.connect(self.ws_url)
            self.running = True
            logger.info("Connected to Pump.fun WebSocket")
        except Exception as e:
            logger.error(f"Failed to connect to Pump.fun: {e}")
            raise

    async def disconnect(self):
        """Disconnect from Pump.fun"""
        self.running = False
        if self.ws:
            await self.ws.close()
            self.ws = None

    async def subscribe(self, callback: Callable[[TokenSignal], None]):
        """Subscribe to new tokens and trades"""
        self.callbacks.append(callback)

        # Subscribe to new token launches
        await self.ws.send(json.dumps({"method": "subscribeNewToken"}))
        logger.info("Subscribed to new token events")

        # Subscribe to trades for tokens we're interested in
        await self.ws.send(json.dumps({"method": "subscribeTrade"}))
        logger.info("Subscribed to trade events")

        await self._listen()

    async def _listen(self):
        """Main listening loop"""
        while self.running:
            try:
                message = await self.ws.recv()
                await self._process_message(message)
            except websockets.exceptions.ConnectionClosed:
                logger.warning("Pump.fun connection closed, reconnecting...")
                await asyncio.sleep(5)
                await self.connect()
                await self._resubscribe()
            except Exception as e:
                logger.error(f"Error processing message: {e}")
                await asyncio.sleep(1)

    async def _resubscribe(self):
        """Resubscribe after reconnect"""
        await self.ws.send(json.dumps({"method": "subscribeNewToken"}))
        await self.ws.send(json.dumps({"method": "subscribeTrade"}))

    async def _process_message(self, message: str):
        """Process incoming WebSocket message"""
        try:
            data = json.loads(message)
            tx_type = data.get("txType", "")

            if tx_type == "create":
                await self._handle_new_token(data)
            elif tx_type == "trade":
                await self._handle_trade(data)
            elif tx_type == "complete":
                await self._handle_graduation(data)

        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON message: {message[:100]}")
        except Exception as e:
            logger.error(f"Error processing message: {e}")

    async def _handle_new_token(self, data: Dict):
        """Handle new token creation"""
        mint = data.get("mint", "")
        if not mint:
            return

        # Initial token data. PumpPortal exposes the trader public key on
        # trade/create events; retaining it lets us measure real unique
        # traders instead of assuming every event is a new wallet.
        trader = data.get("traderPublicKey") or data.get("user") or ""
        is_buy = bool(data.get("isBuy", False))
        token_data = {
            "mint": mint,
            "name": data.get("name", "Unknown"),
            "symbol": data.get("symbol", "?"),
            "dev_buy_sol": float(data.get("solAmount", 0) or 0),
            "market_cap_sol": float(data.get("marketCapSol", 0) or 0),
            "total_trades": 1,
            "unique_wallets": 1 if trader else 0,
            "buy_trades": 1 if is_buy else 0,
            "sell_trades": 0 if is_buy else 1,
            "creator_address": trader,
            "traders": {trader} if trader else set(),
            "buy_traders": {trader} if is_buy and trader else set(),
            "sell_traders": {trader} if not is_buy and trader else set(),
            "trades": [{
                "trader": trader,
                "is_buy": is_buy,
                "sol_amount": float(data.get("solAmount", 0) or 0),
                "timestamp": datetime.now()
            }]
        }

        self._token_buffer[mint] = token_data

        # Schedule processing after buffer period
        asyncio.create_task(self._process_buffered_token(mint))

    async def _handle_trade(self, data: Dict):
        """Handle individual trade"""
        mint = data.get("mint", "")
        if not mint or mint not in self._token_buffer:
            return

        is_buy = bool(data.get("isBuy", False))
        trader = data.get("traderPublicKey") or data.get("user") or ""
        amount = float(data.get("solAmount", 0) or 0)
        trades = self._token_buffer[mint].get("trades", [])
        trades.append({
            "trader": trader,
            "is_buy": is_buy,
            "sol_amount": amount,
            "timestamp": datetime.now()
        })

        buffered = self._token_buffer[mint]
        buffered["total_trades"] += 1
        if trader:
            buffered.setdefault("traders", set()).add(trader)
            buffered["unique_wallets"] = len(buffered["traders"])
        if is_buy:
            buffered["buy_trades"] += 1
            if trader:
                buffered.setdefault("buy_traders", set()).add(trader)
        else:
            buffered["sell_trades"] += 1
            if trader:
                buffered.setdefault("sell_traders", set()).add(trader)
                if trader == buffered.get("creator_address"):
                    buffered["creator_sold"] = True

    async def _handle_graduation(self, data: Dict):
        """Handle token graduation to Raydium"""
        mint = data.get("mint", "")
        logger.info(f"Token graduated: {mint}")
        # Process any buffered data before graduation

    async def _process_buffered_token(self, mint: str):
        """Process buffered token data after accumulation period"""
        await asyncio.sleep(self._buffer_duration)

        if mint not in self._token_buffer:
            return

        data = self._token_buffer[mint]
        del self._token_buffer[mint]

        # Calculate metrics
        buy_ratio = data["buy_trades"] / max(data["total_trades"], 1)
        behavior = self._behavior_summary(data)

        # Apply filters
        if not self._passes_filters(data, buy_ratio):
            return

        # Create signal
        signal = TokenSignal(
            mint=mint,
            name=data["name"],
            symbol=data["symbol"],
            dev_buy_sol=data["dev_buy_sol"],
            market_cap_sol=data["market_cap_sol"],
            liquidity_sol=0,  # Will be filled by market data
            buy_ratio=buy_ratio,
            unique_wallets=data["unique_wallets"],
            total_trades=data["total_trades"],
            creator_address=data.get("creator_address"),
            behavior_data=behavior,
        )
        signal.calculate_overall_score()

        # Emit to callbacks
        for callback in self.callbacks:
            try:
                await callback(signal)
            except Exception as e:
                logger.error(f"Callback error: {e}")

    @staticmethod
    def _behavior_summary(data: Dict) -> Dict:
        """Produce conservative launch-behavior proxies from WS observations.

        These are not proof of manipulation. They are early warning features
        inspired by bundle/wash/mechanical-trading research and are persisted
        so they can be evaluated against later outcomes.
        """
        trades = data.get("trades", [])
        buys = [trade for trade in trades if trade.get("is_buy")]
        sells = [trade for trade in trades if not trade.get("is_buy")]
        buy_traders = data.get("buy_traders", set())
        sell_traders = data.get("sell_traders", set())
        overlap = buy_traders & sell_traders

        from collections import Counter
        amount_buckets = Counter(round(float(trade.get("sol_amount", 0) or 0), 4) for trade in trades)
        repeated_amount_ratio = (
            max(amount_buckets.values()) / len(trades) if len(trades) >= 4 and amount_buckets else 0.0
        )
        mechanicality_score = repeated_amount_ratio * 100.0
        wash_trading_score = (
            len(overlap) / max(1, min(len(buy_traders), len(sell_traders))) * 100.0
            if buy_traders and sell_traders else 0.0
        )

        # A concentration proxy: if a few addresses account for most observed
        # buys, keep it as a warning until bundle/funding data confirms it.
        buy_counts = Counter(trade.get("trader") for trade in buys if trade.get("trader"))
        top_three_buy_share = sum(count for _, count in buy_counts.most_common(3)) / max(1, len(buys))
        bundle_risk_score = top_three_buy_share * 100.0 if len(buys) >= 5 else 0.0
        creator_sold = bool(data.get("creator_sold", False))
        return {
            "observed_traders": len(data.get("traders", set())),
            "early_buyer_count": len(data.get("buy_traders", set())),
            "wash_trading_score": round(min(100.0, wash_trading_score), 2),
            "mechanicality_score": round(min(100.0, mechanicality_score), 2),
            "bundle_risk_score": round(min(100.0, bundle_risk_score), 2),
            "creator_sold": creator_sold,
            "data_quality": "observed" if data.get("traders") else "limited",
        }

    def _passes_filters(self, data: Dict, buy_ratio: float) -> bool:
        """Check if token passes configured filters"""
        if data["dev_buy_sol"] < self.filters.min_dev_buy_sol:
            return False
        if data["market_cap_sol"] > self.filters.max_market_cap_sol:
            return False
        if buy_ratio < self.filters.min_buy_ratio:
            return False
        if data["unique_wallets"] < self.filters.min_unique_wallets:
            return False
        return True


class DexScreenerScanner(TokenSource):
    """
    DexScreener API scanner
    Monitors trending and new pairs
    """

    def __init__(self, filters: ScanFilters = None):
        self.filters = filters or ScanFilters()
        self.api_base = "https://api.dexscreener.com"
        self.running = False
        self.callbacks: List[Callable] = []

    async def connect(self):
        """No WebSocket needed for DexScreener polling"""
        self.running = True
        logger.info("DexScreener scanner initialized")

    async def disconnect(self):
        self.running = False

    async def subscribe(self, callback: Callable[[TokenSignal], None]):
        self.callbacks.append(callback)

        while self.running:
            try:
                await self._scan_new_pairs()
                await asyncio.sleep(5)  # Poll every 5 seconds
            except Exception as e:
                logger.error(f"Scanner error: {e}")
                await asyncio.sleep(30)

    async def _scan_new_pairs(self):
        """Scan for new pairs"""
        async with aiohttp.ClientSession() as session:
            # Get recent pairs
            url = f"{self.api_base}/v1/pairs/solana?sort=createdAt&order=desc&limit=50"
            async with session.get(url) as resp:
                if resp.status != 200:
                    return

                data = await resp.json()
                pairs = data.get("pairs", [])

                for pair in pairs[:10]:  # Process top 10 newest
                    signal = self._parse_pair(pair)
                    if signal and self._passes_filters(signal):
                        for callback in self.callbacks:
                            await callback(signal)

    def _parse_pair(self, pair: Dict) -> Optional[TokenSignal]:
        """Parse DexScreener pair data"""
        try:
            chain = pair.get("chainId", "")
            if chain != "solana":
                return None

            token = pair.get("baseToken", {})
            quote = pair.get("quoteToken", {})

            if quote.get("symbol", "") != "SOL":
                return None

            market_data = pair.get("marketCap", 0)
            liquidity = float(pair.get("liquidity", {}).get("usd", 0)) / 200  # Rough SOL estimate

            # Volume data
            h1_volume = float(pair.get("volume", {}).get("h1", 0)) or 0

            # Buy/Sell counts
            buys = pair.get("txns", {}).get("buys", 0) or 0
            sells = pair.get("txns", {}).get("sells", 0) or 0
            total_trades = buys + sells

            signal = TokenSignal(
                mint=token.get("address", ""),
                name=token.get("name", "Unknown"),
                symbol=token.get("symbol", "?"),
                dev_buy_sol=0,  # Unknown from DexScreener
                market_cap_sol=market_data / 200 if market_data else 0,
                liquidity_sol=liquidity,
                buy_ratio=buys / max(total_trades, 1),
                unique_wallets=total_trades,
                total_trades=total_trades
            )
            signal.calculate_overall_score()

            return signal

        except Exception as e:
            logger.error(f"Error parsing pair: {e}")
            return None

    def _passes_filters(self, signal: TokenSignal) -> bool:
        """Check if signal passes filters"""
        if signal.liquidity_sol < self.filters.min_liquidity_sol:
            return False
        if signal.market_cap_sol > self.filters.max_market_cap_sol:
            return False
        return True


class SolanaTrackerScanner(TokenSource):
    """
    SolanaTracker.io WebSocket scanner
    Comprehensive token tracking
    """

    def __init__(self, api_key: str, filters: ScanFilters = None):
        self.api_key = api_key
        self.filters = filters or ScanFilters()
        self.ws_url = f"wss://datastream.solanatracker.io/{api_key}"
        self.ws = None
        self.running = False
        self.callbacks: List[Callable] = []

    async def connect(self):
        try:
            self.ws = await websockets.connect(self.ws_url)
            self.running = True
            logger.info("Connected to SolanaTracker")
        except Exception as e:
            logger.error(f"Failed to connect to SolanaTracker: {e}")
            raise

    async def disconnect(self):
        self.running = False
        if self.ws:
            await self.ws.close()

    async def subscribe(self, callback: Callable[[TokenSignal], None]):
        self.callbacks.append(callback)

        # Subscribe to various rooms
        rooms = [
            {"type": "join", "room": "latest"},
            {"type": "join", "room": "pumpfun:curve:30"},
            {"type": "join", "room": "pumpfun:curve:50"},
            {"type": "join", "room": "graduated"}
        ]

        for room in rooms:
            await self.ws.send(json.dumps(room))

        await self._listen()

    async def _listen(self):
        while self.running:
            try:
                message = await self.ws.recv()
                await self._process_message(message)
            except Exception as e:
                logger.error(f"Tracker error: {e}")
                await asyncio.sleep(5)

    async def _process_message(self, message: str):
        try:
            data = json.loads(message)
            if data.get("type") != "message":
                return

            token = data.get("data", {}).get("token", {})
            pools = data.get("data", {}).get("pools", [{}])[0]

            signal = TokenSignal(
                mint=token.get("address", ""),
                name=token.get("name", "Unknown"),
                symbol=token.get("symbol", "?"),
                dev_buy_sol=0,
                market_cap_sol=pools.get("marketCap", {}).get("sol", 0),
                liquidity_sol=pools.get("liquidity", {}).get("sol", 0),
                buy_ratio=0.6,  # Default
                unique_wallets=token.get("holder", 0) or 0,
                total_trades=pools.get(" trades", 0) or 0
            )
            signal.calculate_overall_score()

            for callback in self.callbacks:
                await callback(signal)

        except Exception as e:
            logger.error(f"Error processing tracker message: {e}")


class TokenScanner:
    """
    Unified token scanner that combines multiple sources
    """

    def __init__(self, filters: ScanFilters = None):
        self.filters = filters or ScanFilters()
        self.scanners: List[TokenSource] = []
        self.active_signals: Dict[str, TokenSignal] = {}
        self.on_signal: Optional[Callable] = None

    def add_scanner(self, scanner: TokenSource):
        """Add a token source scanner"""
        self.scanners.append(scanner)

    async def start(self, on_signal: Callable[[TokenSignal], None]):
        """Start all scanners"""
        self.on_signal = on_signal

        for scanner in self.scanners:
            try:
                await scanner.connect()
                await scanner.subscribe(self._handle_signal)
            except Exception as e:
                logger.error(f"Scanner failed to start: {e}")

        # Keep running
        while True:
            await asyncio.sleep(1)

    async def _handle_signal(self, signal: TokenSignal):
        """Handle incoming signal"""
        # Deduplicate
        if signal.mint in self.active_signals:
            # Update existing signal
            existing = self.active_signals[signal.mint]
            signal.overall_score = max(existing.overall_score, signal.calculate_overall_score())

        self.active_signals[signal.mint] = signal

        # Emit to handler
        if self.on_signal:
            await self.on_signal(signal)

    async def stop(self):
        """Stop all scanners"""
        for scanner in self.scanners:
            await scanner.disconnect()
