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
    helius_api_key: str = ""
    helius_rpc_ws: str = ""  # wss://atlas-mainnet.helius-rpc.com/?api-key=...
    logs_subscribe_enabled: bool = False

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
    dca_cooldown_seconds: int = 15

    # Grid selling settings - V3: earlier profit taking for 1.4x max reality
    grid_levels: int = 5
    grid_spacing_pct: float = 18.0  # was 15, now wider spread for earlier TPs
    first_tp_pct: float = 25.0  # was 50 – too high for 1.41x max observed, now take 25% first
    moon_bag_pct: float = 25.0  # was 20, keep more for runners after earlier takes
    # Market-cap based TPs for migrated tokens (Raydium/PumpSwap)
    enable_mcap_tp: bool = True
    mcap_tp_levels: List[float] = None  # USD thresholds

    # Risk management - V3 tightened based on 85% false positive data
    stop_loss_pct: float = 22.0  # was 30, drawdown avg -13% so cut earlier
    trailing_stop_activation_pct: float = 35.0  # was 50, lock gains earlier
    trailing_stop_distance_pct: float = 12.0  # was 15, tighter trail for meme volatility
    # Kept as a compatibility alias for older configurations.
    trailing_stop_pct: float = 12.0
    max_slippage_bps: int = 500

    # Live anti-rug monitors - V3 faster exits
    time_exceed_seconds: int = 180  # was 300, avg hold 2.3m =138s, but losers need faster cut
    dev_dump_threshold_pct: float = 12.0  # was 15, more sensitive
    liquidity_drop_threshold_pct: float = 40.0  # was 50
    top_holder_spike_pct: float = 4.0  # was 5
    enable_live_rug_checks: bool = True
    live_rug_check_interval_seconds: int = 20  # was 30

    # Bundle & manipulation filters - V3 tightened after 22% approval too high
    max_bundle_risk_score: float = 60.0  # was 70/75
    max_wash_score: float = 60.0  # was 70
    max_sniper_saturation_score: float = 70.0  # was 80/85
    max_mechanicality_score: float = 65.0  # was 75
    min_sale_duration_seconds: float = 5.0
    min_unique_trader_ratio: float = 0.35  # was 0.3, require more unique
    enable_funding_cluster_check: bool = True
    funding_cluster_max_checked: int = 12
    max_funding_cluster_risk: float = 60.0  # was 70/75
    serial_deployer_threshold: int = 3

    # Paper execution realism. Fees/slippage are deliberately explicit so
    # paper results are not confused with ideal quote returns.
    paper_fee_bps: int = 100
    paper_slippage_bps: int = 50

    # Smart-money confirmation must be convergent by default.
    min_confirming_whales: int = 2
    max_whale_multiplier: float = 1.25
    max_concurrent_whale_positions: int = 5
    whale_min_trades: int = 20
    whale_min_win_rate: float = 55.0
    gmgn_token_enrich_per_hour: int = 50
    signal_recheck_interval_seconds: int = 60
    signal_recheck_max_age_seconds: int = 1800

    # Scanner tuning - selectivity over speed
    pumpportal_buffer_seconds: int = 10
    pumpfun_poll_interval_seconds: int = 3  # vs 30s before, still not 0-slot but 10x faster
    gecko_poll_interval_seconds: int = 30
    enable_pumpfun_api_scanner: bool = True
    enable_gecko_scanner: bool = True

    # Indicators & filters - V3: tighter for higher potential after 85% false positives
    # Scanner level is fast pre-filter, scoring is where selectivity happens
    min_liquidity_usd: float = 8000.0  # was 5000, require more exit liquidity
    min_market_cap_usd: float = 8000.0  # was 5000
    min_buy_ratio: float = 0.68  # was 0.60, need stronger buy pressure
    min_unique_wallets: int = 10  # was 8, need more organic interest
    max_top_holder_pct: float = 22.0  # was 30, tighten from 7 holder_concentration rejections only
    min_dev_buy_sol_max: float = 50.0

    def __post_init__(self):
        if self.mcap_tp_levels is None:
            self.mcap_tp_levels = [100000.0, 300000.0, 1000000.0, 3000000.0, 10000000.0]

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
