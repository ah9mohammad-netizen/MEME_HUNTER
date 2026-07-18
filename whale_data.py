"""Lightweight, cached whale/KOL intelligence and learned actor registry."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiohttp

from state_store import StateStore

logger = logging.getLogger(__name__)

CACHE_TTL = {
    "wallet_trades": 60,
    "top_traders": 300,
    "token_analytics": 60,
    "trending": 60,
}


@dataclass
class WhaleWallet:
    address: str
    name: str
    source: str = "manual"
    min_buy_threshold: float = 0.5
    track_pnl: bool = True
    copy_trade: bool = False
    last_seen: datetime = field(default_factory=datetime.now)


class WhaleDataFetcher:
    """Fetch only the small amount of smart-money data needed by a signal."""

    def __init__(self, store: Optional[StateStore] = None):
        self.store = store or StateStore()
        self.whale_wallets: Dict[str, WhaleWallet] = {}
        self._cache: Dict[str, tuple] = {}
        self._request_times: List[float] = []
        self.gmgn_base = "https://gmgn.ai/api/v1"
        self._init_whale_wallets()
        self._load_learned_wallets()

    def _init_whale_wallets(self) -> None:
        # No fabricated addresses are shipped. GMGN/config/Telegram can add
        # actual addresses at runtime.
        return

    def _load_learned_wallets(self) -> None:
        try:
            for item in self.store.get_wallet_candidates(limit=100):
                self.add_wallet(
                    item["address"], item.get("name", "Learned actor"), "learned",
                    float(item.get("min_buy_threshold", 0.5)), copy_trade=False,
                )
        except Exception as exc:
            logger.warning("Could not load learned wallet candidates: %s", exc)

    def add_wallet(
        self,
        address: str,
        name: str = "Tracked wallet",
        source: str = "manual",
        min_buy_threshold: float = 0.5,
        copy_trade: bool = False,
    ) -> Optional[WhaleWallet]:
        """Add a real wallet; learned wallets are intentionally alert-only."""
        address = str(address).strip()
        if len(address) < 32:
            return None
        wallet = self.whale_wallets.get(address)
        if wallet is None:
            wallet = WhaleWallet(
                address=address,
                name=name or f"Wallet {address[:8]}",
                source=source,
                min_buy_threshold=float(min_buy_threshold),
                copy_trade=bool(copy_trade) if source != "learned" else False,
            )
            self.whale_wallets[address] = wallet
        else:
            wallet.name = name or wallet.name
            wallet.min_buy_threshold = float(min_buy_threshold or wallet.min_buy_threshold)
        return wallet

    def _is_rate_limited(self) -> bool:
        now = time.time()
        self._request_times = [stamp for stamp in self._request_times if now - stamp < 10]
        return len(self._request_times) >= 30

    async def _request(
        self,
        session: aiohttp.ClientSession,
        url: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        while self._is_rate_limited():
            await asyncio.sleep(0.5)
        self._request_times.append(time.time())
        try:
            async with session.get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 429:
                    logger.warning("GMGN rate limit; backing off")
                    await asyncio.sleep(5)
                    return None
                if response.status != 200:
                    logger.debug("GMGN response %s for %s", response.status, url)
                    return None
                payload = await response.json()
                return payload if isinstance(payload, dict) else None
        except Exception as exc:
            logger.debug("GMGN request failed: %s", exc)
            return None

    def _get_cache(self, key: str) -> Any:
        item = self._cache.get(key)
        if item is None:
            return None
        data, timestamp, ttl = item
        if time.time() - timestamp < ttl:
            return data
        self._cache.pop(key, None)
        return None

    def _set_cache(self, key: str, data: Any, ttl: Optional[int] = None) -> None:
        self._cache[key] = (data, time.time(), ttl or CACHE_TTL.get(key.split(":")[0], 60))

    async def fetch_top_traders(self, chain: str = "sol", limit: int = 50) -> List[Dict]:
        key = f"top_traders:{chain}:{limit}"
        cached = self._get_cache(key)
        if cached is not None:
            return cached
        async with aiohttp.ClientSession() as session:
            payload = await self._request(
                session, f"{self.gmgn_base}/wallets/{chain}",
                {"limit": limit, "order_by": "pnl_7d", "sort": "desc"},
            )
        data = payload.get("data", {}) if payload else {}
        traders = data.get("wallets", []) if isinstance(data, dict) else []
        for trader in traders:
            address = trader.get("address")
            if address:
                self.add_wallet(address, trader.get("name", f"GMGN_{address[:8]}"), "gmgn", 1.0)
        self._set_cache(key, traders)
        return traders

    async def fetch_wallet_trades(self, wallet: str, limit: int = 20) -> List[Dict]:
        key = f"wallet_trades:{wallet}:{limit}"
        cached = self._get_cache(key)
        if cached is not None:
            return cached
        async with aiohttp.ClientSession() as session:
            payload = await self._request(
                session, f"{self.gmgn_base}/wallet_history/sol/{wallet}", {"limit": limit}
            )
        data = payload.get("data", {}) if payload else {}
        trades = data.get("trades", []) if isinstance(data, dict) else []
        if not isinstance(trades, list):
            trades = []
        self._set_cache(key, trades)
        return trades

    async def get_wallet_positions(self, wallet: str) -> List[Dict]:
        key = f"wallet_positions:{wallet}"
        cached = self._get_cache(key)
        if cached is not None:
            return cached
        async with aiohttp.ClientSession() as session:
            payload = await self._request(session, f"{self.gmgn_base}/wallet_positions/sol/{wallet}")
        data = payload.get("data", {}) if payload else {}
        positions = data.get("positions", []) if isinstance(data, dict) else []
        self._set_cache(key, positions)
        return positions

    async def check_token_buys(self, mint: str, min_sol: float = 0.5) -> List[Dict]:
        """Check cached/recent wallet history concurrently, with a hard cap."""
        results: List[Dict] = []
        wallets = list(self.whale_wallets.values())[:50]
        semaphore = asyncio.Semaphore(5)

        async def check(wallet: WhaleWallet) -> None:
            async with semaphore:
                trades = await self.fetch_wallet_trades(wallet.address, limit=20)
            for trade in trades:
                trade_mint = trade.get("mint") or trade.get("token_address")
                if trade_mint != mint:
                    continue
                try:
                    amount = float(trade.get("sol_amount", trade.get("amount_sol", 0)) or 0)
                except (TypeError, ValueError):
                    amount = 0.0
                if amount < max(float(min_sol), wallet.min_buy_threshold):
                    continue
                evidence = {
                    "address": wallet.address,
                    "wallet": wallet.address,
                    "wallet_name": wallet.name,
                    "source": wallet.source,
                    "sol_amount": amount,
                    "pnl": trade.get("pnl", 0),
                    "win_rate": trade.get("win_rate", 0),
                    "timestamp": trade.get("time"),
                }
                results.append(evidence)

        await asyncio.gather(*(check(wallet) for wallet in wallets), return_exceptions=True)
        return results

    async def get_token_analytics(self, mint: str) -> Dict:
        key = f"token_analytics:{mint}"
        cached = self._get_cache(key)
        if cached is not None:
            return cached
        async with aiohttp.ClientSession() as session:
            payload = await self._request(session, f"{self.gmgn_base}/token_analytics/sol/{mint}")
        result = payload.get("data", {}) if payload else {}
        self._set_cache(key, result)
        return result if isinstance(result, dict) else {}

    @staticmethod
    def get_smart_money_summary(token_mint: str, buys: List[Dict]) -> Dict:
        if not buys:
            return {
                "signal": "NONE", "total_sol": 0.0, "total_buys": 0, "whale_count": 0,
                "kol_count": 0, "avg_buy": 0.0, "top_buyer": None,
                "conviction": "LOW", "all_buys": [],
            }
        total_sol = sum(float(item.get("sol_amount", 0) or 0) for item in buys)
        whale_buys = [item for item in buys if item.get("source") == "gmgn" and item.get("sol_amount", 0) >= 2]
        kol_buys = [item for item in buys if item.get("source") in ("kol", "kolscan")]
        if total_sol >= 10:
            signal, conviction = "STRONG", "HIGH"
        elif total_sol >= 5:
            signal, conviction = "MODERATE", "MEDIUM"
        elif total_sol >= 2:
            signal, conviction = "WEAK", "LOW"
        else:
            signal, conviction = "MINIMAL", "VERY_LOW"
        return {
            "signal": signal, "total_sol": total_sol, "total_buys": len(buys), "whale_count": len(whale_buys),
            "kol_count": len(kol_buys), "avg_buy": total_sol / len(buys),
            "top_buyer": max(buys, key=lambda item: item.get("sol_amount", 0)),
            "conviction": conviction, "all_buys": buys,
        }

    def learn_from_profitable_token(
        self, mint: str, symbol: str, pnl_sol: float, actors: List[Dict]
    ) -> int:
        """Persist actor evidence and add profitable-token actors as alert-only wallets."""
        self.store.record_token_outcome(mint, symbol, pnl_sol, actors)
        if pnl_sol <= 0:
            return 0
        added = 0
        for actor in actors:
            address = str(actor.get("address", actor.get("wallet", ""))).strip()
            if len(address) < 32:
                continue
            self.store.upsert_wallet_candidate(
                address=address,
                name=actor.get("wallet_name", f"Learned {address[:8]}"),
                source="learned", wallet_type=actor.get("type", "learned"),
                min_buy_threshold=float(actor.get("sol_amount", 0.5) or 0.5),
                profitable=True, pnl_sol=pnl_sol, token_mint=mint,
            )
            if self.add_wallet(address, actor.get("wallet_name", "Learned actor"), "learned", copy_trade=False):
                added += 1
        return added


class TrendingFetcher:
    def __init__(self):
        self.gmgn_base = "https://gmgn.ai/api/v1"
        self._cache: Dict[str, tuple] = {}

    def _get_cache(self, key: str, ttl: int = 60) -> Optional[Any]:
        item = self._cache.get(key)
        if item and time.time() - item[1] < ttl:
            return item[0]
        return None

    async def _fetch_pump(self, key: str, params: Dict[str, Any], ttl: int) -> List[Dict]:
        cached = self._get_cache(key, ttl)
        if cached is not None:
            return cached
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://api.pump.fun/coins", params=params, timeout=10) as response:
                    if response.status == 200:
                        payload = await response.json()
                        result = payload if isinstance(payload, list) else payload.get("coins", [])
                        self._cache[key] = (result, time.time())
                        return result
        except Exception as exc:
            logger.debug("Pump.fun trending request failed: %s", exc)
        return []

    async def get_new_tokens(self, limit: int = 20) -> List[Dict]:
        return await self._fetch_pump(f"new_tokens:{limit}", {"limit": limit, "sort": "created"}, 30)

    async def get_trending_tokens(self, limit: int = 20) -> List[Dict]:
        return await self._fetch_pump(f"trending:{limit}", {"limit": limit, "sort": "volume"}, 60)


whale_fetcher = WhaleDataFetcher()
trending_fetcher = TrendingFetcher()


async def get_whale_activity_for_token(mint: str) -> Dict:
    buys = await whale_fetcher.check_token_buys(mint, min_sol=0.5)
    return whale_fetcher.get_smart_money_summary(mint, buys)


async def update_whale_list_from_gmgn(limit: int = 100) -> Dict:
    traders = await whale_fetcher.fetch_top_traders(limit=limit)
    return {"count": len(traders), "sources": ["GMGN leaderboard"]}
