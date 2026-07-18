"""
MEME HUNTER - Example Usage
===========================
Simple examples demonstrating how to use the bot components.
"""

import asyncio
import json
from meme_hunter_bot import MemeHunterBot, PaperTrader
from token_scanner import ScanFilters
from risk_analyzer import RiskAnalyzer, WhaleTracker
from trading_engine import TradingEngine, MomentumDetector
from solana_client import SolanaTradingClient
from config import config, TradingConfig


async def example_basic_analysis():
    """
    Example 1: Basic token analysis
    Analyze a single token for safety and potential
    """
    print("=" * 60)
    print("Example 1: Basic Token Analysis")
    print("=" * 60)

    # Token mint to analyze
    test_mint = "ExampleTokenMintAddress..."

    # Initialize risk analyzer
    risk_analyzer = RiskAnalyzer()

    # Run analysis
    report = await risk_analyzer.analyze(test_mint)

    print(f"\nRisk Report for {test_mint[:16]}...")
    print(f"  Overall Score: {report.overall_score:.1f}/100")
    print(f"  Contract Score: {report.contract_score:.1f}")
    print(f"  Liquidity Score: {report.liquidity_score:.1f}")
    print(f"  Holder Score: {report.holder_score:.1f}")
    print(f"  Recommendation: {report.get_recommendation()}")

    if report.warnings:
        print("\n  Warnings:")
        for warning in report.warnings:
            print(f"    {warning}")

    return report


async def example_wallet_tracking():
    """
    Example 2: Track whale/KOL wallets
    Monitor smart money activity
    """
    print("\n" + "=" * 60)
    print("Example 2: Whale/KOL Wallet Tracking")
    print("=" * 60)

    # Initialize whale tracker with known wallets
    whale_tracker = WhaleTracker([
        {"address": "wallet1...", "name": "WhaleA", "type": "whale"},
        {"address": "wallet2...", "name": "TraderX", "type": "kol"}
    ])

    # Add more wallets dynamically
    whale_tracker.add_wallet("wallet3...", "InsiderY", "insider")

    # Get smart money flow for a specific token
    test_mint = "TokenToCheck..."
    flow = await whale_tracker.get_smart_money_flow(test_mint)

    print(f"\nSmart Money Analysis for {test_mint[:16]}...")
    print(f"  Total Buys: {flow['total_buys']}")
    print(f"  Total SOL: {flow['total_sol']:.2f}")
    print(f"  Whale Count: {flow['whale_count']}")
    print(f"  KOL Count: {flow['kol_count']}")
    print(f"  Signal: {flow['signal']}")

    if flow['buys']:
        print("\n  Recent Whale Activity:")
        for buy in flow['buys'][:5]:
            print(f"    - {buy['name']}: {buy['amount_sol']:.2f} SOL")


async def example_momentum_detection():
    """
    Example 3: Momentum detection
    Track price momentum to identify pumps
    """
    print("\n" + "=" * 60)
    print("Example 3: Momentum Detection")
    print("=" * 60)

    detector = MomentumDetector()

    # Simulate price data
    prices = [
        0.00001000,  # t=0
        0.00001050,  # t=1 (+5%)
        0.00001120,  # t=2 (+7%)
        0.00001180,  # t=3 (+5%)
        0.00001250,  # t=4 (+6%)
        0.00001320,  # t=5 (+5.6%)
    ]

    # Record prices
    test_mint = "test_token_123"
    for i, price in enumerate(prices):
        detector.record_price(test_mint, price)
        if i > 0:
            # Simulate some volume
            volume = 1000 + (i * 500)  # Increasing volume
            detector.record_volume(test_mint, volume)

    # Wait a bit (would be real-time in production)
    await asyncio.sleep(0.1)

    # Detect pump signal
    signal = detector.detect_pump_signal(test_mint)

    print(f"\nMomentum Analysis for {test_mint}")
    print(f"  Pump Signal: {'YES 🚀' if signal['signal'] else 'NO'}")
    print(f"  Signal Strength: {signal['strength']}/100")
    print(f"  Price Momentum: {signal['price_momentum']*100:.1f}%")
    print(f"  Volume Surge: {'YES' if signal['volume_surge'] else 'NO'}")

    if signal['reasons']:
        print("\n  Reasons:")
        for reason in signal['reasons']:
            print(f"    ✓ {reason}")


async def example_paper_trading():
    """
    Example 4: Paper trading simulation
    Test the bot without real money
    """
    print("\n" + "=" * 60)
    print("Example 4: Paper Trading Simulation")
    print("=" * 60)

    # Initialize paper trader
    paper = PaperTrader()

    # Simulate some trades
    print("\n  Simulating trades...")

    # Simulated token data
    tokens = [
        {"mint": "token1", "symbol": "PEPE", "entry": 0.00001},
        {"mint": "token2", "symbol": "WOJAK", "entry": 0.00005},
        {"mint": "token3", "symbol": "BODEN", "entry": 0.001},
    ]

    # Mock price data
    paper.current_prices = {
        "token1": 0.000015,   # +50%
        "token2": 0.000040,   # -20%
        "token3": 0.002,      # +100%
    }

    # Execute simulated buys
    for token in tokens:
        result = await paper.execute_buy(
            mint=token["mint"],
            symbol=token["symbol"],
            sol_amount=0.1
        )
        if result["success"]:
            print(f"    ✓ Bought {token['symbol']}: {result['tokens_received']:.2f} tokens")

    # Simulate price changes
    print("\n  Price updates applied...")

    # Sell some
    print("\n  Selling partial positions...")
    sell1 = await paper.execute_sell("token3", 50)  # Sell 50%

    # Get balance
    balance = paper.get_paper_balance()

    print(f"\n  Paper Trading Results:")
    print(f"    Balance: {balance['balance']:.4f} SOL")
    print(f"    Positions Value: {balance['positions_value']:.4f} SOL")
    print(f"    Total Value: {balance['total_value']:.4f} SOL")
    print(f"    Total PnL: {balance['pnl_pct']:+.2f}%")
    print(f"    Active Positions: {balance['positions']}")
    print(f"    Total Trades: {balance['trades']}")


