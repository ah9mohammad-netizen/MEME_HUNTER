"""
MEME HUNTER - Data Models
=========================
Core data structures for token tracking, trading, and portfolio management.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from datetime import datetime
from enum import Enum
from uuid import uuid4


class TokenStatus(Enum):
    """Token lifecycle status"""
    NEW = "new"                      # Just discovered
    SCANNING = "scanning"            # Under analysis
    APPROVED = "approved"            # Passed filters
    DCA_ENTRING = "dca_entering"     # Currently buying via DCA
    HOLDING = "holding"              # Position active
    GRID_SELLING = "grid_selling"    # Selling via grid
    CLOSED = "closed"                # Position closed
    REJECTED = "rejected"            # Failed filters
    RUGGED = "rugged"                # Detected as rug


class TradeDirection(Enum):
    """Trade direction"""
    BUY = "buy"
    SELL = "sell"


@dataclass
class TokenMetadata:
    """Token information and metadata"""
    mint: str
    name: str
    symbol: str
    decimals: int = 9
    total_supply: float = 0.0

    # Social
    twitter: Optional[str] = None
    telegram: Optional[str] = None
    website: Optional[str] = None

    # Security
    mint_authority_revoked: bool = False
    freeze_authority_revoked: bool = False
    lp_burned: bool = False
    is_honeypot: bool = False
    transfer_hook: bool = False

    # Contract verification
    dext_score: Optional[float] = None
    rugcheck_score: Optional[float] = None


@dataclass
class MarketData:
    """Real-time market data"""
    price_sol: float = 0.0
    price_usd: float = 0.0
    market_cap_sol: float = 0.0
    market_cap_usd: float = 0.0

    # Liquidity
    liquidity_sol: float = 0.0
    liquidity_usd: float = 0.0

    # Volume
    volume_24h_sol: float = 0.0
    volume_24h_usd: float = 0.0
    volume_1h_sol: float = 0.0

    # Trading
    buys_24h: int = 0
    sells_24h: int = 0
    unique_wallets_24h: int = 0

    # Momentum indicators
    price_change_5m: float = 0.0
    price_change_1h: float = 0.0
    price_change_24h: float = 0.0

    # Bonding curve (Pump.fun)
    progress_pct: float = 0.0  # 0-100%
    virtual_sol_reserves: float = 0.0
    virtual_token_reserves: float = 0.0

    # Timestamps
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class HolderData:
    """Holder distribution data"""
    total_holders: int = 0

    # Top holder stats
    top_10_pct: float = 0.0
    top_5_pct: float = 0.0
    dev_pct: float = 0.0

    # Concentration flags
    is_concentrated: bool = False
    dev_cluster_detected: bool = False

    # Recent changes
    new_holders_1h: int = 0
    holder_growth_rate: float = 0.0  # % per hour


@dataclass
class WhaleActivity:
    """Whale wallet activity tracking"""
    wallet_address: str
    wallet_name: str
    wallet_type: str  # 'whale', 'KOL', 'insider', 'bot'

    # Activity
    total_trades: int = 0
    total_volume_sol: float = 0.0
    avg_trade_size_sol: float = 0.0

    # Performance
    win_rate: float = 0.0
    avg_pnl_pct: float = 0.0
    total_pnl_sol: float = 0.0

    # Current positions
    active_positions: int = 0
    total_position_value: float = 0.0

    last_activity: datetime = field(default_factory=datetime.now)


@dataclass
class DCAOrder:
    """Dollar Cost Averaging order"""
    order_id: str
    leg_number: int  # Which DCA leg (1st, 2nd, 3rd, etc.)

    amount_sol: float
    expected_price: float
    actual_price: float = 0.0

    status: str = "pending"  # pending, filled, failed
    tx_signature: Optional[str] = None

    created_at: datetime = field(default_factory=datetime.now)
    filled_at: Optional[datetime] = None


@dataclass
class GridLevel:
    """Single grid level for selling"""
    level: int
    price_pct_of_entry: float  # e.g., 150% means sell at 1.5x entry
    amount_pct: float  # % of position to sell at this level
    status: str = "active"  # active, triggered, skipped
    triggered_price: Optional[float] = None
    triggered_at: Optional[datetime] = None


@dataclass
class Position:
    """Active trading position - extended for selectivity framework"""
    token_mint: str
    token_symbol: str

    # Entry
    entry_price: float  # Average entry price in SOL
    total_invested_sol: float
    total_tokens: float
    # Cost basis of tokens still held.  This is separate from total_invested_sol
    # because grid sells realize part of the original cost basis.
    remaining_cost_sol: float = 0.0

    # Live anti-rug context - new for selectivity framework
    initial_liquidity_sol: float = 0.0
    initial_liquidity_usd: float = 0.0
    initial_dev_token_balance: float = 0.0
    creator_address: Optional[str] = None
    creation_timestamp: Optional[datetime] = None
    last_rug_check_at: Optional[datetime] = None
    last_dca_fill_at: Optional[datetime] = None
    live_checks_failed: int = 0

    # Signal/actor context is persisted with the position so profitable tokens
    # can improve the whale/KOL list after a restart.
    entry_signal_score: float = 0.0
    associated_wallets: List[str] = field(default_factory=list)
    actor_evidence: List[Dict] = field(default_factory=list)

    # Strategy attribution and outcome metrics
    signal_run_id: str = ""
    strategy_types: List[str] = field(default_factory=list)
    signal_features: Dict = field(default_factory=dict)
    max_price: float = 0.0
    min_price: float = 0.0
    last_price: float = 0.0
    max_drawdown_pct: float = 0.0
    total_fees_sol: float = 0.0
    total_slippage_sol: float = 0.0

    # DCA tracking
    dca_orders: List[DCAOrder] = field(default_factory=list)
    dca_complete: bool = False
    dca_pending_sol: float = 0.0

    # Grid setup
    grid_levels: List[GridLevel] = field(default_factory=list)
    grid_sold_pct: float = 0.0

    # Take profit / Stop loss
    stop_loss_price: float = 0.0
    trailing_stop_price: float = 0.0
    trailing_stop_active: bool = False
    peak_price: float = 0.0

    # Moon bag
    moon_bag_tokens: float = 0.0
    moon_bag_active: bool = True
    grid_initial_tokens: float = 0.0

    # Status
    status: TokenStatus = TokenStatus.HOLDING

    # Metrics
    unrealized_pnl_sol: float = 0.0
    unrealized_pnl_pct: float = 0.0
    realized_pnl_sol: float = 0.0

    # Timestamps
    opened_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class TokenSignal:
    """Signal for a discovered token"""
    mint: str
    name: str
    symbol: str

    # Market data at discovery
    dev_buy_sol: float
    market_cap_sol: float
    liquidity_sol: float
    buy_ratio: float
    unique_wallets: int
    total_trades: int

    # Scoring
    overall_score: float = 0.0
    momentum_score: float = 0.0
    safety_score: float = 0.0
    potential_score: float = 0.0

    # Alerts
    is_whale_alert: bool = False
    whale_address: Optional[str] = None
    whale_type: Optional[str] = None

    # KOL mentions
    kol_mentions: int = 0
    kol_wallets: List[str] = field(default_factory=list)

    # Behavioral traces collected by the scanner. These are deliberately
    # optional because DexScreener signals do not expose launch-level traces.
    creator_address: Optional[str] = None
    behavior_data: Dict = field(default_factory=dict)

    # Social signals
    twitter_mentions: int = 0
    telegram_members: int = 0

    # Strategy attribution
    source: str = "unknown"
    strategy_types: List[str] = field(default_factory=lambda: ["normal_scanner"])
    signal_run_id: str = field(default_factory=lambda: uuid4().hex)
    observation_ids: List[int] = field(default_factory=list)

    # Timestamps
    discovered_at: datetime = field(default_factory=datetime.now)

    def calculate_overall_score(self) -> float:
        """Calculate a bounded 0–100 opportunity score with enhanced MELT-inspired penalties.

        Keeps opportunity, quality, momentum and smart-money separate.
        Risk analysis is still independent hard gate. Now includes sniper saturation,
        funding cluster, sale duration penalties for selectivity.
        """
        # Momentum: 35 points - early stage pump.fun typically has 10-20 unique, 10-30 trades
        # Give more credit for moderate unique wallets
        unique_score = min(max(self.unique_wallets, 0) / 30.0, 1.0) * 10.0 if self.unique_wallets < 30 else 10.0
        # Actually keep progressive but with better curve
        unique_score = min(max(self.unique_wallets, 0) / 50.0, 1.0) * 10.0
        # Bonus if source is pump portal with observed traders (real data vs snapshot)
        bonus_unique = 0.0
        if (self.behavior_data or {}).get("data_quality") == "observed" and self.unique_wallets >= 8:
            bonus_unique = 3.0

        momentum = (
            min(max(self.buy_ratio, 0.0), 1.0) * 15.0
            + unique_score
            + min(max(self.total_trades, 0) / 80.0, 1.0) * 10.0
            + bonus_unique
        )
        momentum = min(35.0, momentum)

        # Market quality: 30 points now (was 20) – crucial fix for pump.fun where liquidity=0
        # V2 hotfix: give generous baseline for pump.fun bonding curve
        behavior = self.behavior_data or {}
        # Dev buy: 0.5 SOL is decent, give 6 points, 1 SOL gives 12 (capped)
        dev_score = min(max(self.dev_buy_sol, 0.0) / 1.0, 1.0) * 12.0

        liq_score = 0.0
        if self.liquidity_sol > 0:
            # For pump.fun, be generous: 30 SOL = full 12 points, 3 SOL = 4 points
            if self.source in {"pumpfun_new", "pumpfun_api", "logs_subscribe"}:
                liq_score = max(5.0, min(max(self.liquidity_sol, 0.0) / 25.0, 1.0) * 12.0)
            else:
                liq_score = min(max(self.liquidity_sol, 0.0) / 35.0, 1.0) * 10.0
        else:
            if self.source in {"pumpfun_new", "pumpfun_api", "logs_subscribe"}:
                if behavior.get("dev_buy_known") or self.dev_buy_sol >= 0.3:
                    liq_score = 6.0  # stronger baseline for bonding curve
                if behavior.get("virtual_sol_reserves"):
                    try:
                        vsol = float(behavior.get("virtual_sol_reserves") or 0)
                        if vsol > 0:
                            liq_score = max(liq_score, min(vsol / 25.0, 1.0) * 10.0)
                    except (TypeError, ValueError):
                        pass

        gmgn_score = min(max(float(behavior.get("gmgn_quality_score", 0) or 0), 0.0), 10.0) * 0.6
        observed_bonus = 3.0 if behavior.get("data_quality") == "observed" else 0.0
        # Bonus for having at least 8 unique wallets (real interest)
        unique_bonus = 2.0 if self.unique_wallets >= 8 else 0.0

        quality = min(30.0, dev_score + liq_score + gmgn_score + observed_bonus + unique_bonus)

        # Opportunity: 25 points, but don't penalize too harsh for low mcap in pump.fun
        market_cap_ratio = min(max(self.market_cap_sol, 0.0) / 50.0, 1.0)
        potential = (1.0 - market_cap_ratio) * 25.0
        # Soft penalty only for extreme cases
        if self.market_cap_sol < 0.3 and self.liquidity_sol < 2 and self.source not in {"pumpfun_new", "pumpfun_api", "logs_subscribe"}:
            potential *= 0.5
        # Bonus for sweet spot mcap 2-15 SOL (many runners start here) – higher potential zone
        if 1.5 <= self.market_cap_sol <= 12.0:
            potential += 3.0
            potential = min(25.0, potential)

        # Social / community bonus – diagnosed higher potential memes often have replies and smart degens
        social_bonus = 0.0
        try:
            reply_count = int((self.behavior_data or {}).get("reply_count") or (self.behavior_data or {}).get("replyCount") or 0)
            if reply_count >= 10:
                social_bonus += 2.0
            elif reply_count >= 5:
                social_bonus += 1.0
        except (TypeError, ValueError):
            pass
        try:
            smart_degen = int((self.behavior_data or {}).get("smart_degen_count") or 0)
            if smart_degen >= 5:
                social_bonus += 3.0
            elif smart_degen >= 2:
                social_bonus += 1.5
        except (TypeError, ValueError):
            pass
        potential = min(28.0, potential + social_bonus)

        # Smart-money context: 20 points, capped
        smart_money = min(20.0, self.kol_mentions * 5.0 + (10.0 if self.is_whale_alert else 0.0))

        # Enhanced penalties: MELT-inspired
        behavior = self.behavior_data or {}
        # Core manipulation scores
        core_scores = [
            float(behavior.get("wash_trading_score", 0.0) or 0.0),
            float(behavior.get("bundle_risk_score", 0.0) or 0.0),
            float(behavior.get("mechanicality_score", 0.0) or 0.0),
        ]
        # New selectivity scores
        extra_scores = [
            float(behavior.get("sniper_saturation_score", 0.0) or 0.0),
            float(behavior.get("time_cluster_score", 0.0) or 0.0),
            float(behavior.get("sale_duration_risk_score", 0.0) or 0.0),
            float(behavior.get("cluster_risk_score", 0.0) or 0.0),
            float(behavior.get("funding_cluster_risk", 0.0) or 0.0),
        ]
        # Serial deployer penalty
        serial_penalty = 0.0
        if behavior.get("is_serial_deployer"):
            serial_penalty = float(behavior.get("serial_deployer_risk_score", 30.0) or 30.0)

        max_core = max(core_scores, default=0.0)
        max_extra = max(extra_scores, default=0.0)
        # Core penalty up to 20, extra up to 15, serial up to 10
        behavior_penalty = (
            min(20.0, max_core / 100.0 * 20.0)
            + min(15.0, max_extra / 100.0 * 15.0)
            + min(10.0, serial_penalty / 100.0 * 10.0)
        )

        self.momentum_score = momentum
        self.safety_score = quality
        self.potential_score = potential
        self.overall_score = max(0.0, min(100.0, momentum + quality + potential + smart_money - behavior_penalty))
        return self.overall_score


@dataclass
class Portfolio:
    """Overall portfolio tracking"""
    positions: Dict[str, Position] = field(default_factory=dict)

    # Balance
    starting_balance_sol: float = 0.0
    current_balance_sol: float = 0.0
    total_invested_sol: float = 0.0

    # PnL
    realized_pnl_sol: float = 0.0
    unrealized_pnl_sol: float = 0.0
    total_pnl_sol: float = 0.0
    total_pnl_pct: float = 0.0

    # Stats
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0

    # Trackers
    traded_mints: Set[str] = field(default_factory=set)
    watched_mints: Set[str] = field(default_factory=set)

    def update_metrics(self):
        """Recalculate portfolio metrics"""
        self.unrealized_pnl_sol = sum(p.unrealized_pnl_sol for p in self.positions.values())
        self.realized_pnl_sol = sum(p.realized_pnl_sol for p in self.positions.values())
        self.total_pnl_sol = self.realized_pnl_sol + self.unrealized_pnl_sol

        if self.starting_balance_sol > 0:
            self.total_pnl_pct = (self.total_pnl_sol / self.starting_balance_sol) * 100

        self.total_trades = self.winning_trades + self.losing_trades
        if self.total_trades > 0:
            self.win_rate = (self.winning_trades / self.total_trades) * 100
