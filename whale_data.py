"""Lightweight, cached whale/KOL intelligence and learned actor registry."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiohttp

from config import config
from wallet_store import WalletStore

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

    def __init__(self, store: Optional[WalletStore] = None):
        self.store = store or WalletStore()
        self.whale_wallets: Dict[str, WhaleWallet] = {}
        self._cache: Dict[str, tuple] = {}
        self._request_times: List[float] = []
        self.gmgn_base = "https://gmgn.ai/api/v1"
        self.gmgn_quotation_base = "https://gmgn.ai/defi/quotation/v1"
        self.request_headers = {
            "User-Agent": "Mozilla/5.0 (compatible; MEME-HUNTER/1.0)",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://gmgn.ai/",
        }
        self.gmgn_api_key = os.getenv("GMGN_API_KEY", "").strip()
        self.gmgn_cli_path = os.getenv("GMGN_CLI_PATH", "gmgn-cli")
        self._activity_by_mint: Dict[str, List[Dict]] = {}
        self._activity_cache_time = 0.0
        self._activity_refresh_lock = asyncio.Lock()
        self._activity_warning_logged = False
        self._token_enrichment_times: List[float] = []
        self._init_whale_wallets()
        self._load_learned_wallets()

    def _init_whale_wallets(self) -> None:
        # No fabricated addresses are shipped. GMGN/config/Telegram can add
        # actual addresses at runtime.
        return

    def _load_learned_wallets(self) -> None:
        try:
            for item in self.store.get_wallet_candidates(limit=100):
                source = item.get("source", "learned")
                self.add_wallet(
                    item["address"], item.get("name", "Learned actor"), source,
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
        self.store.ensure_wallet(
            address=wallet.address, name=wallet.name, source=wallet.source,
            wallet_type="learned" if wallet.source == "learned" else wallet.source,
            min_buy_threshold=wallet.min_buy_threshold,
        )
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
        headers: Optional[Dict[str, str]] = None,
    ) -> Optional[Dict[str, Any]]:
        while self._is_rate_limited():
            await asyncio.sleep(0.5)
        self._request_times.append(time.time())
        try:
            async with session.get(
                url,
                params=params,
                headers=headers or self.request_headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status == 429:
                    logger.warning("GMGN rate limit; backing off")
                    await asyncio.sleep(5)
                    return None
                if response.status != 200:
                    logger.warning("GMGN response %s for %s", response.status, url)
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

    @staticmethod
    def _extract_cli_items(payload: Any) -> List[Dict]:
        """Extract trade records from the CLI's possible response envelopes."""
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            return []
        for key in ("list", "activities", "trades", "data", "result"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                nested = WhaleDataFetcher._extract_cli_items(value)
                if nested:
                    return nested
        return []

    async def _run_gmgn_cli(self, source: str, limit: int = 100) -> List[Dict]:
        """Read KOL/smart-money trades through the official read-only CLI."""
        if not self.gmgn_api_key:
            if not self._activity_warning_logged:
                logger.warning(
                    "GMGN_API_KEY is not configured; KOL/smart-money activity is disabled"
                )
                self._activity_warning_logged = True
            return []
        env = os.environ.copy()
        env["GMGN_API_KEY"] = self.gmgn_api_key
        command = [
            self.gmgn_cli_path,
            "track",
            source,
            "--chain",
            "sol",
            "--side",
            "buy",
            "--limit",
            str(limit),
            "--raw",
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
            if process.returncode != 0:
                logger.warning(
                    "GMGN %s feed failed with exit code %s: %s",
                    source,
                    process.returncode,
                    stderr.decode(errors="replace")[-300:],
                )
                return []
            text = stdout.decode(errors="replace").strip()
            # Some CLI versions print a short status line before JSON.
            start = min((index for index in (text.find("{"), text.find("[")) if index >= 0), default=-1)
            if start < 0:
                return []
            payload = json.loads(text[start:])
            return self._extract_cli_items(payload)
        except FileNotFoundError:
            logger.warning("GMGN CLI not found at %s", self.gmgn_cli_path)
        except asyncio.TimeoutError:
            logger.warning("GMGN %s feed timed out", source)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not parse GMGN %s feed: %s", source, exc)
        return []

    async def fetch_token_info(self, mint: str) -> Dict[str, Any]:
        """Fetch one cached GMGN token/security snapshot for enrichment."""
        key = f"gmgn_token_info:{mint}"
        cached = self._get_cache(key)
        if cached is not None:
            return cached
        now = time.time()
        self._token_enrichment_times = [stamp for stamp in self._token_enrichment_times if now - stamp < 3600]
        if len(self._token_enrichment_times) >= config.TRADING.gmgn_token_enrich_per_hour:
            return {}
        if not self.gmgn_api_key:
            return {}
        self._token_enrichment_times.append(now)
        env = os.environ.copy()
        env["GMGN_API_KEY"] = self.gmgn_api_key
        command = [
            self.gmgn_cli_path, "token", "info", "--chain", "sol",
            "--address", mint, "--raw",
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *command, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, env=env,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=20)
            if process.returncode != 0:
                logger.debug("GMGN token info failed for %s: %s", mint[:12], stderr.decode(errors="replace")[-200:])
                return {}
            text = stdout.decode(errors="replace").strip()
            start = min((index for index in (text.find("{"), text.find("[")) if index >= 0), default=-1)
            if start < 0:
                return {}
            payload = json.loads(text[start:])
            items = self._extract_cli_items(payload)
            if items:
                result = items[0]
            elif isinstance(payload, dict):
                result = payload.get("data", payload.get("result", payload))
            else:
                result = {}
            result = result if isinstance(result, dict) else {}
            self._set_cache(key, result, ttl=60)
            return result
        except (FileNotFoundError, asyncio.TimeoutError, json.JSONDecodeError, OSError) as exc:
            logger.debug("Could not enrich %s from GMGN: %s", mint[:12], exc)
            return {}

    @staticmethod
    def _normalize_activity(item: Dict, source: str) -> Optional[Dict]:
        mint = (
            item.get("base_address")
            or item.get("token_address")
            or item.get("mint")
            or (item.get("base_token") or {}).get("address")
        )
        wallet = item.get("maker") or item.get("wallet") or item.get("wallet_address")
        if not mint or not wallet:
            return None
        side = str(item.get("side", item.get("event", "buy"))).lower()
        if side not in {"buy", "bought", "0"}:
            return None
        try:
            amount_usd = float(item.get("amount_usd", item.get("value_usd", 0)) or 0)
        except (TypeError, ValueError):
            amount_usd = 0.0
        try:
            amount_sol = float(item.get("sol_amount", item.get("amount_sol", 0)) or 0)
        except (TypeError, ValueError):
            amount_sol = 0.0
        if amount_sol <= 0 and amount_usd > 0:
            # Approximation is only used for the existing SOL threshold; keep
            # the original USD amount in evidence for later accurate analysis.
            amount_sol = amount_usd / float(os.getenv("SOL_PRICE_USD", "150"))
        maker_info = item.get("maker_info") or {}
        return {
            "base_address": mint,
            "address": wallet,
            "wallet": wallet,
            "wallet_name": maker_info.get("twitter_username") or source,
            "source": "kol" if source == "kol" else "gmgn",
            "sol_amount": amount_sol,
            "amount_usd": amount_usd,
            "pnl": item.get("pnl", 0),
            "win_rate": item.get("win_rate", 0),
            "timestamp": item.get("timestamp") or item.get("time"),
            "tags": maker_info.get("tags", []),
            "is_open_or_close": item.get("is_open_or_close"),
        }

    async def refresh_activity_feeds(self, limit: int = 100) -> int:
        """Refresh KOL and smart-money buys once per minute, not per wallet."""
        if time.time() - self._activity_cache_time < CACHE_TTL["wallet_trades"]:
            return sum(len(items) for items in self._activity_by_mint.values())
        async with self._activity_refresh_lock:
            if time.time() - self._activity_cache_time < CACHE_TTL["wallet_trades"]:
                return sum(len(items) for items in self._activity_by_mint.values())
            kol_items, smart_items = await asyncio.gather(
                self._run_gmgn_cli("kol", limit),
                self._run_gmgn_cli("smartmoney", limit),
            )
            self._activity_by_mint = {}
            for source, items in (("kol", kol_items), ("smartmoney", smart_items)):
                for item in items:
                    activity = self._normalize_activity(item, source)
                    if activity:
                        self._activity_by_mint.setdefault(activity["base_address"], []).append(activity)
                        self.add_wallet(
                            activity["address"], activity["wallet_name"], activity["source"],
                            activity.get("sol_amount", 0.5), copy_trade=False,
                        )
            self._activity_cache_time = time.time()
            return sum(len(items) for items in self._activity_by_mint.values())

    async def fetch_top_traders(self, chain: str = "sol", limit: int = 50) -> List[Dict]:
        key = f"top_traders:{chain}:{limit}"
        cached = self._get_cache(key)
        if cached is not None:
            return cached
        async with aiohttp.ClientSession() as session:
            # Use the current browser-facing leaderboard directly. The former
            # /api/v1/wallets/sol endpoint is obsolete and only creates noisy
            # 404s before the valid fallback can run.
            rank_url = f"{self.gmgn_quotation_base}/rank/{chain}/wallets/7d"
            rank_params = {
                "orderby": "pnl_7d",
                "direction": "desc",
                "limit": limit,
            }
            rank_payload = await self._request(session, rank_url, rank_params)
            rank_data = rank_payload.get("data", {}) if rank_payload else {}
            traders = []
            if isinstance(rank_data, dict):
                traders = rank_data.get("rank") or rank_data.get("wallets") or []
        if not isinstance(traders, list):
            traders = []
        for trader in traders:
            address = (
                trader.get("address")
                or trader.get("wallet_address")
                or trader.get("wallet")
            )
            if address:
                self.add_wallet(
                    address,
                    trader.get("name") or trader.get("twitter_username") or f"GMGN_{address[:8]}",
                    "gmgn",
                    1.0,
                )
        if not traders:
            logger.warning("GMGN returned no top traders; whale confirmation is unavailable")
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
        """Return cached KOL/smart-money buys for one token."""
        await self.refresh_activity_feeds(limit=100)
        return [
            item for item in self._activity_by_mint.get(mint, [])
            if float(item.get("sol_amount", 0) or 0) >= float(min_sol)
        ]

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
        """
        Enhanced summary with vetting against inflated win rate and low trade count.
        Applies config thresholds for min win rate and min trades if available in item.
        """
        if not buys:
            return {
                "signal": "NONE", "total_sol": 0.0, "total_buys": 0, "wallet_count": 0,
                "whale_count": 0, "kol_count": 0, "avg_buy": 0.0, "avg_win_rate": 0.0, "top_buyer": None,
                "conviction": "LOW", "all_buys": [], "filtered_out": 0,
            }

        # New: filter out wallets that look suspicious per selectivity framework
        filtered_buys = []
        filtered_out = 0
        min_win = getattr(config.TRADING, "whale_min_win_rate", 55.0)
        min_trades = getattr(config.TRADING, "whale_min_trades", 20)

        for item in buys:
            # Ignore tiny test buys <0.1 SOL if not already filtered
            if float(item.get("sol_amount", 0) or 0) < 0.1:
                filtered_out += 1
                continue
            # If win_rate known, require min win rate unless it's a KOL (KOL may have different metrics)
            wr = item.get("win_rate")
            if wr is not None:
                try:
                    wr_f = float(wr)
                    # Win rate is 0-1 or 0-100? Normalize
                    if wr_f <= 1:
                        wr_f *= 100
                    if wr_f < min_win and wr_f != 0:
                        # Allow if source is gmgn and amount big? No, strict for selectivity
                        # Except if convction high and it's a KOL we keep but log
                        if item.get("source") != "kol":
                            filtered_out += 1
                            continue
                except (TypeError, ValueError):
                    pass
            # If total trades known and < min, skip - likely inflated win rate via holding losers
            total_trades = item.get("total_trades") or item.get("trade_count")
            if total_trades is not None:
                try:
                    if int(total_trades) < min_trades:
                        filtered_out += 1
                        continue
                except (TypeError, ValueError):
                    pass
            filtered_buys.append(item)

        # Use filtered list for signal, but keep original for evidence
        use_buys = filtered_buys if filtered_buys else buys
        if not filtered_buys:
            # If all filtered, downgrade signal to NONE to avoid false whale alert
            pass

        total_sol = sum(float(item.get("sol_amount", 0) or 0) for item in use_buys)
        whale_buys = [item for item in use_buys if item.get("source") == "gmgn" and float(item.get("sol_amount", 0) or 0) >= 2]
        kol_buys = [item for item in use_buys if item.get("source") in ("kol", "kolscan")]

        # Conviction now also considers wallet diversity
        wallet_count = len({item.get("address", item.get("wallet")) for item in use_buys})
        if total_sol >= 10 and wallet_count >= 3:
            signal, conviction = "STRONG", "HIGH"
        elif total_sol >= 5 and wallet_count >= 2:
            signal, conviction = "MODERATE", "MEDIUM"
        elif total_sol >= 2:
            signal, conviction = "WEAK", "LOW"
        else:
            signal, conviction = "MINIMAL", "VERY_LOW"

        # If filtered out many, reduce conviction
        if filtered_out >= 3 and conviction in {"HIGH", "MEDIUM"}:
            conviction = "LOW"
            signal = "WEAK"

        known_win_rates = []
        for item in use_buys:
            wr = item.get("win_rate")
            if wr is not None:
                try:
                    wr_f = float(wr)
                    if wr_f <= 1:
                        wr_f *= 100
                    known_win_rates.append(wr_f)
                except (TypeError, ValueError):
                    pass

        return {
            "signal": signal if filtered_buys else "NONE",
            "total_sol": total_sol,
            "total_buys": len(use_buys),
            "wallet_count": wallet_count,
            "whale_count": len(whale_buys),
            "kol_count": len(kol_buys),
            "avg_buy": total_sol / len(use_buys) if use_buys else 0.0,
            "avg_win_rate": sum(known_win_rates) / len(known_win_rates) if known_win_rates else 0.0,
            "top_buyer": max(use_buys, key=lambda item: float(item.get("sol_amount", 0) or 0)) if use_buys else None,
            "conviction": conviction,
            "all_buys": use_buys,
            "filtered_out": filtered_out,
            "original_buys_count": len(buys),
        }

    def learn_from_profitable_token(
        self, mint: str, symbol: str, pnl_sol: float, actors: List[Dict]
    ) -> int:
        """Persist actor evidence and add profitable-token actors as alert-only wallets."""
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
    activity_count = await whale_fetcher.refresh_activity_feeds(limit=100)
    return {
        "count": len(traders),
        "tracked_count": len(whale_fetcher.whale_wallets),
        "activity_count": activity_count,
        "sources": ["GMGN leaderboard", "GMGN KOL", "GMGN smart-money"],
    }