async def example_custom_filters():
    """
    Example 5: Custom scanning filters
    Configure your own token discovery criteria
    """
    print("\n" + "=" * 60)
    print("Example 5: Custom Scanning Filters")
    print("=" * 60)

    # Define strict filters for more conservative trading
    strict_filters = ScanFilters(
        min_dev_buy_sol=1.0,      # Only tokens with >1 SOL dev buy
        max_market_cap_sol=30.0,  # Cap at $6k market cap
        min_liquidity_sol=5.0,    # Need at least $1k liquidity
        min_unique_wallets=15,    # More diverse trading
        min_buy_ratio=0.7,        # 70%+ buy ratio
        require_socials=True      # Must have socials
    )

    # Define aggressive filters for more opportunities
    aggressive_filters = ScanFilters(
        min_dev_buy_sol=0.2,      # Lower dev buy threshold
        max_market_cap_sol=100.0, # Higher cap tolerance
        min_liquidity_sol=1.0,    # Lower liquidity
        min_unique_wallets=5,     # Fewer wallets needed
        min_buy_ratio=0.5,        # 50%+ buy ratio
        require_socials=False      # No social requirement
    )

    print("\n  Strict Filters (Conservative):")
    print(f"    Min Dev Buy: {strict_filters.min_dev_buy_sol} SOL")
    print(f"    Max Market Cap: ${strict_filters.max_market_cap_sol * 200:,.0f}")
    print(f"    Min Liquidity: ${strict_filters.min_liquidity_sol * 200:,.0f}")
    print(f"    Min Wallets: {strict_filters.min_unique_wallets}")
    print(f"    Min Buy Ratio: {strict_filters.min_buy_ratio:.0%}")
    print(f"    Require Socials: {strict_filters.require_socials}")

    print("\n  Aggressive Filters (High Opportunity):")
    print(f"    Min Dev Buy: {aggressive_filters.min_dev_buy_sol} SOL")
    print(f"    Max Market Cap: ${aggressive_filters.max_market_cap_sol * 200:,.0f}")
    print(f"    Min Liquidity: ${aggressive_filters.min_liquidity_sol * 200:,.0f}")
    print(f"    Min Wallets: {aggressive_filters.min_unique_wallets}")
    print(f"    Min Buy Ratio: {aggressive_filters.min_buy_ratio:.0%}")
    print(f"    Require Socials: {aggressive_filters.require_socials}")


async def example_config_update():
    """
    Example 6: Update trading configuration
    Modify bot parameters dynamically
    """
    print("\n" + "=" * 60)
    print("Example 6: Configuration Updates")
    print("=" * 60)

    # Current config
    print("\n  Current Trading Config:")
    print(f"    Max Position: {config.TRADING.max_position_per_coin} SOL")
    print(f"    Max Coins: {config.TRADING.max_coins_tracked}")
    print(f"    DCA Entries: {config.TRADING.dca_entries}")
    print(f"    Grid Levels: {config.TRADING.grid_levels}")
    print(f"    Stop Loss: {config.TRADING.stop_loss_pct}%")
    print(f"    Moon Bag: {config.TRADING.moon_bag_pct}%")

    # Example: Make more conservative
    config.TRADING.max_position_per_coin = 0.05
    config.TRADING.stop_loss_pct = 25.0
    config.TRADING.moon_bag_pct = 30.0

    print("\n  Updated Config (Conservative):")
    print(f"    Max Position: {config.TRADING.max_position_per_coin} SOL")
    print(f"    Max Coins: {config.TRADING.max_coins_tracked}")
    print(f"    DCA Entries: {config.TRADING.dca_entries}")
    print(f"    Grid Levels: {config.TRADING.grid_levels}")
    print(f"    Stop Loss: {config.TRADING.stop_loss_pct}%")
    print(f"    Moon Bag: {config.TRADING.moon_bag_pct}%")


async def main():
    """Run all examples"""
    print("\n" + "🧪 " * 20)
    print("MEME HUNTER - Usage Examples")
    print("🧪 " * 20)

    await example_basic_analysis()
    await example_wallet_tracking()
    await example_momentum_detection()
    await example_paper_trading()
    await example_custom_filters()
    await example_config_update()

    print("\n" + "=" * 60)
    print("Examples Complete!")
    print("=" * 60)
    print("\nNext steps:")
    print("1. Configure your wallet in config.json")
    print("2. Add KOL/whale wallets to track")
    print("3. Run: python meme_hunter_bot.py")
    print("\nHappy hunting! 🚀")


if __name__ == "__main__":
    asyncio.run(main())
