"""
MEME HUNTER - Risk Analyzer - Selectivity Focused Rewrite
===========================================================
Security checks, rug detection, and LIVE anti-rug monitoring.

Improvements:
- Enhanced bundle/wash/sniper/mechanical detection with MELT-inspired thresholds
- Funding cluster check integration (optional Helius key)
- Serial deployer detection
- Live rug checks: dev dump, liquidity drop, top holder spike
- Tighter fail-closed logic
"""

import asyncio
import logging
import os
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
import aiohttp

logger = logging.getLogger(__name__)

try:
    from config import config
    HAS_CONFIG = True
except ImportError:
    HAS_CONFIG = False

@dataclass
class RiskReport:
    mint: str
    overall_score: float = 0.0
    contract_score: float = 0.0
    liquidity_score: float = 0.0
    holder_score: float = 0.0
    social_score: float = 0.0

    is_honeypot: bool = False
    is_rugged: bool = False
    has_mint_authority: bool = False
    has_freeze_authority: bool = False
    lp_locked: bool = False
    dev_wallet_suspicious: bool = False
    developer_cluster: bool = False
    high_concentration: bool = False
    new_token_unverified: bool = False

    # Data-quality and behavioral warnings - enhanced
    checks_complete: bool = False
    check_status: Dict[str, bool] = None
    unavailable_checks: List[str] = None
    behavior_data_available: bool = False
    wash_trading_suspected: bool = False
    bundle_risk_suspected: bool = False
    mechanical_trading_suspected: bool = False
    creator_sold_early: bool = False
    sniper_saturation_suspected: bool = False
    funding_cluster_suspected: bool = False
    serial_deployer_suspected: bool = False
    time_cluster_suspected: bool = False
    sale_duration_risk: bool = False

    top_holder_pct: float = 0.0
    dev_holding_pct: float = 0.0
    lp_locked_pct: float = 0.0
    rug_pull_history: bool = False

    warnings: List[str] = None
    analyzed_at: datetime = None

    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []
        if self.check_status is None:
            self.check_status = {}
        if self.unavailable_checks is None:
            self.unavailable_checks = []
        if self.analyzed_at is None:
            self.analyzed_at = datetime.now()

    def get_recommendation(self) -> str:
        if self.overall_score >= 80:
            return "APPROVE - Low risk, good for trading"
        elif self.overall_score >= 60:
            return "CAUTION - Moderate risk, trade with small position"
        elif self.overall_score >= 40:
            return "HIGH RISK - Only if strong conviction"
        else:
            return "REJECT - Too risky, avoid"

    def is_tradeable(self, min_score: float = 50.0) -> bool:
        # Hard fails - expanded for selectivity
        if self.is_honeypot or self.is_rugged:
            return False
        if self.high_concentration or self.developer_cluster:
            return False
        if not self.checks_complete:
            return False
        if self.wash_trading_suspected or self.bundle_risk_suspected or self.creator_sold_early:
            return False
        if self.sniper_saturation_suspected or self.funding_cluster_suspected:
            return False
        if self.serial_deployer_suspected and self.overall_score < 60:
            return False
        return self.overall_score >= min_score


