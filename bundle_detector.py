"""
MEME HUNTER - Bundle & Manipulation Detection
==============================================
Focuses on selectivity, not speed.

Implements MELT-inspired features that are feasible without full archive node:

1. Funding cluster heuristic (same funder / same-second buys)
2. Time clustering & sniper saturation
3. Sale duration (fast fill of bonding curve = high risk)
4. Mechanical / wash trading enhancement
5. Serial deployer detection via public APIs

This module is pure Python, no extra dependencies, optional Helius key for deeper funding graph.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Set, Tuple, Optional

import aiohttp

logger = logging.getLogger(__name__)

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "").strip()
RPC_ENDPOINT = os.getenv("RPC_ENDPOINT", "https://api.mainnet-beta.solana.com")


def _parse_trades_for_analysis(data: Dict) -> List[Dict]:
    """
    data comes from PumpPortalScanner _token_buffer:
    {
      "trades": [{"trader": str, "is_buy": bool, "sol_amount": float, "timestamp": datetime}, ...],
      "traders": set(),
      "buy_traders": set(),
      ...
    }
    """
    trades = data.get("trades", [])
    # Normalize timestamps to datetime if needed
    normalized = []
    for t in trades:
        ts = t.get("timestamp")
        if not isinstance(ts, datetime):
            ts = datetime.now()
        normalized.append({
            "trader": t.get("trader", ""),
            "is_buy": bool(t.get("is_buy", False)),
            "sol_amount": float(t.get("sol_amount", 0) or 0),
            "timestamp": ts
        })
    return normalized


def _seconds_bucket(dt: datetime) -> int:
    return int(dt.timestamp())  # second resolution


def analyze_launch_behavior(data: Dict) -> Dict:
    """
    MELT-inspired launch behavior analysis from buffered trades.
    Returns scores 0-100 and additional metrics.
    """
    trades = _parse_trades_for_analysis(data)
    if not trades:
        return {
            "wash_trading_score": 0.0,
            "mechanicality_score": 0.0,
            "bundle_risk_score": 0.0,
            "sniper_saturation_score": 0.0,
            "time_cluster_score": 0.0,
            "sale_duration_seconds": 0.0,
            "early_concentration_score": 0.0,
            "unique_trader_ratio": 0.0,
        }

    buys = [t for t in trades if t["is_buy"]]
    sells = [t for t in trades if not t["is_buy"]]

    buy_traders: Set[str] = set(data.get("buy_traders", set()) or {t["trader"] for t in buys if t["trader"]})
    sell_traders: Set[str] = set(data.get("sell_traders", set()) or {t["trader"] for t in sells})
    all_traders: Set[str] = set(data.get("traders", set()) or {t["trader"] for t in trades if t["trader"]})

    # --- Wash: overlap buy/sell same wallet ---
    overlap = buy_traders & sell_traders
    wash_score = 0.0
    if buy_traders and sell_traders:
        wash_score = len(overlap) / min(len(buy_traders), len(sell_traders)) * 100.0

    # --- Mechanical: repeated SOL amounts ---
    amount_counter = Counter(round(t["sol_amount"], 4) for t in trades if t["sol_amount"] > 0)
    mechanical_score = 0.0
    if len(trades) >= 4 and amount_counter:
        most_common = amount_counter.most_common(1)[0][1]
        mechanical_score = (most_common / len(trades)) * 100.0

    # --- Bundle: top3 buyer share + time clustering ---
    buy_counts = Counter(t["trader"] for t in buys if t["trader"])
    top3_share = 0.0
    if len(buys) >= 3 and buy_counts:
        top3 = sum(c for _, c in buy_counts.most_common(3))
        top3_share = top3 / len(buys) * 100.0

    # Time clustering: many buys in same second
    second_buckets = Counter(_seconds_bucket(t["timestamp"]) for t in buys)
    time_cluster_score = 0.0
    if buys and second_buckets:
        max_same_sec = max(second_buckets.values())
        time_cluster_score = (max_same_sec / len(buys)) * 100.0

    # Bundle risk = max of top3 concentration and time clustering, weighted
    bundle_risk = max(top3_share, time_cluster_score * 0.9)
    # If both high, boost
    if top3_share > 40 and time_cluster_score > 40:
        bundle_risk = min(100.0, bundle_risk * 1.2)

    # --- Sniper saturation: first N trades all within short window ---
    sniper_saturation_score = 0.0
    if len(trades) >= 10:
        first_10 = sorted(trades, key=lambda x: x["timestamp"])[:10]
        span_first_10 = (first_10[-1]["timestamp"] - first_10[0]["timestamp"]).total_seconds()
        if span_first_10 <= 3.0:
            sniper_saturation_score = 85.0
        elif span_first_10 <= 10.0:
            sniper_saturation_score = 60.0
        elif span_first_10 <= 30.0:
            sniper_saturation_score = 30.0

        # Also check if first 20 trades are all buys from distinct wallets but same time → bot swarm
        if len(trades) >= 20:
            first_20 = sorted(trades, key=lambda x: x["timestamp"])[:20]
            span_first_20 = (first_20[-1]["timestamp"] - first_20[0]["timestamp"]).total_seconds()
            buy_ratio_first_20 = sum(1 for t in first_20 if t["is_buy"]) / 20.0
            if span_first_20 <= 5.0 and buy_ratio_first_20 >= 0.9:
                sniper_saturation_score = max(sniper_saturation_score, 90.0)

    # --- Sale duration: fast filling bonding curve = high risk (MELT) ---
    timestamps = [t["timestamp"] for t in trades]
    sale_duration = 0.0
    if timestamps:
        sale_duration = (max(timestamps) - min(timestamps)).total_seconds()

    # Fast duration penalty: if we observed 20+ trades in <10 sec, likely over-sniped
    sale_duration_risk = 0.0
    if len(trades) >= 20 and sale_duration > 0 and sale_duration < 10:
        sale_duration_risk = 80.0
    elif len(trades) >= 20 and sale_duration < 30:
        sale_duration_risk = 50.0

    # --- Unique trader ratio: low ratio = few wallets churning ---
    unique_ratio = 0.0
    if trades and all_traders:
        unique_ratio = len(all_traders) / len(trades)  # 1.0 = all unique per trade, low = same wallets
    early_concentration = 0.0
    if unique_ratio > 0:
        early_concentration = (1.0 - min(1.0, unique_ratio)) * 100.0  # high when same wallets repeated

    # Combine some signals
    return {
        "wash_trading_score": round(min(100.0, wash_score), 2),
        "mechanicality_score": round(min(100.0, mechanical_score), 2),
        "bundle_risk_score": round(min(100.0, bundle_risk), 2),
        "sniper_saturation_score": round(min(100.0, sniper_saturation_score), 2),
        "time_cluster_score": round(min(100.0, time_cluster_score), 2),
        "sale_duration_seconds": round(sale_duration, 2),
        "sale_duration_risk_score": round(sale_duration_risk, 2),
        "early_concentration_score": round(min(100.0, early_concentration), 2),
        "unique_trader_ratio": round(unique_ratio, 3),
        "observed_traders": len(all_traders),
        "early_buyer_count": len(buy_traders),
        "trade_count": len(trades),
    }


async def fetch_funding_source(wallet: str, session: aiohttp.ClientSession, rpc_http: str = RPC_ENDPOINT, helius_key: str = HELIUS_API_KEY) -> Optional[str]:
    """
    Try to get the funding source of a wallet via Helius or public RPC.
    Returns the source wallet address if found, else None.
    Optional – works best with HELIUS_API_KEY set.
    """
    try:
        if helius_key:
            # Helius enhanced transactions for first funding
            url = f"https://api.helius.xyz/v0/addresses/{wallet}/transactions?api-key={helius_key}&limit=10&type=TRANSFER"
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list) and data:
                        # Find first inbound SOL transfer
                        for tx in reversed(data):  # oldest first
                            # Helius format: nativeTransfers
                            for nt in tx.get("nativeTransfers", []) or []:
                                if nt.get("toUserAccount") == wallet and nt.get("fromUserAccount") != wallet:
                                    amt = nt.get("amount", 0) / 1e9
                                    if amt >= 0.01:  # ignore dust
                                        return nt.get("fromUserAccount")
        else:
            # Fallback: use public RPC getSignaturesForAddress + getTransaction for first tx
            payload = {
                "jsonrpc": "2.0", "id": 1,
                "method": "getSignaturesForAddress",
                "params": [wallet, {"limit": 5}]
            }
            async with session.post(rpc_http, json=payload, timeout=10) as resp:
                if resp.status == 200:
                    js = await resp.json()
                    sigs = js.get("result", []) or []
                    if sigs:
                        oldest = sigs[-1]
                        sig = oldest.get("signature")
                        if sig:
                            payload2 = {
                                "jsonrpc": "2.0", "id": 1,
                                "method": "getTransaction",
                                "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
                            }
                            async with session.post(rpc_http, json=payload2, timeout=10) as resp2:
                                if resp2.status == 200:
                                    tx_data = await resp2.json()
                                    # Very simplified parsing – look for first account that is not wallet
                                    result = tx_data.get("result", {})
                                    if result:
                                        message = result.get("transaction", {}).get("message", {})
                                        keys = message.get("accountKeys", []) or []
                                        # Return first key that is not wallet if exists
                                        for k in keys:
                                            addr = k.get("pubkey") if isinstance(k, dict) else k
                                            if addr and addr != wallet:
                                                # Heuristic: first signer often funder
                                                return addr
    except Exception as e:
        logger.debug(f"Funding source fetch failed for {wallet[:8]}: {e}")
    return None


async def detect_funding_clusters(wallets: List[str], max_wallets: int = 15) -> Dict:
    """
    Detect if multiple early buyers were funded by same source.
    Returns cluster summary with risk score.
    """
    if not wallets:
        return {"cluster_risk_score": 0.0, "clusters": {}, "wallet_to_funder": {}}

    # Limit to avoid RPC spam
    check_wallets = wallets[:max_wallets]
    wallet_to_funder: Dict[str, str] = {}
    funder_to_wallets: Dict[str, List[str]] = defaultdict(list)

    async with aiohttp.ClientSession() as session:
        tasks = [fetch_funding_source(w, session) for w in check_wallets]
        funders = await asyncio.gather(*tasks, return_exceptions=True)
        for wallet, funder in zip(check_wallets, funders):
            if isinstance(funder, Exception) or not funder:
                continue
            wallet_to_funder[wallet] = funder
            funder_to_wallets[funder].append(wallet)

    # Find largest cluster funded by same source
    max_cluster_size = 0
    largest_funder = None
    for funder, ws in funder_to_wallets.items():
        if len(ws) > max_cluster_size:
            max_cluster_size = len(ws)
            largest_funder = funder

    cluster_risk = 0.0
    if max_cluster_size >= 2:
        ratio = max_cluster_size / len(check_wallets) * 100.0
        # If 30%+ of early buyers share funder → high risk
        if ratio >= 50:
            cluster_risk = 90.0
        elif ratio >= 30:
            cluster_risk = 70.0
        elif ratio >= 20:
            cluster_risk = 40.0

    return {
        "cluster_risk_score": cluster_risk,
        "max_cluster_size": max_cluster_size,
        "largest_funder": largest_funder,
        "clusters": dict(funder_to_wallets),
        "wallet_to_funder": wallet_to_funder,
        "checked_wallets": len(check_wallets)
    }


async def check_serial_deployer(creator_address: str) -> Dict:
    """
    Check if creator deployed many tokens recently (serial deployer = high risk).
    Uses RugCheck API or Pump.fun API.
    """
    if not creator_address or len(creator_address) < 32:
        return {"is_serial_deployer": False, "deployed_count_recent": 0, "risk_score": 0.0}

    try:
        async with aiohttp.ClientSession() as session:
            # Try RugCheck v1 report sometimes includes deployer history, fallback to Pump.fun
            # Pump.fun API: https://api.pump.fun/coins?creator={creator}
            url = f"https://api.pump.fun/coins/creator/{creator_address}"
            # Actually Pump.fun public API: /coins?creator=...
            # Use generic endpoint
            url2 = f"https://api.pump.fun/coins?creator={creator_address}&limit=20&sort=createdAt"
            async with session.get(url2, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    coins = data if isinstance(data, list) else data.get("coins", []) if isinstance(data, dict) else []
                    # Count recent within 7 days
                    cutoff = datetime.now() - timedelta(days=7)
                    recent = 0
                    for c in coins[:20]:
                        created = c.get("createdAt") or c.get("created_at") or c.get("created_timestamp")
                        try:
                            if isinstance(created, (int, float)):
                                # ms timestamp
                                dt = datetime.fromtimestamp(created/1000.0 if created>1e12 else created)
                            else:
                                dt = datetime.fromisoformat(str(created).replace("Z", "+00:00")).replace(tzinfo=None)
                            if dt >= cutoff:
                                recent += 1
                        except Exception:
                            # If can't parse, assume recent if we got coins
                            recent += 1
                    is_serial = recent >= 3
                    risk = 0.0
                    if recent >= 10:
                        risk = 90.0
                    elif recent >= 5:
                        risk = 60.0
                    elif recent >= 3:
                        risk = 35.0
                    return {
                        "is_serial_deployer": is_serial,
                        "deployed_count_recent": recent,
                        "risk_score": risk,
                        "total_found": len(coins)
                    }
    except Exception as e:
        logger.debug(f"Serial deployer check failed for {creator_address[:8]}: {e}")

    return {"is_serial_deployer": False, "deployed_count_recent": 0, "risk_score": 0.0}


# Compatibility wrapper for old _behavior_summary callers
def legacy_behavior_summary(data: Dict) -> Dict:
    """
    Drop-in replacement for old PumpPortalScanner._behavior_summary but using new analyzer.
    """
    analysis = analyze_launch_behavior(data)
    # Map to old keys for backward compat
    result = {
        "observed_traders": analysis["observed_traders"],
        "early_buyer_count": analysis["early_buyer_count"],
        "wash_trading_score": analysis["wash_trading_score"],
        "mechanicality_score": analysis["mechanicality_score"],
        "bundle_risk_score": analysis["bundle_risk_score"],
        "sniper_saturation_score": analysis["sniper_saturation_score"],
        "time_cluster_score": analysis["time_cluster_score"],
        "sale_duration_seconds": analysis["sale_duration_seconds"],
        "sale_duration_risk_score": analysis["sale_duration_risk_score"],
        "early_concentration_score": analysis["early_concentration_score"],
        "unique_trader_ratio": analysis["unique_trader_ratio"],
        "creator_sold": bool(data.get("creator_sold", False)),
        "data_quality": "observed" if data.get("traders") else "limited",
        "dev_buy_known": True,
    }
    return result
