"""
MEME HUNTER configuration.

The defaults are intentionally conservative for a one-SOL wallet: at most 10%
of the configured capital is allocated to one token and 20% is kept untouched
as a reserve.  All values can be overridden through the JSON config or the
environment variables read by ``meme_hunter_bot.py``.
"""

from dataclasses import dataclass
from typing import List
import json
import logging


@dataclass
class TradingConfig:
    """Trading, risk and capital-allocation settings."""

    # Wallet settings
    wallet_private_key: str = ""
    rpc_endpoint: str = "https://api.mainnet-beta.solana.com"

    # Capital allocation.  The percentage is applied to the latest observed
    # free SOL balance (or the startup balance before the first refresh).
    allocation_per_meme_pct: float = 10.0
    max_portfolio_allocation_pct: float = 80.0
    min_sol_reserve: float = 0.05
    min_trade_sol: float = 0.005
    max_position_per_coin: float = 0.1
    max_coins_tracked: int = 10
    max_dev_buy_sol: float = 50.0
    min_dev_buy_sol: float = 0.5

    # DCA settings
    dca_entries: int = 3
    dca_spacing_pct: float = 10.0
    dca_increment_mult: float = 1.5
    dca_wait_for_dips: bool = True

    # Grid selling settings
    grid_levels: int = 5
    grid_spacing_pct: float = 15.0
    first_tp_pct: float = 50.0
    moon_bag_pct: float = 20.0

    # Risk management
    stop_loss_pct: float = 30.0
    trailing_stop_activation_pct: float = 50.0
    trailing_stop_distance_pct: float = 15.0
    # Kept as a compatibility alias for older configurations.
    trailing_stop_pct: float = 15.0
    max_slippage_bps: int = 500

    # Paper execution realism. Fees/slippage are deliberately explicit so
    # paper results are not confused with ideal quote returns.
    paper_fee_bps: int = 100
    paper_slippage_bps: int = 50

    # Smart-money confirmation must be convergent by default.
    min_confirming_whales: int = 2

    # Indicators & filters
    min_liquidity_usd: float = 5000.0
    min_buy_ratio: float = 0.6
    min_unique_wallets: int = 10
    max_top_holder_pct: float = 30.0

    # Persistence/learning. These are deliberately separate files: trade
    # history is not mixed with the evolving whale/KOL list.
    trade_history_db_path: str = "trade_history.db"
    wallets_db_path: str = "wallets_list.db"
    # Backwards-compatible alias for older deployments.
    state_db_path: str = "trade_history.db"
    auto_learn_wallets: bool = True
    learned_wallet_min_profit_sol: float = 0.01

    # Paper trading is the safe default until live execution is explicitly
    # enabled. Paper balance is simulated independently of the wallet.
    paper_trading: bool = True
    paper_starting_balance_sol: float = 1.0


@dataclass
class KOLWallet:
    """Key Opinion Leader / whale wallet configuration."""

    address: str
    name: str
    tier: str = "medium"  # whale, kol, insider, learned
    min_buy_sol: float = 0.1
    track_pnl: bool = True
    copy_trade: bool = False


@dataclass
class IndicatorWeights:
    """Weights for scoring meme potential."""

    volume_24h: float = 15.0
    buy_ratio: float = 20.0
    unique_wallets: float = 15.0
    dev_buy: float = 20.0
    social_score: float = 10.0
    holder_growth: float = 10.0
    liquidity: float = 10.0


class Config:
    """Global configuration container."""

    TRADING = TradingConfig()
    KOL_WALLETS: List[KOLWallet] = []
    WEIGHTS = IndicatorWeights()

    DEX_SOURCES = ["dexscreener", "jupiter", "birdeye", "photon"]
    WEBSOCKET_SOURCES = {
        "pump_portal": "wss://pumpportal.fun/api/data",
        "flintr": "wss://api.flintr.io/v1/stream",
        "solana_tracker": "wss://datastream.solanatracker.io",
    }

    LOG_LEVEL = logging.INFO
    LOG_FILE = "meme_hunter.log"

    @classmethod
    def load_from_file(cls, filepath: str):
        """Load configuration from JSON, ignoring unknown template keys."""
        with open(filepath, "r", encoding="utf-8") as handle:
            data = json.load(handle)

        if "trading" in data:
            allowed = set(TradingConfig.__dataclass_fields__)
            cls.TRADING = TradingConfig(
                **{key: value for key, value in data["trading"].items() if key in allowed}
            )

        if "kol_wallets" in data:
            wallets = []
            for item in data["kol_wallets"]:
                # Older templates used `type`; accept it as the tier alias.
                address = str(item.get("address", "")).strip()
                if not address:
                    continue
                wallets.append(
                    KOLWallet(
                        address=address,
                        name=item.get("name", "Unnamed wallet"),
                        tier=item.get("tier", item.get("type", "medium")),
                        min_buy_sol=float(item.get("min_buy_sol", 0.1)),
                        track_pnl=bool(item.get("track_pnl", True)),
                        copy_trade=bool(item.get("copy_trade", False)),
                    )
                )
            cls.KOL_WALLETS = wallets

        if "weights" in data:
            allowed = set(IndicatorWeights.__dataclass_fields__)
            cls.WEIGHTS = IndicatorWeights(
                **{key: value for key, value in data["weights"].items() if key in allowed}
            )

        return cls


# Backwards-compatible singleton used by the existing modules.
config = Config()
