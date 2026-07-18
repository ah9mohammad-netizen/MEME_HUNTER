"""
MEME HUNTER - Risk Analyzer
===========================
Security checks, rug detection, and risk scoring for meme coins.
"""

import asyncio
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class RiskReport:
    """Comprehensive risk analysis report"""
    mint: str

    # Overall score (0-100, higher is safer)
    overall_score: float = 0.0

    # Component scores
    contract_score: float = 0.0
    liquidity_score: float = 0.0
    holder_score: float = 0.0
    social_score: float = 0.0

    # Flags
    is_honeypot: bool = False
    is_rugged: bool = False
    has_mint_authority: bool = False
    has_freeze_authority: bool = False
    lp_locked: bool = False
    dev_wallet_suspicious: bool = False
    developer_cluster: bool = False
    high_concentration: bool = False
    new_token_unverified: bool = False

    # Details
    top_holder_pct: float = 0.0
    dev_holding_pct: float = 0.0
    lp_locked_pct: float = 0.0
    rug_pull_history: bool = False

    # Warnings
    warnings: List[str] = None

    # Timestamp
    analyzed_at: datetime = None

    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []
        if self.analyzed_at is None:
            self.analyzed_at = datetime.now()

    def get_recommendation(self) -> str:
        """Get trading recommendation based on score"""
        if self.overall_score >= 80:
            return "APPROVE - Low risk, good for trading"
        elif self.overall_score >= 60:
            return "CAUTION - Moderate risk, trade with small position"
        elif self.overall_score >= 40:
            return "HIGH RISK - Only if strong conviction"
        else:
            return "REJECT - Too risky, avoid"

    def is_tradeable(self, min_score: float = 50.0) -> bool:
        """Check if token passes minimum safety threshold"""
        if self.is_honeypot or self.is_rugged:
            return False
        if self.high_concentration or self.developer_cluster:
            return False
        return self.overall_score >= min_score


