"""
MEME HUNTER - Utility Scripts
============================
Helper functions and standalone tools for the meme trading bot.
"""

import asyncio
import aiohttp
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def check_token_safety(mint: str) -> Dict:
    """
    Quick token safety check using multiple APIs
    """
    results = {
        "mint": mint,
        "timestamp": datetime.now().isoformat(),
        "checks": {}
    }

    async with aiohttp.ClientSession() as session:
        # RugCheck
        try:
            url = f"https://api.rugcheck.xyz/v1/tokens/{mint}/reports"
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results["checks"]["rugcheck"] = {
                        "status": "passed" if not data.get("isExploitable") else "failed",
                        "details": data
                    }
        except Exception as e:
            results["checks"]["rugcheck"] = {"status": "error", "error": str(e)}

        # GoPlus Token Security
        try:
            url = f"https://api.gopluslabs.io/api/v1/token-security/{mint}"
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results["checks"]["goplus"] = {
                        "status": "passed" if not data.get("is_honeypot") else "failed",
                        "is_honeypot": data.get("is_honeypot", False),
                        "buy_tax": data.get("buy_tax", 0),
                        "sell_tax": data.get("sell_tax", 0)
                    }
        except Exception as e:
            results["checks"]["goplus"] = {"status": "error", "error": str(e)}

    return results


async def get_whale_wallets(chain: str = "solana", min_pnl: float = 10) -> List[Dict]:
    """
    Get top performing whale wallets from GMGN
    """
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://gmgn.ai/api/v1/wallets/{chain}"
            params = {"min_pnl": min_pnl, "sort_by": "pnl_7d", "order": "desc"}

            async with session.get(url, params=params, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("data", {}).get("wallets", [])
    except Exception as e:
        logger.error(f"Failed to get whale wallets: {e}")

    return []


async def get_pump_fun_trending(limit: int = 20) -> List[Dict]:
    """
    Get trending tokens on Pump.fun
    """
    try:
        async with aiohttp.ClientSession() as session:
            url = "https://api.pump.fun/coins"
            params = {"limit": limit, "sort": "volume"}

            async with session.get(url, params=params, timeout=10) as resp:
                if resp.status == 200:
                    return await resp.json()
    except Exception as e:
        logger.error(f"Failed to get trending: {e}")

    return []


async def analyze_holder_distribution(mint: str) -> Dict:
    """
    Analyze token holder distribution
    """
    try:
        async with aiohttp.ClientSession() as session:
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
                        return {"error": "No holders found"}

                    total = sum(float(acc.get("uiAmount", 0)) for acc in accounts)
                    top_10 = sum(float(acc.get("uiAmount", 0)) for acc in accounts[:10])
                    top_5 = sum(float(acc.get("uiAmount", 0)) for acc in accounts[:5])

                    return {
                        "total_holders": len(accounts),
                        "top_10_pct": (top_10 / total * 100) if total > 0 else 0,
                        "top_5_pct": (top_5 / total * 100) if total > 0 else 0,
                        "holders": [
                            {
                                "address": acc.get("address"),
                                "amount": acc.get("uiAmount"),
                                "pct": (float(acc.get("uiAmount", 0)) / total * 100) if total > 0 else 0
                            }
                            for acc in accounts[:10]
                        ]
                    }
    except Exception as e:
        logger.error(f"Failed to analyze holders: {e}")

    return {"error": str(e)}


async def get_dexscreener_pairs(filters: Dict = None) -> List[Dict]:
    """
    Get DexScreener pairs with filters
    """
    defaults = {
        "chain": "solana",
        "sort": "volume",
        "order": "desc",
        "limit": 50
    }
    defaults.update(filters or {})

    try:
        async with aiohttp.ClientSession() as session:
            url = "https://api.dexscreener.com/latest/dex/pairs"

            async with session.get(url, params=defaults, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("pairs", [])
    except Exception as e:
        logger.error(f"Failed to get pairs: {e}")

    return []


async def calculate_potential(token_data: Dict) -> Dict:
    """
    Calculate profit potential for a token
    """
    market_cap = token_data.get("market_cap_sol", 0)
    liquidity = token_data.get("liquidity_sol", 0)

    # Estimate potential based on market cap
    if market_cap == 0:
        return {"potential": "unknown", "reason": "No market cap data"}

    # If mcap is very low, high potential
    if market_cap < 10000:
        potential_multiplier = "100x+"
    elif market_cap < 50000:
        potential_multiplier = "50x"
    elif market_cap < 100000:
        potential_multiplier = "20x"
    elif market_cap < 500000:
        potential_multiplier = "10x"
    elif market_cap < 1000000:
        potential_multiplier = "5x"
    else:
        potential_multiplier = "2x"

    # Risk assessment
    if liquidity < 1000:
        risk = "EXTREME"
    elif liquidity < 10000:
        risk = "HIGH"
    elif liquidity < 50000:
        risk = "MEDIUM"
    else:
        risk = "LOW"

    return {
        "potential_multiplier": potential_multiplier,
        "risk_level": risk,
        "market_cap_sol": market_cap,
        "liquidity_sol": liquidity,
        "liquidity_ratio": (liquidity / market_cap * 100) if market_cap > 0 else 0
    }


async def monitor_wallet(wallet_address: str, limit: int = 10) -> Dict:
    """
    Monitor a specific wallet's recent trades
    """
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://gmgn.ai/api/v1/wallet_history/sol/{wallet_address}"
            params = {"limit": limit}

            async with session.get(url, params=params, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        "wallet": wallet_address,
                        "trades": data.get("data", {}).get("trades", []),
                        "total_pnl": data.get("data", {}).get("total_pnl", 0),
                        "win_rate": data.get("data", {}).get("win_rate", 0)
                    }
    except Exception as e:
        logger.error(f"Failed to monitor wallet: {e}")

    return {"error": "Failed to fetch wallet data"}


async def scan_for_alpha():
    """
    Scan for alpha opportunities - tokens with strong whale activity
    """
    logger.info("🔍 Scanning for alpha opportunities...")

    opportunities = []

    # Get trending tokens
    trending = await get_pump_fun_trending(20)

    for token in trending[:10]:
        mint = token.get("mint")
        if not mint:
            continue

        # Quick safety check
        safety = await check_token_safety(mint)
        if safety.get("checks", {}).get("rugcheck", {}).get("status") == "failed":
            continue

        # Get holder analysis
        holders = await analyze_holder_distribution(mint)

        # Calculate potential
        potential = await calculate_potential({
            "market_cap_sol": token.get("market_cap", 0) / 200,
            "liquidity_sol": token.get("usd_market_cap", 0) / 400  # Rough estimate
        })

        opportunities.append({
            "token": token,
            "safety": safety,
            "holders": holders,
            "potential": potential
        })

    # Sort by potential
    opportunities.sort(
        key=lambda x: x["potential"]["potential_multiplier"],
        reverse=True
    )

    return opportunities


async def main():
    """Run utility tests"""
    print("=" * 60)
    print("MEME HUNTER - Utility Tools")
    print("=" * 60)

    # Example: Scan for alpha
    print("\n🔍 Scanning for alpha opportunities...")
    alpha = await scan_for_alpha()

    for i, opp in enumerate(alpha[:5], 1):
        print(f"\n{i}. {opp['token'].get('name', 'Unknown')}")
        print(f"   Potential: {opp['potential']['potential_multiplier']}")
        print(f"   Risk: {opp['potential']['risk_level']}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
