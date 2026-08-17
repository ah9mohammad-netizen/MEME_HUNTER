"""
MEME HUNTER - Token Scanner & Discovery - Selectivity Focused Rewrite
======================================================================
Real-time token discovery with improved MELT-inspired bundle detection.

Key improvements over previous version:
- PumpPortalScanner now uses bundle_detector for sniper saturation, time clustering, sale duration
- New PumpFunAPIScanner polls pump.fun directly every 2-3s (10x faster than Gecko 30s)
- New LogsSubscribeScanner uses Solana logsSubscribe at processed commitment for near-real-time detection
- DexScreenerScanner (Gecko new_pools) kept but configurable interval
- All scanners emit TokenSignal with enhanced behavior_data
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

# New: try bundle_detector, fallback to old logic if not available
try:
    from bundle_detector import analyze_launch_behavior as enhanced_behavior
    HAS_ENHANCED = True
except ImportError:
    HAS_ENHANCED = False

PUMPFUN_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
RAYDIUM_PROGRAM_ID = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"


@dataclass
class ScanFilters:
    """Filters for token discovery - tightened for selectivity"""
    min_dev_buy_sol: float = 0.3
    min_market_cap_sol: float = 0.0
    max_market_cap_sol: float = 100.0
    min_liquidity_sol: float = 1.0
    min_unique_wallets: int = 5
    min_buy_ratio: float = 0.5
    require_socials: bool = False

    min_curve_progress: float = 0.0
    max_curve_progress: float = 100.0


class TokenSource(ABC):
    @abstractmethod
    async def connect(self): pass
    @abstractmethod
    async def disconnect(self): pass
    @abstractmethod
    async def subscribe(self, callback: Callable[[TokenSignal], None]): pass


class PumpPortalScanner(TokenSource):
    """
    Pump.fun WebSocket via pumpportal.fun - still primary for selectivity.
    Buffer 10s to collect trades, then run enhanced bundle analysis.
    """

    def __init__(self, filters: ScanFilters = None, buffer_duration: int = 10):
        self.ws_url = "wss://pumpportal.fun/api/data"
        self.filters = filters or ScanFilters()
        self.ws = None
        self.running = False
        self.callbacks: List[Callable] = []
        self._token_buffer: Dict[str, Dict] = {}
        self._buffer_duration = buffer_duration

    async def connect(self):
        try:
            self.ws = await websockets.connect(self.ws_url)
            self.running = True
            logger.info("Connected to Pump.fun WebSocket (PumpPortal)")
        except Exception as e:
            logger.error(f"Failed to connect to Pump.fun: {e}")
            raise

    async def disconnect(self):
        self.running = False
        if self.ws:
            await self.ws.close()
            self.ws = None

    async def subscribe(self, callback: Callable[[TokenSignal], None]):
        self.callbacks.append(callback)
        await self.ws.send(json.dumps({"method": "subscribeNewToken"}))
        logger.info("Subscribed to new token events")
        await self.ws.send(json.dumps({"method": "subscribeTrade"}))
        logger.info("Subscribed to trade events")
        await self._listen()

    async def _listen(self):
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
        await self.ws.send(json.dumps({"method": "subscribeNewToken"}))
        await self.ws.send(json.dumps({"method": "subscribeTrade"}))

    async def _process_message(self, message: str):
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
            logger.warning(f"Invalid JSON: {message[:100]}")
        except Exception as e:
            logger.error(f"Error processing msg: {e}")

    async def _handle_new_token(self, data: Dict):
        mint = data.get("mint", "")
        if not mint:
            return
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
        asyncio.create_task(self._process_buffered_token(mint))

    async def _handle_trade(self, data: Dict):
        mint = data.get("mint", "")
        if not mint or mint not in self._token_buffer:
            return
        is_buy = bool(data.get("isBuy", False))
        trader = data.get("traderPublicKey") or data.get("user") or ""
        amount = float(data.get("solAmount", 0) or 0)
        trades = self._token_buffer[mint].get("trades", [])
        trades.append({"trader": trader, "is_buy": is_buy, "sol_amount": amount, "timestamp": datetime.now()})
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
        mint = data.get("mint", "")
        logger.info(f"Token graduated: {mint}")

    async def _process_buffered_token(self, mint: str):
        await asyncio.sleep(self._buffer_duration)
        if mint not in self._token_buffer:
            return
        data = self._token_buffer.pop(mint, None)
        if not data:
            return
        buy_ratio = data["buy_trades"] / max(data["total_trades"], 1)
        behavior = self._behavior_summary(data)
        if not self._passes_filters(data, buy_ratio):
            return
        # Estimate bonding curve liquidity for pump.fun
        est_liquidity = max(data["dev_buy_sol"] * 1.5, data["market_cap_sol"] * 0.2, 3.0)

        signal = TokenSignal(
            mint=mint,
            name=data["name"],
            symbol=data["symbol"],
            dev_buy_sol=data["dev_buy_sol"],
            market_cap_sol=data["market_cap_sol"],
            liquidity_sol=est_liquidity,
            buy_ratio=buy_ratio,
            unique_wallets=data["unique_wallets"],
            total_trades=data["total_trades"],
            creator_address=data.get("creator_address"),
            behavior_data=behavior,
            source="pumpfun_new",
        )
        signal.calculate_overall_score()
        for cb in self.callbacks:
            try:
                await cb(signal)
            except Exception as e:
                logger.error(f"Callback error: {e}")

    @staticmethod
    def _behavior_summary(data: Dict) -> Dict:
        if HAS_ENHANCED:
            try:
                analysis = enhanced_behavior(data)
                # Preserve old keys for compat
                analysis["creator_sold"] = bool(data.get("creator_sold", False))
                analysis["data_quality"] = "observed" if data.get("traders") else "limited"
                analysis["dev_buy_known"] = True
                return analysis
            except Exception as e:
                logger.debug(f"Enhanced behavior failed, fallback: {e}")

        # Fallback old logic
        trades = data.get("trades", [])
        buys = [t for t in trades if t.get("is_buy")]
        buy_traders = data.get("buy_traders", set())
        sell_traders = data.get("sell_traders", set())
        overlap = buy_traders & sell_traders
        from collections import Counter
        amount_buckets = Counter(round(float(t.get("sol_amount", 0) or 0), 4) for t in trades)
        repeated_ratio = max(amount_buckets.values()) / len(trades) if len(trades) >=4 and amount_buckets else 0.0
        wash = len(overlap) / max(1, min(len(buy_traders), len(sell_traders))) * 100 if buy_traders and sell_traders else 0.0
        buy_counts = Counter(t.get("trader") for t in buys if t.get("trader"))
        top3_share = sum(c for _, c in buy_counts.most_common(3)) / max(1, len(buys)) * 100 if len(buys)>=5 else 0.0
        return {
            "observed_traders": len(data.get("traders", set())),
            "early_buyer_count": len(buy_traders),
            "wash_trading_score": round(min(100.0, wash),2),
            "mechanicality_score": round(min(100.0, repeated_ratio*100),2),
            "bundle_risk_score": round(min(100.0, top3_share),2),
            "creator_sold": bool(data.get("creator_sold", False)),
            "data_quality": "observed" if data.get("traders") else "limited",
            "dev_buy_known": True,
        }

    def _passes_filters(self, data: Dict, buy_ratio: float) -> bool:
        if data["dev_buy_sol"] < self.filters.min_dev_buy_sol:
            return False
        if data["market_cap_sol"] < self.filters.min_market_cap_sol:
            return False
        if data["market_cap_sol"] > self.filters.max_market_cap_sol:
            return False
        if buy_ratio < self.filters.min_buy_ratio:
            return False
        if data["unique_wallets"] < self.filters.min_unique_wallets:
            return False
        return True


class PumpFunAPIScanner(TokenSource):
    """
    NEW: Direct Pump.fun API polling every 2-3 seconds.
    Much faster than Gecko 30s, still selectivity-focused (not 0-slot but 10x better).
    Uses https://api.pump.fun/coins?limit=20&sort=createdAt
    """

    def __init__(self, filters: ScanFilters = None, poll_interval: int = 3):
        self.filters = filters or ScanFilters()
        self.poll_interval_seconds = poll_interval
        self.retry_after_seconds = 10
        self.running = False
        self.callbacks: List[Callable] = []
        self._seen_mints: set = set()
        self.api_base = "https://api.pump.fun"

    async def connect(self):
        self.running = True
        logger.info(f"Pump.fun API scanner initialized (poll {self.poll_interval_seconds}s)")

    async def disconnect(self):
        self.running = False

    async def subscribe(self, callback: Callable[[TokenSignal], None]):
        self.callbacks.append(callback)
        while self.running:
            try:
                await self._scan_new_coins()
                await asyncio.sleep(self.poll_interval_seconds)
            except Exception as e:
                logger.error(f"Pump.fun API scanner error: {e}")
                await asyncio.sleep(self.retry_after_seconds)

    async def _scan_new_coins(self):
        async with aiohttp.ClientSession() as session:
            # Try multiple endpoints for resilience
            endpoints = [
                f"{self.api_base}/coins?limit=25&sort=createdAt&order=desc",
                f"{self.api_base}/coins?limit=25&sort=lastTradeTimestamp&order=desc",
            ]
            for url in endpoints:
                try:
                    async with session.get(url, timeout=10) as resp:
                        if resp.status != 200:
                            continue
                        data = await resp.json()
                        coins = data if isinstance(data, list) else data.get("coins", []) if isinstance(data, dict) else []
                        for coin in coins[:20]:
                            signal = self._parse_pump_coin(coin)
                            if signal and signal.mint not in self._seen_mints:
                                self._seen_mints.add(signal.mint)
                                if self._passes_filters(signal):
                                    for cb in self.callbacks:
                                        await cb(signal)
                        return
                except Exception as e:
                    logger.debug(f"Pump.fun endpoint {url} failed: {e}")
                    continue

    def _parse_pump_coin(self, coin: Dict) -> Optional[TokenSignal]:
        try:
            mint = coin.get("mint") or coin.get("id") or coin.get("address") or ""
            if not mint:
                return None
            name = coin.get("name", "Unknown")
            symbol = coin.get("symbol", "?")
            # Market cap and liquidity: pump.fun API gives usd_market_cap, etc.
            market_cap_usd = float(coin.get("usd_market_cap") or coin.get("market_cap") or coin.get("marketCap") or 0)
            # dev buy often in initialBuy or creator field
            dev_buy = 0.0
            creator = coin.get("creator") or coin.get("creatorAddress") or ""
            # Some versions have virtualSolReserves
            v_sol = float(coin.get("virtualSolReserves") or coin.get("virtual_sol_reserves") or 0) / 1e9
            # Approx mcap sol = usd/150
            mcap_sol = market_cap_usd / 150.0 if market_cap_usd else (float(coin.get("marketCapSol") or 0))
            # Liquidity approx
            liquidity_sol = v_sol if v_sol else 0.0

            # Trading activity
            # Pump.fun API doesn't give buy_ratio directly, estimate from transactions?
            # Use holder count as proxy for unique wallets
            holder_count = int(coin.get("holderCount") or coin.get("holders") or coin.get("numHolders") or 0)
            # If we have no holder count, use 5 as placeholder but behavior will penalize
            unique = holder_count if holder_count else 5

            # Conservative buy ratio estimate - will be refined by risk gate
            buy_ratio = 0.65

            # Behavior data from coin itself
            behavior = {
                "data_quality": "pump_api",
                "dev_buy_known": False,
                "is_pump_fun": True,
                "creator_address": creator,
                "created_timestamp": coin.get("createdAt") or coin.get("created_timestamp"),
                "market_cap_usd": market_cap_usd,
                "virtual_sol_reserves": v_sol,
                "reply_count": coin.get("replyCount", 0),
                "is_nsfw": coin.get("isNsfw", False),
            }

            signal = TokenSignal(
                mint=mint,
                name=name,
                symbol=symbol,
                dev_buy_sol=float(coin.get("initialBuySol") or dev_buy),
                market_cap_sol=mcap_sol,
                liquidity_sol=liquidity_sol,
                buy_ratio=buy_ratio,
                unique_wallets=unique,
                total_trades=max(5, holder_count),  # rough
                creator_address=creator,
                behavior_data=behavior,
                source="pumpfun_api",
            )
            signal.calculate_overall_score()
            return signal
        except Exception as e:
            logger.debug(f"Parse pump coin failed: {e}")
            return None

    def _passes_filters(self, signal: TokenSignal) -> bool:
        if signal.market_cap_sol < self.filters.min_market_cap_sol:
            return False
        if signal.market_cap_sol > self.filters.max_market_cap_sol:
            return False
        if signal.liquidity_sol and signal.liquidity_sol < self.filters.min_liquidity_sol:
            return False
        # For pump API, relax unique wallets slightly because data incomplete
        if signal.unique_wallets < max(1, self.filters.min_unique_wallets - 5):
            return False
        return True


class LogsSubscribeScanner(TokenSource):
    """
    NEW: Solana logsSubscribe at processed commitment - near real-time detection.

    Subscribes to Pump.fun program logs: mentions [PUMPFUN_PROGRAM_ID]
    When log contains "Create" instruction, fetches transaction to extract mint.

    Requires:
    - RPC WebSocket URL (wss://...): HELIUS_WSS_URL or RPC_ENDPOINT wss variant
    - RPC HTTP URL for getTransaction: RPC_ENDPOINT or Helius HTTP

    Even without perfect parsing, detecting new token in <1-2 sec is 15x faster than 30s poll.
    """

    def __init__(self, filters: ScanFilters = None, ws_url: str = "", http_url: str = ""):
        self.filters = filters or ScanFilters()
        self.ws_url = ws_url  # e.g., wss://atlas-mainnet.helius-rpc.com/?api-key=...
        self.http_url = http_url or "https://api.mainnet-beta.solana.com"
        self.ws = None
        self.running = False
        self.callbacks: List[Callable] = []
        self._seen_sigs: set = set()

    async def connect(self):
        if not self.ws_url:
            logger.warning("LogsSubscribeScanner disabled: no WS URL provided")
            self.running = False
            return
        try:
            self.ws = await websockets.connect(self.ws_url)
            self.running = True
            # Send logsSubscribe request
            sub_req = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "logsSubscribe",
                "params": [
                    {"mentions": [PUMPFUN_PROGRAM_ID]},
                    {"commitment": "processed"}
                ]
            }
            await self.ws.send(json.dumps(sub_req))
            logger.info(f"LogsSubscribe connected to {self.ws_url[:40]}... for {PUMPFUN_PROGRAM_ID[:10]}")
        except Exception as e:
            logger.error(f"LogsSubscribe connect failed: {e}")
            self.running = False
            raise

    async def disconnect(self):
        self.running = False
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None

    async def subscribe(self, callback: Callable[[TokenSignal], None]):
        if not self.running:
            return
        self.callbacks.append(callback)
        await self._listen()

    async def _listen(self):
        while self.running:
            try:
                msg = await self.ws.recv()
                await self._process_ws_message(msg)
            except websockets.exceptions.ConnectionClosed:
                logger.warning("LogsSubscribe WS closed, reconnecting in 5s...")
                await asyncio.sleep(5)
                try:
                    await self.connect()
                except Exception:
                    await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"LogsSubscribe error: {e}")
                await asyncio.sleep(1)

    async def _process_ws_message(self, message: str):
        try:
            data = json.loads(message)
            # Initial subscription confirmation
            if "result" in data and isinstance(data["result"], int):
                logger.info(f"LogsSubscribe subscribed id {data['result']}")
                return
            # Notification
            params = data.get("params", {})
            result = params.get("result", {})
            value = result.get("value", {})
            logs = value.get("logs", []) or []
            sig = value.get("signature", "")
            if not sig or sig in self._seen_sigs:
                return
            # Quick filter: must contain Create instruction log
            log_str = " ".join(logs).lower()
            if "create" not in log_str and "initialize" not in log_str and "program log" not in log_str:
                # Still process some to catch new mints, but filter heavily
                if "program data" not in log_str:
                    return
            self._seen_sigs.add(sig)
            # Keep set bounded
            if len(self._seen_sigs) > 5000:
                self._seen_sigs = set(list(self._seen_sigs)[-2500:])

            # Fetch transaction to extract mint
            mint = await self._fetch_mint_from_tx(sig)
            if not mint:
                return
            # Create signal with minimal data, will be enriched later by risk gate
            signal = TokenSignal(
                mint=mint,
                name="Unknown (logs)",
                symbol="UNKNOWN",
                dev_buy_sol=0.5,  # assume standard, will be checked by risk
                market_cap_sol=1.0,
                liquidity_sol=5.0,
                buy_ratio=0.7,
                unique_wallets=5,
                total_trades=5,
                behavior_data={
                    "data_quality": "logs_subscribe",
                    "detection_method": "logs_processed",
                    "signature": sig,
                    "dev_buy_known": False,
                    "is_logs_detected": True,
                    "logs_snippet": logs[:3]
                },
                source="logs_subscribe",
            )
            signal.calculate_overall_score()
            for cb in self.callbacks:
                try:
                    await cb(signal)
                except Exception as e:
                    logger.error(f"LogsSubscribe callback error: {e}")

        except json.JSONDecodeError:
            pass
        except Exception as e:
            logger.debug(f"LogsSubscribe process msg error: {e}")

    async def _fetch_mint_from_tx(self, signature: str) -> Optional[str]:
        """Fetch transaction and try to extract mint address"""
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [signature, {"encoding": "jsonParsed", "commitment": "processed", "maxSupportedTransactionVersion": 0}]
                }
                async with session.post(self.http_url, json=payload, timeout=8) as resp:
                    if resp.status != 200:
                        return None
                    js = await resp.json()
                    result = js.get("result")
                    if not result:
                        return None
                    # Try to find mint in accountKeys or postTokenBalances
                    # Method 1: postTokenBalances - first new mint with amount
                    meta = result.get("meta", {})
                    post_balances = meta.get("postTokenBalances", []) or []
                    pre_balances = meta.get("preTokenBalances", []) or []
                    pre_mints = {b.get("mint") for b in pre_balances}
                    for pb in post_balances:
                        mint = pb.get("mint")
                        if mint and mint not in pre_mints:
                            # Likely new mint
                            return mint
                    # Method 2: look at transaction message accountKeys for new account
                    transaction = result.get("transaction", {})
                    message = transaction.get("message", {})
                    account_keys = message.get("accountKeys", []) or []
                    # Heuristic: accountKeys[0] is often mint for pump.fun create
                    # Pump.fun create instruction accounts: [mint, mint_authority, bondingCurve, ...]
                    if account_keys:
                        # Return first that looks like token mint (not program id)
                        for ak in account_keys[:5]:
                            pk = ak.get("pubkey") if isinstance(ak, dict) else ak
                            if pk and pk != PUMPFUN_PROGRAM_ID and len(pk) > 30:
                                # Quick check not system program etc.
                                if pk not in ["11111111111111111111111111111111", "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"]:
                                    return pk
        except Exception as e:
            logger.debug(f"Fetch mint from tx {signature[:10]} failed: {e}")
        return None


class DexScreenerScanner(TokenSource):
    """GeckoTerminal new pools - kept for migration detection, now configurable"""

    def __init__(self, filters: ScanFilters = None, poll_interval: int = 30):
        self.filters = filters or ScanFilters()
        self.api_base = "https://api.geckoterminal.com/api/v2"
        self.running = False
        self.callbacks: List[Callable] = []
        self.poll_interval_seconds = poll_interval
        self.retry_after_seconds = 60

    async def connect(self):
        self.running = True
        logger.info(f"Gecko scanner initialized (poll {self.poll_interval_seconds}s)")

    async def disconnect(self):
        self.running = False

    async def subscribe(self, callback: Callable[[TokenSignal], None]):
        self.callbacks.append(callback)
        while self.running:
            try:
                status = await self._scan_new_pairs()
                if status == 429:
                    logger.warning(f"New-pools rate-limited; backoff {self.retry_after_seconds}s")
                    await asyncio.sleep(self.retry_after_seconds)
                else:
                    await asyncio.sleep(self.poll_interval_seconds)
            except Exception as e:
                logger.error(f"Scanner error: {e}")
                await asyncio.sleep(self.retry_after_seconds)

    async def _scan_new_pairs(self):
        async with aiohttp.ClientSession() as session:
            url = f"{self.api_base}/networks/solana/new_pools?page=1"
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    logger.warning("New-pools feed HTTP %s", resp.status)
                    return resp.status
                payload = await resp.json()
                pools = payload.get("data", [])
                for pool in pools[:20]:
                    signal = self._parse_gecko_pool(pool)
                    if signal and self._passes_filters(signal):
                        for cb in self.callbacks:
                            await cb(signal)
                return 200

    def _parse_gecko_pool(self, pool: Dict) -> Optional[TokenSignal]:
        try:
            attributes = pool.get("attributes", {})
            relationships = pool.get("relationships", {})
            base_data = relationships.get("base_token", {}).get("data", {})
            mint = str(base_data.get("id", "")).removeprefix("solana_")
            quote_data = relationships.get("quote_token", {}).get("data", {})
            quote = str(quote_data.get("id", "")).removeprefix("solana_")
            if not mint or quote != "So11111111111111111111111111111111111111112":
                return None
            transactions = attributes.get("transactions", {}).get("h1", {})
            buys = int(transactions.get("buys", 0) or 0)
            sells = int(transactions.get("sells", 0) or 0)
            buyers = int(transactions.get("buyers", 0) or 0)
            sellers = int(transactions.get("sellers", 0) or 0)
            total_trades = buys + sells
            name = str(attributes.get("name", "Unknown / SOL")).split(" / ")[0]
            market_cap_usd = float(attributes.get("market_cap_usd") or attributes.get("fdv_usd") or 0)
            liquidity_usd = float(attributes.get("reserve_in_usd") or 0)
            dex_id = str(relationships.get("dex", {}).get("data", {}).get("id", ""))
            signal = TokenSignal(
                mint=mint,
                name=name,
                symbol=name,
                dev_buy_sol=0.0,
                market_cap_sol=market_cap_usd / 200.0,
                liquidity_sol=liquidity_usd / 200.0,
                buy_ratio=buys / max(total_trades, 1),
                unique_wallets=buyers + sellers,
                total_trades=total_trades,
                behavior_data={
                    "data_quality": "pool_snapshot",
                    "dev_buy_known": False,
                    "migration": dex_id in {"raydium", "pumpswap", "raydium-clmm"},
                    "dex": dex_id,
                },
                source="gecko_new_pool",
            )
            signal.calculate_overall_score()
            return signal
        except (TypeError, ValueError, AttributeError) as exc:
            logger.debug("Could not parse Gecko pool: %s", exc)
            return None

    def _passes_filters(self, signal: TokenSignal) -> bool:
        if signal.liquidity_sol < self.filters.min_liquidity_sol:
            return False
        if signal.market_cap_sol < self.filters.min_market_cap_sol:
            return False
        if signal.market_cap_sol > self.filters.max_market_cap_sol:
            return False
        return True


class SolanaTrackerScanner(TokenSource):
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
                buy_ratio=0.6,
                unique_wallets=token.get("holder", 0) or 0,
                total_trades=pools.get(" trades", 0) or 0
            )
            signal.calculate_overall_score()
            for cb in self.callbacks:
                await cb(signal)
        except Exception as e:
            logger.error(f"Error processing tracker message: {e}")


class TokenScanner:
    def __init__(self, filters: ScanFilters = None):
        self.filters = filters or ScanFilters()
        self.scanners: List[TokenSource] = []
        self.active_signals: Dict[str, TokenSignal] = {}
        self.on_signal: Optional[Callable] = None
        self._scanner_tasks: List[asyncio.Task] = []

    def add_scanner(self, scanner: TokenSource):
        self.scanners.append(scanner)

    async def start(self, on_signal: Callable[[TokenSignal], None]):
        self.on_signal = on_signal
        for scanner in self.scanners:
            try:
                await scanner.connect()
            except Exception as exc:
                logger.error("Scanner failed to connect: %s", exc)

        self._scanner_tasks = [
            asyncio.create_task(scanner.subscribe(self._handle_signal))
            for scanner in self.scanners
            if getattr(scanner, "running", False)
        ]
        logger.info("Started %s scanner subscription task(s)", len(self._scanner_tasks))
        while True:
            await asyncio.sleep(1)

    async def _handle_signal(self, signal: TokenSignal):
        if signal.mint in self.active_signals:
            existing = self.active_signals[signal.mint]
            signal.overall_score = max(existing.overall_score, signal.calculate_overall_score())
        self.active_signals[signal.mint] = signal
        if self.on_signal:
            await self.on_signal(signal)

    async def stop(self):
        for task in self._scanner_tasks:
            task.cancel()
        if self._scanner_tasks:
            await asyncio.gather(*self._scanner_tasks, return_exceptions=True)
        self._scanner_tasks.clear()
        for scanner in self.scanners:
            await scanner.disconnect()