class RiskAnalyzer:
    """
    Comprehensive risk analysis for meme tokens
    """

    def __init__(self):
        # API endpoints
        self.rugcheck_api = "https://api.rugcheck.xyz/v1"
        self.goplus_api = "https://api.gopluslabs.io/api/v1"
        self.dextools_api = "https://api.dextools.io/v2"

        # Cache for recent results
        self._cache: Dict[str, Tuple[RiskReport, datetime]] = {}
        self._cache_ttl = 60  # seconds

    async def analyze(self, mint: str, market_data: Dict = None) -> RiskReport:
        """
        Perform comprehensive risk analysis on a token

        Args:
            mint: Token mint address
            market_data: Optional pre-fetched market data

        Returns:
            RiskReport with detailed analysis
        """
        # Check cache
        if mint in self._cache:
            cached, timestamp = self._cache[mint]
            if (datetime.now() - timestamp).seconds < self._cache_ttl:
                return cached

        report = RiskReport(mint=mint)

        # Run all checks in parallel
        tasks = [
            self._check_contract_safety(mint),
            self._check_honeypot(mint),
            self._check_liquidity(mint),
            self._check_holder_distribution(mint),
            self._check_dev_wallets(mint),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        contract_ok, contract_data = results[0] if isinstance(results[0], tuple) else (True, {})
        is_honeypot, honeypot_data = results[1] if isinstance(results[1], tuple) else (False, {})
        liquidity_ok, liquidity_data = results[2] if isinstance(results[2], tuple) else (True, {})
        holders_ok, holder_data = results[3] if isinstance(results[3], tuple) else (True, {})
        dev_ok, dev_data = results[4] if isinstance(results[4], tuple) else (True, {})

        # Update report
        report.is_honeypot = is_honeypot
        report.has_mint_authority = contract_data.get("has_mint_authority", True)
        report.has_freeze_authority = contract_data.get("has_freeze_authority", True)

        report.lp_locked = liquidity_data.get("lp_locked", False)
        report.lp_locked_pct = liquidity_data.get("lock_pct", 0)

        report.top_holder_pct = holder_data.get("top_10_pct", 0)
        report.dev_holding_pct = holder_data.get("dev_pct", 0)
        report.high_concentration = report.top_holder_pct > 30
        report.developer_cluster = holder_data.get("has_cluster", False)

        # Calculate scores
        report.contract_score = self._calc_contract_score(contract_data)
        report.liquidity_score = self._calc_liquidity_score(liquidity_data)
        report.holder_score = self._calc_holder_score(holder_data)

        # Overall score (weighted average)
        report.overall_score = (
            report.contract_score * 0.35 +
            report.liquidity_score * 0.25 +
            report.holder_score * 0.40
        )

        # Add warnings
        if report.has_mint_authority:
            report.warnings.append("⚠️ Mint authority NOT revoked - dev can create tokens")
        if report.has_freeze_authority:
            report.warnings.append("⚠️ Freeze authority NOT revoked - dev can freeze assets")
        if not report.lp_locked:
            report.warnings.append("⚠️ LP NOT locked - risk of rug pull")
        if report.high_concentration:
            report.warnings.append(f"⚠️ High holder concentration: {report.top_holder_pct:.1f}%")
        if report.developer_cluster:
            report.warnings.append("⚠️ Developer cluster detected")
        if report.is_honeypot:
            report.warnings.append("🚨 HONEYPOT DETECTED - Cannot sell!")

        # Cache result
        self._cache[mint] = (report, datetime.now())

        return report

    async def _check_contract_safety(self, mint: str) -> Tuple[bool, Dict]:
        """Check contract safety via RugCheck"""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.rugcheck_api}/tokens/{mint}/reports"
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        report = data.get("report", {})

                        # Check for dangerous functions
                        has_mint = report.get("isMint", False)
                        has_freeze = report.get("isFreeze", False)
                        is_exploitable = report.get("isExploitable", False)

                        return not is_exploitable, {
                            "has_mint_authority": has_mint,
                            "has_freeze_authority": has_freeze,
                            "trust_level": report.get("trustLevel", "unknown")
                        }
        except Exception as e:
            logger.warning(f"RugCheck API error for {mint}: {e}")

        return True, {}

    async def _check_honeypot(self, mint: str) -> Tuple[bool, Dict]:
        """Check if token is a honeypot"""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.goplus_api}/token-security/{mint}"
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        token_data = data.get("data", {})

                        is_honeypot = token_data.get("is_honeypot", False)
                        honeypot_type = token_data.get("honeypot_type", "none")

                        return is_honeypot, {
                            "honeypot_type": honeypot_type,
                            "buy_tax": token_data.get("buy_tax", 0),
                            "sell_tax": token_data.get("sell_tax", 0)
                        }
        except Exception as e:
            logger.warning(f"GoPlus API error for {mint}: {e}")

        return False, {}

    async def _check_liquidity(self, mint: str) -> Tuple[bool, Dict]:
        """Check liquidity and LP lock status"""
        try:
            async with aiohttp.ClientSession() as session:
                # Get pair data from DexScreener
                url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        pairs = data.get("pairs", [])

                        if pairs:
                            pair = pairs[0]  # Top pair
                            liquidity = pair.get("liquidity", {})
                            liquidity_usd = float(liquidity.get("usd", 0))

                            # Check LP lock (usually shown in pair data)
                            lp_address = pair.get("lxStack", [])
                            lp_locked = len(lp_address) > 0 if lp_address else False

                            return liquidity_usd > 5000, {
                                "liquidity_usd": liquidity_usd,
                                "lp_locked": lp_locked,
                                "lock_pct": 100 if lp_locked else 0  # Simplified
                            }
        except Exception as e:
            logger.warning(f"Liquidity check error for {mint}: {e}")

        return True, {}

    async def _check_holder_distribution(self, mint: str) -> Tuple[bool, Dict]:
        """Analyze holder distribution for concentration"""
        try:
            async with aiohttp.ClientSession() as session:
                # Use Solana RPC to get token accounts
                url = "https://api.mainnet-beta.solana.com"
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTokenLargestAccounts",
                    "params": [mint]
                }

                async with session.post(url, json=payload, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        accounts = data.get("result", {}).get("value", [])

                        if not accounts:
                            return True, {}

                        # Calculate top holder percentages
                        total_supply = sum(float(acc.get("uiAmount", 0)) for acc in accounts)
                        top_10_pct = 0
                        dev_pct = 0

                        for i, acc in enumerate(accounts[:10]):
                            amount = float(acc.get("uiAmount", 0))
                            pct = (amount / total_supply * 100) if total_supply > 0 else 0
                            top_10_pct += pct

                            # First holder is often dev
                            if i == 0:
                                dev_pct = pct

                        return True, {
                            "top_10_pct": top_10_pct,
                            "dev_pct": dev_pct,
                            "has_cluster": False  # Would need more analysis
                        }
        except Exception as e:
            logger.warning(f"Holder check error for {mint}: {e}")

        return True, {}

    async def _check_dev_wallets(self, mint: str) -> Tuple[bool, Dict]:
        """Check developer wallet behavior"""
        # This would integrate with GMGN, Arkham, etc.
        # For now, return placeholder
        return True, {"suspicious": False}

    def _calc_contract_score(self, data: Dict) -> float:
        """Calculate contract safety score (0-100)"""
        score = 100

        if data.get("has_mint_authority"):
            score -= 30
        if data.get("has_freeze_authority"):
            score -= 20
        if data.get("trust_level") == "danger":
            score -= 50

        return max(0, score)

    def _calc_liquidity_score(self, data: Dict) -> float:
        """Calculate liquidity score (0-100)"""
        liquidity = data.get("liquidity_usd", 0)

        if liquidity < 1000:
            return 10
        elif liquidity < 5000:
            return 30
        elif liquidity < 10000:
            return 50
        elif liquidity < 50000:
            return 75
        else:
            score = min(100, 75 + (liquidity - 50000) / 1000)

        if data.get("lp_locked"):
            score += 10

        return min(100, score)

    def _calc_holder_score(self, data: Dict) -> float:
        """Calculate holder distribution score (0-100)"""
        top_10_pct = data.get("top_10_pct", 0)

        if top_10_pct > 60:
            return 10
        elif top_10_pct > 45:
            return 30
        elif top_10_pct > 30:
            return 50
        elif top_10_pct > 20:
            return 75
        else:
            return 95


class WhaleTracker:
    """
    Track whale and KOL wallet activity
    """

    def __init__(self, tracked_wallets: List[Dict] = None):
        self.tracked_wallets = tracked_wallets or []
        self._wallet_cache: Dict[str, Dict] = {}

    def add_wallet(self, address: str, name: str, wallet_type: str = "whale"):
        """Add a wallet to track"""
        self.tracked_wallets.append({
            "address": address,
            "name": name,
            "type": wallet_type
        })

    async def check_wallet_activity(self, wallet: str) -> Optional[Dict]:
        """Get recent activity for a wallet"""
        try:
            async with aiohttp.ClientSession() as session:
                # Use GMGN API
                url = f"https://gmgn.ai/api/v1/wallet_history/sol/{wallet}"
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("data", {})
        except Exception as e:
            logger.warning(f"Wallet check error: {e}")

        return None

    async def scan_for_buys(self, mint: str) -> List[Dict]:
        """Scan for whale/KOL buys of a specific token"""
        buys = []

        for wallet_info in self.tracked_wallets:
            wallet = wallet_info["address"]
            activity = await self.check_wallet_activity(wallet)

            if activity:
                # Check if they bought this token
                trades = activity.get("trades", [])
                for trade in trades:
                    if trade.get("mint") == mint:
                        buys.append({
                            "wallet": wallet,
                            "name": wallet_info["name"],
                            "type": wallet_info["type"],
                            "amount_sol": trade.get("sol_amount", 0),
                            "pnl": trade.get("pnl", 0),
                            "timestamp": trade.get("time")
                        })

        return buys

    async def get_smart_money_flow(self, mint: str) -> Dict:
        """Analyze smart money flow for a token"""
        buys = await self.scan_for_buys(mint)

        total_sol = sum(b["amount_sol"] for b in buys)
        buy_count = len(buys)

        # Whale vs KOL split
        whale_buys = [b for b in buys if b["type"] == "whale"]
        kol_buys = [b for b in buys if b["type"] == "kol"]

        return {
            "total_buys": buy_count,
            "total_sol": total_sol,
            "whale_count": len(whale_buys),
            "whale_sol": sum(b["amount_sol"] for b in whale_buys),
            "kol_count": len(kol_buys),
            "kol_sol": sum(b["amount_sol"] for b in kol_buys),
            "buys": buys,
            "signal": "STRONG" if total_sol > 5 else "MODERATE" if total_sol > 1 else "WEAK"
        }