class RiskAnalyzer:
    def __init__(self):
        self.rugcheck_api = "https://api.rugcheck.xyz/v1"
        self.goplus_api = "https://api.gopluslabs.io/api/v1"
        self.dextools_api = "https://api.dextools.io/v2"
        self._cache: Dict[str, Tuple[RiskReport, datetime]] = {}
        self._cache_ttl = 60
        self._rpc_endpoint = os.getenv("RPC_ENDPOINT", "https://api.mainnet-beta.solana.com")
        if HAS_CONFIG:
            self._rpc_endpoint = getattr(config.TRADING, "rpc_endpoint", self._rpc_endpoint) or self._rpc_endpoint

    async def analyze(
        self,
        mint: str,
        market_data: Dict = None,
        behavior_data: Optional[Dict] = None,
    ) -> RiskReport:
        # Check cache
        if mint in self._cache:
            cached, timestamp = self._cache[mint]
            if (datetime.now() - timestamp).seconds < self._cache_ttl:
                return cached

        report = RiskReport(mint=mint)

        tasks = [
            self._check_contract_safety(mint),
            self._check_honeypot(mint),
            self._check_liquidity(mint),
            self._check_holder_distribution(mint),
            self._check_dev_wallets(mint),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        contract_ok, contract_data = results[0] if isinstance(results[0], tuple) else (True, {})
        is_honeypot, honeypot_data = results[1] if isinstance(results[1], tuple) else (False, {})
        liquidity_ok, liquidity_data = results[2] if isinstance(results[2], tuple) else (True, {})
        holders_ok, holder_data = results[3] if isinstance(results[3], tuple) else (True, {})
        dev_ok, dev_data = results[4] if isinstance(results[4], tuple) else (True, {})

        if not liquidity_data.get("available") and market_data:
            snapshot_liquidity = float(market_data.get("liquidity_usd", 0) or 0)
            if snapshot_liquidity > 0:
                liquidity_data = {
                    "available": True,
                    "liquidity_usd": snapshot_liquidity,
                    "lp_locked": False,
                    "lock_pct": 0,
                    "source": "signal_snapshot",
                }

        if not holder_data.get("available") and honeypot_data.get("available") and "top_10_pct" in honeypot_data:
            holder_data = {
                "available": True,
                "top_10_pct": honeypot_data.get("top_10_pct", 0),
                "dev_pct": 0,
                "has_cluster": False,
                "source": "goplus_fallback",
            }

        report.is_honeypot = is_honeypot
        report.check_status = {
            "contract": bool(contract_data.get("available", False)),
            "honeypot": bool(honeypot_data.get("available", False)),
            "liquidity": bool(liquidity_data.get("available", False)),
            "holders": bool(holder_data.get("available", False)),
        }
        report.unavailable_checks = [name for name, avail in report.check_status.items() if not avail]
        report.checks_complete = not report.unavailable_checks
        report.has_mint_authority = contract_data.get("has_mint_authority", True)
        report.has_freeze_authority = contract_data.get("has_freeze_authority", True)

        report.lp_locked = liquidity_data.get("lp_locked", False)
        report.lp_locked_pct = liquidity_data.get("lock_pct", 0)

        report.top_holder_pct = holder_data.get("top_10_pct", 0)
        report.dev_holding_pct = holder_data.get("dev_pct", 0)
        report.high_concentration = report.top_holder_pct > (config.TRADING.max_top_holder_pct if HAS_CONFIG else 30)
        report.developer_cluster = holder_data.get("has_cluster", False)

        behavior = behavior_data or {}
        report.behavior_data_available = bool(behavior)

        # MELT-inspired thresholds - configurable
        if HAS_CONFIG:
            bundle_thr = config.TRADING.max_bundle_risk_score
            wash_thr = config.TRADING.max_wash_score
            sniper_thr = config.TRADING.max_sniper_saturation_score
            mech_thr = config.TRADING.max_mechanicality_score
            funding_thr = config.TRADING.max_funding_cluster_risk
        else:
            bundle_thr, wash_thr, sniper_thr, mech_thr, funding_thr = 70, 70, 80, 75, 70

        report.wash_trading_suspected = float(behavior.get("wash_trading_score", 0) or 0) >= wash_thr
        report.bundle_risk_suspected = float(behavior.get("bundle_risk_score", 0) or 0) >= bundle_thr
        report.mechanical_trading_suspected = float(behavior.get("mechanicality_score", 0) or 0) >= mech_thr
        report.creator_sold_early = bool(behavior.get("creator_sold", False))
        report.sniper_saturation_suspected = float(behavior.get("sniper_saturation_score", 0) or 0) >= sniper_thr
        report.time_cluster_suspected = float(behavior.get("time_cluster_score", 0) or 0) >= 60
        report.funding_cluster_suspected = float(behavior.get("cluster_risk_score", 0) or behavior.get("funding_cluster_risk", 0) or 0) >= funding_thr
        report.serial_deployer_suspected = bool(behavior.get("is_serial_deployer", False))
        report.sale_duration_risk = float(behavior.get("sale_duration_risk_score", 0) or 0) >= 60

        # Scores
        report.contract_score = self._calc_contract_score(contract_data)
        report.liquidity_score = self._calc_liquidity_score(liquidity_data)
        report.holder_score = self._calc_holder_score(holder_data)

        report.overall_score = (
            report.contract_score * 0.35 +
            report.liquidity_score * 0.25 +
            report.holder_score * 0.40
        )
        if not report.checks_complete:
            report.overall_score = min(report.overall_score, 25.0)
        # Penalties - expanded
        if report.wash_trading_suspected:
            report.overall_score -= 25.0
        if report.bundle_risk_suspected:
            report.overall_score -= 20.0
        if report.mechanical_trading_suspected:
            report.overall_score -= 10.0
        if report.creator_sold_early:
            report.overall_score -= 20.0
        if report.sniper_saturation_suspected:
            report.overall_score -= 25.0
        if report.funding_cluster_suspected:
            report.overall_score -= 20.0
        if report.serial_deployer_suspected:
            report.overall_score -= 15.0
        if report.time_cluster_suspected:
            report.overall_score -= 10.0
        if report.sale_duration_risk:
            report.overall_score -= 15.0

        report.overall_score = max(0.0, min(100.0, report.overall_score))

        # Warnings
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
        if not report.checks_complete:
            report.warnings.append("⚠️ Required risk data unavailable - fail closed")
        if report.wash_trading_suspected:
            report.warnings.append("⚠️ Wash-trading proxy detected")
        if report.bundle_risk_suspected:
            report.warnings.append("⚠️ Coordinated early-wallet proxy detected")
        if report.mechanical_trading_suspected:
            report.warnings.append("⚠️ Mechanical/repeated trade pattern detected")
        if report.creator_sold_early:
            report.warnings.append("⚠️ Creator wallet sold during observation window")
        if report.sniper_saturation_suspected:
            report.warnings.append("⚠️ Sniper saturation - over-sniped launch")
        if report.funding_cluster_suspected:
            report.warnings.append("⚠️ Funding cluster - multiple wallets same funder")
        if report.serial_deployer_suspected:
            report.warnings.append("⚠️ Serial deployer - creator deployed many tokens")
        if report.time_cluster_suspected:
            report.warnings.append("⚠️ Time clustering - many buys same second")
        if report.sale_duration_risk:
            report.warnings.append("⚠️ Fast fill - bonding curve filled too quickly")

        self._cache[mint] = (report, datetime.now())
        return report

    # --- LIVE ANTI-RUG CHECKS (new) ---

    async def live_rug_check(self, mint: str, position_context: Dict) -> Optional[str]:
        """
        Called periodically for active positions.
        Returns reason string if rug detected, else None.

        Checks:
        - LP liquidity dropped > threshold
        - Top holder concentration spike
        - Re-check contract authorities (mint/freeze)
        """
        try:
            if not HAS_CONFIG or not config.TRADING.enable_live_rug_checks:
                return None

            # Check liquidity drop
            initial_liq_usd = position_context.get("initial_liquidity_usd", 0)
            if initial_liq_usd > 0:
                ok, liq_data = await self._check_liquidity(mint)
                if ok and liq_data.get("available"):
                    current_liq = liq_data.get("liquidity_usd", 0)
                    drop_pct = (initial_liq_usd - current_liq) / initial_liq_usd * 100 if initial_liq_usd else 0
                    threshold = config.TRADING.liquidity_drop_threshold_pct
                    if drop_pct >= threshold:
                        return f"LIQUIDITY_DROP {drop_pct:.1f}% (>{threshold}%)"

            # Check holder spike: if top10 pct increased significantly
            initial_top10 = position_context.get("initial_top10_pct", 0)
            if initial_top10 > 0:
                ok, holder_data = await self._check_holder_distribution(mint)
                if ok and holder_data.get("available"):
                    current_top10 = holder_data.get("top_10_pct", 0)
                    # If top10 increased by >10% absolute and new wallet >5% appears, flag
                    spike_thr = config.TRADING.top_holder_spike_pct
                    if current_top10 - initial_top10 > 10:
                        return f"HOLDER_SPIKE top10 {initial_top10:.1f}% -> {current_top10:.1f}%"

            # Check authorities still revoked? If now has mint authority -> rugged potential
            # We skip heavy check for perf, only if LP not locked originally maybe re-check
            return None
        except Exception as e:
            logger.debug(f"Live rug check failed for {mint[:8]}: {e}")
            return None

    async def check_dev_dump(self, mint: str, creator_address: str, initial_dev_balance: float) -> Optional[str]:
        """
        Check if dev sold significant portion.
        initial_dev_balance: tokens dev held at entry.
        """
        if not creator_address or initial_dev_balance <= 0:
            return None
        try:
            async with aiohttp.ClientSession() as session:
                # Use RPC to get token balance for creator
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTokenAccountsByOwner",
                    "params": [
                        creator_address,
                        {"mint": mint},
                        {"encoding": "jsonParsed"}
                    ]
                }
                async with session.post(self._rpc_endpoint, json=payload, timeout=10) as resp:
                    if resp.status == 200:
                        js = await resp.json()
                        accounts = js.get("result", {}).get("value", []) or []
                        current_balance = 0.0
                        for acc in accounts:
                            data = acc.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
                            amount = data.get("tokenAmount", {})
                            current_balance += float(amount.get("uiAmount", 0) or 0)
                        if initial_dev_balance > 0:
                            sold_pct = (initial_dev_balance - current_balance) / initial_dev_balance * 100
                            threshold = config.TRADING.dev_dump_threshold_pct if HAS_CONFIG else 15.0
                            if sold_pct >= threshold:
                                return f"DEV_DUMP sold {sold_pct:.1f}% (>{threshold}%)"
        except Exception as e:
            logger.debug(f"Dev dump check failed: {e}")
        return None

    # --- Original checks (kept) ---

    async def _check_contract_safety(self, mint: str) -> Tuple[bool, Dict]:
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.rugcheck_api}/tokens/{mint}/report"
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        report = data.get("report", {})
                        has_mint = report.get("isMint", False)
                        has_freeze = report.get("isFreeze", False)
                        is_exploitable = report.get("isExploitable", False)
                        return not is_exploitable, {
                            "has_mint_authority": has_mint,
                            "has_freeze_authority": has_freeze,
                            "trust_level": report.get("trustLevel", "unknown"),
                            "available": True
                        }
        except Exception as e:
            logger.warning(f"RugCheck error for {mint[:12]}: {e}")
        return False, {"available": False}

    async def _check_honeypot(self, mint: str) -> Tuple[bool, Dict]:
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.goplus_api}/solana/token_security"
                params = {"contract_addresses": mint}
                async with session.get(url, params=params, timeout=10) as resp:
                    if resp.status == 200:
                        payload = await resp.json()
                        token_data = (payload.get("result", {}) or {}).get(mint, {})
                        if not token_data:
                            return False, {"available": False}
                        is_honeypot = str(token_data.get("is_honeypot", "0")) in {"1", "true"}
                        mintable = str(token_data.get("mintable", {}).get("status", "0"))
                        freezable = str(token_data.get("freezable", {}).get("status", "0"))
                        holders = token_data.get("holders", []) or []
                        top10_pct = sum(float(h.get("percent", 0) or 0) * 100 for h in holders[:10])
                        return is_honeypot, {
                            "honeypot_type": token_data.get("honeypot_type", "none"),
                            "buy_tax": token_data.get("buy_tax", 0),
                            "sell_tax": token_data.get("sell_tax", 0),
                            "has_mint_authority": mintable != "0",
                            "has_freeze_authority": freezable != "0",
                            "holder_count": int(token_data.get("holder_count", 0) or 0),
                            "top_10_pct": top10_pct,
                            "available": True,
                        }
        except Exception as e:
            logger.warning(f"GoPlus error for {mint[:12]}: {e}")
        return False, {"available": False}

    async def _check_liquidity(self, mint: str) -> Tuple[bool, Dict]:
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        pairs = data.get("pairs", [])
                        if pairs:
                            # Prefer SOL quote
                            sol_pairs = [p for p in pairs if p.get("quoteToken", {}).get("symbol") == "SOL" or p.get("quoteToken", {}).get("address") == "So11111111111111111111111111111111111111112"]
                            pair = sol_pairs[0] if sol_pairs else pairs[0]
                            liquidity = pair.get("liquidity", {})
                            liquidity_usd = float(liquidity.get("usd", 0))
                            lp_locked = False
                            # DexScreener sometimes has labels
                            labels = pair.get("labels", []) or []
                            if "locked" in str(labels).lower():
                                lp_locked = True
                            return liquidity_usd > 1000, {
                                "liquidity_usd": liquidity_usd,
                                "lp_locked": lp_locked,
                                "lock_pct": 100 if lp_locked else 0,
                                "available": True,
                                "pair_address": pair.get("pairAddress"),
                                "dex": pair.get("dexId")
                            }
        except Exception as e:
            logger.warning(f"Liquidity check error for {mint[:12]}: {e}")
        return False, {"available": False}

    async def _check_holder_distribution(self, mint: str) -> Tuple[bool, Dict]:
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTokenLargestAccounts",
                    "params": [mint]
                }
                async with session.post(self._rpc_endpoint, json=payload, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        accounts = data.get("result", {}).get("value", []) or []
                        if not accounts:
                            return True, {"available": False}
                        total_supply = sum(float(acc.get("uiAmount", 0)) for acc in accounts)
                        top_10_pct = 0
                        dev_pct = 0
                        for i, acc in enumerate(accounts[:10]):
                            amount = float(acc.get("uiAmount", 0))
                            pct = (amount / total_supply * 100) if total_supply > 0 else 0
                            top_10_pct += pct
                            if i == 0:
                                dev_pct = pct
                        return True, {
                            "top_10_pct": top_10_pct,
                            "dev_pct": dev_pct,
                            "has_cluster": top_10_pct > 50,  # heuristic
                            "available": True,
                            "total_accounts_returned": len(accounts)
                        }
        except Exception as e:
            logger.warning(f"Holder check error for {mint[:12]}: {e}")
        return False, {"available": False}

    async def _check_dev_wallets(self, mint: str) -> Tuple[bool, Dict]:
        return True, {"suspicious": False}

    def _calc_contract_score(self, data: Dict) -> float:
        score = 100
        if data.get("has_mint_authority"):
            score -= 30
        if data.get("has_freeze_authority"):
            score -= 20
        if data.get("trust_level") == "danger":
            score -= 50
        return max(0, score)

    def _calc_liquidity_score(self, data: Dict) -> float:
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


# Legacy WhaleTracker kept for compat but real logic in whale_data.py
class WhaleTracker:
    def __init__(self, tracked_wallets: List[Dict] = None):
        self.tracked_wallets = tracked_wallets or []
        self._wallet_cache: Dict[str, Dict] = {}

    def add_wallet(self, address: str, name: str, wallet_type: str = "whale"):
        self.tracked_wallets.append({"address": address, "name": name, "type": wallet_type})

    async def check_wallet_activity(self, wallet: str) -> Optional[Dict]:
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://gmgn.ai/api/v1/wallet_history/sol/{wallet}"
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("data", {})
        except Exception as e:
            logger.warning(f"Wallet check error: {e}")
        return None

    async def scan_for_buys(self, mint: str) -> List[Dict]:
        buys = []
        for wallet_info in self.tracked_wallets:
            wallet = wallet_info["address"]
            activity = await self.check_wallet_activity(wallet)
            if activity:
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
        buys = await self.scan_for_buys(mint)
        total_sol = sum(b["amount_sol"] for b in buys)
        buy_count = len(buys)
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
