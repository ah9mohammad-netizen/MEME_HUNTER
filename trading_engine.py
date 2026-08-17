"""DCA entry, grid exits, live anti-rug, selectivity framework."""

from __future__ import annotations

import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from capital_manager import CapitalManager
from config import config
from learning import TradeLearner
from models import DCAOrder, GridLevel, Position, TokenSignal, TokenStatus
from state_store import StateStore

logger = logging.getLogger(__name__)


def result_value(result, key: str, default=0):
    if isinstance(result, dict):
        return result.get(key, default)
    return getattr(result, key, default)


def result_ok(result) -> bool:
    return bool(result_value(result, "success", False))


class DCAExecutor:
    def __init__(self, trading_client, store: StateStore):
        self.client = trading_client
        self.store = store

    @staticmethod
    def _sizes(total: float, entries: int, multiplier: float) -> List[float]:
        entries = max(1, int(entries))
        multiplier = max(1.0, float(multiplier))
        denominator = sum(multiplier ** i for i in range(entries))
        sizes = [total * (multiplier ** i) / denominator for i in range(entries)]
        sizes[-1] += total - sum(sizes)
        return sizes

    def _create_grid_levels(self) -> List[GridLevel]:
        cfg = config.TRADING
        count = max(1, int(cfg.grid_levels))
        sellable_pct = max(0.0, min(100.0, 100.0 - cfg.moon_bag_pct))
        first_pct = min(25.0, sellable_pct)
        remaining = sellable_pct - first_pct
        per_level = remaining / (count - 1) if count > 1 else sellable_pct
        levels = []
        for index in range(count):
            amount = sellable_pct if count == 1 else (first_pct if index == 0 else per_level)
            levels.append(
                GridLevel(
                    level=index + 1,
                    price_pct_of_entry=cfg.first_tp_pct + cfg.grid_spacing_pct * index,
                    amount_pct=amount,
                )
            )
        return levels

    async def _fill_order(self, position: Position, order: DCAOrder) -> bool:
        try:
            result = await self.client.execute_buy(
                mint=position.token_mint,
                sol_amount=order.amount_sol,
                slippage_bps=config.TRADING.max_slippage_bps,
            )
            if not result_ok(result):
                order.status = "failed"
                logger.warning(
                    "DCA leg %s failed for %s: %s",
                    order.leg_number,
                    position.token_symbol,
                    result_value(result, "error", "unknown"),
                )
                return False
            tokens = float(result_value(result, "tokens_received", 0) or 0)
            actual_price = float(result_value(result, "price", 0) or 0) or order.expected_price
            order.actual_price = actual_price
            order.tx_signature = result_value(result, "signature")
            order.status = "filled"
            order.filled_at = datetime.now()
            position.total_invested_sol += order.amount_sol
            position.total_tokens += tokens
            position.remaining_cost_sol += order.amount_sol
            position.total_fees_sol += float(result_value(result, "gas_used", 0) or 0)
            position.total_slippage_sol += float(result_value(result, "slippage_sol", 0) or 0)
            # Track last DCA fill for cooldown
            setattr(position, "last_dca_fill_at", datetime.now())
            self.store.record_trade(
                position_mint=position.token_mint,
                symbol=position.token_symbol,
                side="buy",
                strategy="dca",
                reason=f"DCA leg {order.leg_number}",
                sol_amount=order.amount_sol,
                token_amount=tokens,
                price=actual_price,
                fee_sol=float(result_value(result, "gas_used", 0) or 0),
                slippage_sol=float(result_value(result, "slippage_sol", 0) or 0),
                tx_signature=order.tx_signature,
            )
            return True
        except Exception as exc:
            order.status = "failed"
            logger.error("DCA leg %s failed for %s: %s", order.leg_number, position.token_symbol, exc)
            return False

    async def execute_dca(
        self,
        mint: str,
        symbol: str,
        entry_price: float,
        total_sol_budget: float,
        signal_score: float = 0.0,
        actors: Optional[List[Dict]] = None,
        initial_liquidity_sol: float = 0.0,
        initial_liquidity_usd: float = 0.0,
        creator_address: Optional[str] = None,
    ) -> Optional[Position]:
        cfg = config.TRADING
        position = Position(
            token_mint=mint,
            token_symbol=symbol,
            entry_price=entry_price,
            total_invested_sol=0.0,
            total_tokens=0.0,
            status=TokenStatus.DCA_ENTRING,
            entry_signal_score=signal_score,
            associated_wallets=[
                item.get("address", item.get("wallet", ""))
                for item in (actors or [])
                if isinstance(item, dict)
            ],
            actor_evidence=list(actors or []),
            initial_liquidity_sol=initial_liquidity_sol,
            initial_liquidity_usd=initial_liquidity_usd,
            creator_address=creator_address,
            creation_timestamp=datetime.now(),
        )
        sizes = self._sizes(total_sol_budget, cfg.dca_entries, cfg.dca_increment_mult)
        prices = [
            entry_price if index == 0
            else entry_price * (1 - cfg.dca_spacing_pct / 100 * index)
            for index in range(len(sizes))
        ]

        for index, (sol_amount, expected_price) in enumerate(zip(sizes, prices), start=1):
            order = DCAOrder(
                order_id=f"dca_{mint[:8]}_{index}_{datetime.now().timestamp()}",
                leg_number=index,
                amount_sol=sol_amount,
                expected_price=expected_price,
            )
            if cfg.dca_wait_for_dips and index > 1:
                position.dca_orders.append(order)
                continue
            position.dca_orders.append(order)
            await self._fill_order(position, order)

        position.dca_pending_sol = sum(
            order.amount_sol for order in position.dca_orders if order.status == "pending"
        )
        if position.total_tokens <= 0 or position.total_invested_sol <= 0:
            return None
        position.entry_price = position.total_invested_sol / position.total_tokens
        position.stop_loss_price = position.entry_price * (1 - cfg.stop_loss_pct / 100)
        position.grid_levels = self._create_grid_levels()
        position.grid_initial_tokens = position.total_tokens
        position.moon_bag_tokens = position.total_tokens * cfg.moon_bag_pct / 100
        position.peak_price = position.entry_price
        position.dca_complete = position.dca_pending_sol <= 0
        position.status = TokenStatus.HOLDING
        setattr(position, "last_dca_fill_at", datetime.now())
        return position

    async def execute_pending(self, position: Position, current_price: float) -> float:
        """Fill pending DCA legs only after lower target and cooldown."""
        cfg = config.TRADING
        # Cooldown: don't DCA too fast
        last_fill = getattr(position, "last_dca_fill_at", None)
        if last_fill:
            if (datetime.now() - last_fill).total_seconds() < cfg.dca_cooldown_seconds:
                return 0.0

        filled_sol = 0.0
        for order in position.dca_orders:
            if order.status != "pending" or current_price > order.expected_price:
                continue
            if await self._fill_order(position, order):
                filled_sol += order.amount_sol
                position.dca_pending_sol = max(0.0, position.dca_pending_sol - order.amount_sol)
                position.entry_price = position.total_invested_sol / position.total_tokens
                position.stop_loss_price = position.entry_price * (1 - config.TRADING.stop_loss_pct / 100)
                position.grid_initial_tokens += max(0.0, position.total_tokens - position.grid_initial_tokens)
                position.moon_bag_tokens = position.total_tokens * config.TRADING.moon_bag_pct / 100
                # Only fill one leg per cooldown period
                break

        position.dca_pending_sol = sum(
            order.amount_sol for order in position.dca_orders if order.status == "pending"
        )
        position.dca_complete = position.dca_pending_sol <= 0
        return filled_sol

    def cancel_pending(self, position: Position, reason: str) -> int:
        cancelled = 0
        for order in position.dca_orders:
            if order.status == "pending":
                order.status = "skipped"
                cancelled += 1
        position.dca_pending_sol = 0.0
        position.dca_complete = True
        if cancelled:
            logger.info("Cancelled %s pending DCA leg(s) for %s: %s", cancelled, position.token_symbol, reason)
        return cancelled

    async def add_dca_leg(self, position: Position, additional_sol: float) -> DCAOrder:
        next_leg = len(position.dca_orders) + 1
        expected = position.entry_price * (1 - config.TRADING.dca_spacing_pct / 100 * next_leg)
        order = DCAOrder(
            order_id=f"dca_{position.token_mint[:8]}_{next_leg}_{datetime.now().timestamp()}",
            leg_number=next_leg, amount_sol=additional_sol, expected_price=expected,
        )
        try:
            result = await self.client.execute_buy(
                position.token_mint, additional_sol, config.TRADING.max_slippage_bps
            )
            if not result_ok(result):
                order.status = "failed"
                return order
            tokens = float(result_value(result, "tokens_received", 0) or 0)
            order.actual_price = float(result_value(result, "price", 0) or 0) or expected
            order.tx_signature = result_value(result, "signature")
            order.status = "filled"
            order.filled_at = datetime.now()
            position.dca_orders.append(order)
            position.total_invested_sol += additional_sol
            position.remaining_cost_sol += additional_sol
            position.total_tokens += tokens
            position.total_fees_sol += float(result_value(result, "gas_used", 0) or 0)
            position.total_slippage_sol += float(result_value(result, "slippage_sol", 0) or 0)
            position.grid_initial_tokens += tokens
            position.moon_bag_tokens = position.total_tokens * config.TRADING.moon_bag_pct / 100
            position.entry_price = position.total_invested_sol / position.total_tokens
            position.stop_loss_price = position.entry_price * (1 - config.TRADING.stop_loss_pct / 100)
            setattr(position, "last_dca_fill_at", datetime.now())
            self.store.record_trade(
                position_mint=position.token_mint, symbol=position.token_symbol, side="buy",
                strategy="dca", reason="manual DCA", sol_amount=additional_sol,
                token_amount=tokens, price=order.actual_price,
                fee_sol=float(result_value(result, "gas_used", 0) or 0),
                slippage_sol=float(result_value(result, "slippage_sol", 0) or 0),
                tx_signature=order.tx_signature,
            )
        except Exception as exc:
            order.status = "failed"
            logger.error("Manual DCA failed: %s", exc)
        return order


class GridSeller:
    def __init__(self, trading_client, store: StateStore):
        self.client = trading_client
        self.store = store

    @staticmethod
    def _apply_sale(position: Position, token_amount: float, sol_received: float) -> float:
        token_amount = min(max(0.0, token_amount), position.total_tokens)
        cost = position.entry_price * token_amount
        position.total_tokens = max(0.0, position.total_tokens - token_amount)
        position.remaining_cost_sol = max(0.0, position.remaining_cost_sol - cost)
        pnl = sol_received - cost
        position.realized_pnl_sol += pnl
        position.moon_bag_tokens = min(position.moon_bag_tokens, position.total_tokens)
        return pnl

    async def monitor_and_sell(self, position: Position, current_price: float) -> List[Dict]:
        if position.status not in (TokenStatus.HOLDING, TokenStatus.GRID_SELLING):
            return []
        fills = []
        initial_tokens = position.grid_initial_tokens or position.total_tokens

        # Check both price % TP and market-cap TP
        mcap_levels = []
        if config.TRADING.enable_mcap_tp and position.signal_features.get("market_cap_sol"):
            init_mcap_sol = float(position.signal_features.get("market_cap_sol", 0) or 0)
            if init_mcap_sol > 0:
                for idx, mcap_usd_thr in enumerate(config.TRADING.mcap_tp_levels):
                    thr_sol = mcap_usd_thr / 150.0  # approx
                    pct_needed = (thr_sol / init_mcap_sol * 100) if init_mcap_sol else 0
                    # If pct_needed reasonable, add as additional trigger
                    if 20 <= pct_needed <= 2000:
                        mcap_levels.append((idx, pct_needed, mcap_usd_thr))

        for level in position.grid_levels:
            if level.status != "active":
                continue
            target = position.entry_price * level.price_pct_of_entry / 100
            # Normal price target
            price_hit = current_price >= target
            # Also check mcap target if near this level
            mcap_hit = False
            triggered_mcap = None
            for mcap_idx, pct_needed, mcap_usd in mcap_levels:
                if mcap_idx + 1 == level.level:  # align level number with mcap level
                    mcap_target_price = position.entry_price * (1 + pct_needed / 100)
                    if current_price >= mcap_target_price:
                        mcap_hit = True
                        triggered_mcap = mcap_usd
                        break

            if not (price_hit or mcap_hit):
                continue

            token_amount = initial_tokens * level.amount_pct / 100
            max_sell = max(0.0, position.total_tokens - position.moon_bag_tokens)
            token_amount = min(token_amount, max_sell)
            if token_amount <= 0:
                level.status = "skipped"
                continue
            try:
                result = await self.client.execute_sell(
                    position.token_mint, token_amount, config.TRADING.max_slippage_bps
                )
                if not result_ok(result):
                    logger.warning("Grid sell failed for %s: %s", position.token_symbol, result_value(result, "error", "unknown"))
                    continue
                proceeds = float(result_value(result, "sol_received", 0) or 0)
                sold_amount = float(result_value(result, "tokens_sold", token_amount) or token_amount)
                pnl = self._apply_sale(position, sold_amount, proceeds)
                position.total_fees_sol += float(result_value(result, "gas_used", 0) or 0)
                position.total_slippage_sol += float(result_value(result, "slippage_sol", 0) or 0)
                level.status = "triggered"
                level.triggered_price = current_price
                level.triggered_at = datetime.now()
                position.grid_sold_pct += level.amount_pct
                reason = f"grid level {level.level}"
                if mcap_hit and triggered_mcap:
                    reason += f" mcap ${triggered_mcap:,.0f}"
                self.store.record_trade(
                    position_mint=position.token_mint, symbol=position.token_symbol, side="sell",
                    strategy="grid", reason=reason, sol_amount=proceeds,
                    token_amount=sold_amount,
                    price=float(result_value(result, "price", 0) or current_price),
                    realized_pnl_sol=pnl,
                    fee_sol=float(result_value(result, "gas_used", 0) or 0),
                    slippage_sol=float(result_value(result, "slippage_sol", 0) or 0),
                    tx_signature=result_value(result, "signature"),
                )
                fills.append({"level": level.level, "proceeds": proceeds, "pnl": pnl})
            except Exception as exc:
                logger.error("Grid sell failed for %s: %s", position.token_symbol, exc)
        position.updated_at = datetime.now()
        return fills


class MomentumDetector:
    def __init__(self):
        self.price_history: Dict[str, List[Tuple[datetime, float]]] = {}
        self.volume_history: Dict[str, List[Tuple[datetime, float]]] = {}

    def record_price(self, mint: str, price: float, volume: float = 0.0) -> None:
        history = self.price_history.setdefault(mint, [])
        history.append((datetime.now(), price))
        if len(history) > 100:
            del history[:-100]
        if volume:
            vhist = self.volume_history.setdefault(mint, [])
            vhist.append((datetime.now(), volume))
            if len(vhist) > 100:
                del vhist[:-100]

    def detect_pump_signal(self, mint: str) -> Dict:
        history = self.price_history.get(mint, [])
        if len(history) < 10:
            return {"signal": False, "reason": "Insufficient data"}
        recent = [item[1] for item in history[-5:]]
        previous = [item[1] for item in history[-10:-5]]
        old_avg = sum(previous) / len(previous)
        new_avg = sum(recent) / len(recent)
        momentum = (new_avg - old_avg) / old_avg if old_avg else 0
        # Also check volume surge
        volume_surge = False
        vhist = self.volume_history.get(mint, [])
        if len(vhist) >= 10:
            recent_vol = sum(v[1] for v in vhist[-5:]) / 5
            prev_vol = sum(v[1] for v in vhist[-10:-5]) / 5 if len(vhist) >= 10 else recent_vol
            if prev_vol > 0 and recent_vol / prev_vol > 1.5:
                volume_surge = True
        return {"signal": momentum > 0.05, "strength": min(100, max(0, momentum * 100)), "price_momentum": momentum, "volume_surge": volume_surge}

    def detect_local_max(self, mint: str, current_price: float) -> bool:
        history = self.price_history.get(mint, [])
        if len(history) < 5:
            return False
        recent = [item[1] for item in history[-5:]]
        return recent[-1] < recent[-2] < recent[-3] and recent[-3] == max(recent)

    def detect_volume_decline(self, mint: str) -> bool:
        vhist = self.volume_history.get(mint, [])
        if len(vhist) < 10:
            return False
        recent = sum(v[1] for v in vhist[-5:]) / 5
        prev = sum(v[1] for v in vhist[-10:-5]) / 5
        return prev > 0 and recent < prev * 0.5


class LiveRugMonitor:
    """
    Selectivity-focused live monitoring - checks for dev dumps, liquidity drops, holder spikes, time exceed, volume decline.
    Does NOT try to be 0-slot fast, but catches rugs early after entry.
    """

    def __init__(self, trading_client, risk_analyzer=None):
        self.client = trading_client
        self.risk_analyzer = risk_analyzer
        self._last_check: Dict[str, datetime] = {}

    async def check_position(self, position: Position, current_price: float, momentum_detector: MomentumDetector) -> Optional[str]:
        """Return reason if rug/time exceed detected, else None"""
        cfg = config.TRADING
        if not cfg.enable_live_rug_checks:
            return None

        mint = position.token_mint
        now = datetime.now()

        # Rate limit checks
        last = self._last_check.get(mint)
        if last and (now - last).total_seconds() < cfg.live_rug_check_interval_seconds:
            return None
        self._last_check[mint] = now

        # 1. Time exceed - if open too long and losing, exit
        hold_seconds = (now - position.opened_at).total_seconds()
        if hold_seconds > cfg.time_exceed_seconds:
            # Only exit if unrealized <0 or grid not triggered and momentum weak
            if position.unrealized_pnl_pct < 0:
                pump = momentum_detector.detect_pump_signal(mint)
                if not pump.get("signal"):
                    return f"TIME_EXCEED {hold_seconds:.0f}s > {cfg.time_exceed_seconds}s and no momentum"

        # 2. Dev dump check if we have creator and risk analyzer
        if self.risk_analyzer and position.creator_address and getattr(position, "initial_dev_token_balance", 0) > 0:
            reason = await self.risk_analyzer.check_dev_dump(
                mint, position.creator_address, position.initial_dev_token_balance
            )
            if reason:
                return reason

        # 3. Live rug checks via risk analyzer (liquidity drop, holder spike)
        if self.risk_analyzer:
            context = {
                "initial_liquidity_usd": getattr(position, "initial_liquidity_usd", 0) or position.signal_features.get("liquidity_sol", 0) * 150,
                "initial_top10_pct": position.signal_features.get("behavior", {}).get("top_10_holder_rate", 0) * 100 if position.signal_features.get("behavior") else 0,
            }
            reason = await self.risk_analyzer.live_rug_check(mint, context)
            if reason:
                return reason

        # 4. Volume decline + price downtrend
        if momentum_detector.detect_volume_decline(mint) and position.unrealized_pnl_pct < -5:
            pump = momentum_detector.detect_pump_signal(mint)
            if not pump.get("volume_surge") and pump.get("price_momentum", 0) < -0.02:
                return "VOLUME_DECLINE + downtrend"

        return None


class TradingEngine:
    def __init__(
        self,
        trading_client,
        capital_manager: Optional[CapitalManager] = None,
        store: Optional[StateStore] = None,
        learner: Optional[TradeLearner] = None,
        risk_analyzer=None,
    ):
        self.client = trading_client
        self.store = store or StateStore()
        self.capital_manager = capital_manager or CapitalManager(trading_client)
        self.learner = learner or TradeLearner(self.store)
        self.dca_executor = DCAExecutor(trading_client, self.store)
        self.grid_seller = GridSeller(trading_client, self.store)
        self.momentum_detector = MomentumDetector()
        self.risk_analyzer = risk_analyzer
        self.live_rug_monitor = LiveRugMonitor(trading_client, risk_analyzer)
        self.trading_paused = False
        self.active_positions: Dict[str, Position] = {
            item.token_mint: item for item in self.store.load_open_positions()
        }

    async def open_position(self, signal: TokenSignal, sol_budget: Optional[float] = None) -> Optional[Position]:
        if self.trading_paused:
            logger.info("New entries paused")
            return None
        if len(self.active_positions) >= config.TRADING.max_coins_tracked:
            logger.warning("Max positions reached")
            return None
        if signal.mint in self.active_positions:
            return None

        # New: cap whale multiplier and concurrent whale positions
        whale_positions = sum(1 for p in self.active_positions.values() if "smart_money" in p.strategy_types or "kol" in str(p.strategy_types))
        if whale_positions >= config.TRADING.max_concurrent_whale_positions and ("smart_money" in str(signal.strategy_types) or signal.is_whale_alert):
            logger.info("Max concurrent whale positions reached, skipping whale signal")
            return None

        requested = sol_budget if sol_budget is not None else self.capital_manager.position_cap_sol()
        # Cap whale multiplier
        if signal.is_whale_alert:
            max_allowed = self.capital_manager.position_cap_sol() * config.TRADING.max_whale_multiplier
            requested = min(requested, max_allowed)

        approved = await self.capital_manager.reserve_for_entry(requested, self.active_positions.values())
        if approved is None:
            return None
        try:
            current_price = await self.client.get_token_price(signal.mint)
            if current_price <= 0:
                logger.warning(f"No price for {signal.symbol}, skipping")
                return None

            actors = []
            for wallet in signal.kol_wallets:
                actors.append({"address": wallet, "source": signal.whale_type or "signal"})

            position = await self.dca_executor.execute_dca(
                signal.mint, signal.symbol, current_price, approved,
                signal_score=signal.overall_score, actors=actors,
                initial_liquidity_sol=signal.liquidity_sol,
                initial_liquidity_usd=signal.liquidity_sol * 150,
                creator_address=signal.creator_address,
            )
            if position is None:
                return None

            position.signal_run_id = signal.signal_run_id
            position.strategy_types = list(signal.strategy_types or ["normal_scanner"])
            position.signal_features = {
                "source": signal.source,
                "score": signal.overall_score,
                "behavior": signal.behavior_data,
                "market_cap_sol": signal.market_cap_sol,
                "liquidity_sol": signal.liquidity_sol,
                "buy_ratio": signal.buy_ratio,
                "unique_wallets": signal.unique_wallets,
                "total_trades": signal.total_trades,
                "initial_top10": signal.behavior_data.get("bundle_risk_score", 0),
            }
            position.max_price = position.entry_price
            position.min_price = position.entry_price
            position.last_price = position.entry_price
            position.creation_timestamp = datetime.now()

            # Try to capture initial dev balance if possible (best effort)
            if signal.creator_address and self.risk_analyzer:
                try:
                    # For simplicity, assume dev held 5-10% initially if not known
                    # Real fetch would be done via RPC, but we store placeholder
                    position.initial_dev_token_balance = 0  # will be lazily fetched on first rug check
                except Exception:
                    pass

            self.active_positions[signal.mint] = position
            self.store.mark_signal_entry(signal.signal_run_id, position.entry_price)
            self.store.save_position(position)
            self.capital_manager.update_after_execution(position.total_invested_sol)
            logger.info(f"Opened {signal.symbol} @ {current_price:.8f} SOL, invested {position.total_invested_sol:.4f} SOL")
            return position
        finally:
            await self.capital_manager.release_reservation(approved)

    def _save(self, position: Position) -> None:
        position.updated_at = datetime.now()
        self.store.save_position(position)

    async def add_dca(self, mint: str, amount_sol: float) -> Optional[DCAOrder]:
        position = self.active_positions.get(mint)
        if not position:
            return None
        if position.grid_sold_pct > 0 or position.status not in (TokenStatus.HOLDING, TokenStatus.DCA_ENTRING):
            return None
        remaining_cap = max(
            0.0,
            self.capital_manager.position_cap_sol()
            - position.total_invested_sol
            - position.dca_pending_sol,
        )
        requested = min(float(amount_sol), remaining_cap)
        if requested < config.TRADING.min_trade_sol:
            return None
        approved = await self.capital_manager.reserve_for_entry(requested, self.active_positions.values())
        if approved is None:
            return None
        try:
            order = await self.dca_executor.add_dca_leg(position, approved)
            if order.status == "filled":
                self.capital_manager.update_after_execution(approved)
                self._save(position)
            return order
        finally:
            await self.capital_manager.release_reservation(approved)

    async def monitor_positions(self, price_data: Dict[str, float]) -> None:
        for mint, position in list(self.active_positions.items()):
            current_price = price_data.get(mint)
            if not current_price or current_price <= 0:
                continue
            position.last_price = current_price
            self.momentum_detector.record_price(mint, current_price)
            position.peak_price = max(position.peak_price or position.entry_price, current_price)
            position.max_price = max(position.max_price or position.entry_price, current_price)
            position.min_price = min(position.min_price or position.entry_price, current_price)
            drawdown_pct = (current_price / position.peak_price - 1) * 100 if position.peak_price > 0 else 0.0
            position.max_drawdown_pct = min(position.max_drawdown_pct, drawdown_pct)

            position.unrealized_pnl_sol = current_price * position.total_tokens - position.remaining_cost_sol
            position.unrealized_pnl_pct = (
                (current_price / position.entry_price - 1) * 100 if position.entry_price else 0
            )

            # === NEW: Live rug & time exceed checks BEFORE stop loss ===
            live_rug_reason = await self.live_rug_monitor.check_position(position, current_price, self.momentum_detector)
            if live_rug_reason:
                self.dca_executor.cancel_pending(position, f"live rug: {live_rug_reason}")
                await self._emergency_exit(position, f"Live rug: {live_rug_reason}")
                continue

            # Exit protection
            if position.stop_loss_price and current_price <= position.stop_loss_price:
                self.dca_executor.cancel_pending(position, "stop loss before DCA")
                await self._emergency_exit(position, "Stop loss triggered")
                continue

            activation_pct = config.TRADING.trailing_stop_activation_pct
            distance_pct = config.TRADING.trailing_stop_distance_pct
            if position.peak_price >= position.entry_price * (1 + activation_pct / 100):
                position.trailing_stop_active = True
                position.trailing_stop_price = max(
                    position.trailing_stop_price,
                    position.peak_price * (1 - distance_pct / 100),
                )
            if position.trailing_stop_active and current_price <= position.trailing_stop_price:
                self.dca_executor.cancel_pending(position, "trailing stop before DCA")
                await self._emergency_exit(position, "Trailing stop triggered")
                continue

            if position.grid_sold_pct > 0:
                self.dca_executor.cancel_pending(position, "grid selling started")
            else:
                pending_spend = await self.dca_executor.execute_pending(position, current_price)
                if pending_spend > 0:
                    self.capital_manager.update_after_execution(pending_spend)
                    position.unrealized_pnl_sol = current_price * position.total_tokens - position.remaining_cost_sol
                    position.unrealized_pnl_pct = (
                        (current_price / position.entry_price - 1) * 100 if position.entry_price else 0
                    )

            grid_fills = await self.grid_seller.monitor_and_sell(position, current_price)
            for fill in grid_fills:
                self.capital_manager.update_after_receipt(fill.get("proceeds", 0))
            position.unrealized_pnl_sol = current_price * position.total_tokens - position.remaining_cost_sol
            position.unrealized_pnl_pct = (
                (current_price / position.entry_price - 1) * 100 if position.entry_price else 0
            )
            if position.signal_run_id:
                self.store.update_signal_market(
                    position.signal_run_id, current_price, position.max_price,
                    position.min_price, position.max_drawdown_pct,
                )
            self._save(position)

    async def _sell_all(self, position: Position, reason: str, slippage_bps: Optional[int] = None) -> bool:
        if position.total_tokens <= 0:
            return True
        result = await self.client.execute_sell(
            position.token_mint, position.total_tokens,
            slippage_bps if slippage_bps is not None else config.TRADING.max_slippage_bps,
        )
        if not result_ok(result):
            logger.error("Could not close %s: %s", position.token_symbol, result_value(result, "error", "unknown"))
            return False
        amount = float(result_value(result, "tokens_sold", position.total_tokens) or position.total_tokens)
        position.last_price = float(result_value(result, "price", position.last_price or position.entry_price) or position.last_price or position.entry_price)
        position.max_price = max(position.max_price or position.entry_price, position.last_price)
        position.min_price = min(position.min_price or position.entry_price, position.last_price)
        proceeds = float(result_value(result, "sol_received", 0) or 0)
        pnl = GridSeller._apply_sale(position, amount, proceeds)
        position.total_fees_sol += float(result_value(result, "gas_used", 0) or 0)
        position.total_slippage_sol += float(result_value(result, "slippage_sol", 0) or 0)
        self.store.record_trade(
            position_mint=position.token_mint, symbol=position.token_symbol, side="sell",
            strategy="risk_exit", reason=reason, sol_amount=proceeds, token_amount=amount,
            price=float(result_value(result, "price", 0) or 0), realized_pnl_sol=pnl,
            fee_sol=float(result_value(result, "gas_used", 0) or 0),
            slippage_sol=float(result_value(result, "slippage_sol", 0) or 0),
            tx_signature=result_value(result, "signature"),
        )
        self.capital_manager.update_after_receipt(proceeds)
        return True

    async def _finalize(self, position: Position, reason: str) -> None:
        self.dca_executor.cancel_pending(position, "position finalized")
        position.status = TokenStatus.CLOSED
        position.moon_bag_tokens = 0
        position.unrealized_pnl_sol = 0.0
        position.unrealized_pnl_pct = 0.0
        exit_price = position.last_price or position.entry_price
        hold_seconds = max(0.0, (datetime.now() - position.opened_at).total_seconds())
        if position.signal_run_id:
            self.store.mark_signal_exit(
                position.signal_run_id,
                exit_price=exit_price,
                realized_pnl_sol=position.realized_pnl_sol,
                fees_sol=position.total_fees_sol,
                slippage_sol=position.total_slippage_sol,
                hold_seconds=hold_seconds,
            )
        self._save(position)
        self.learner.record_closed_position(position, reason)
        self.active_positions.pop(position.token_mint, None)

    async def _emergency_exit(self, position: Position, reason: str) -> None:
        logger.warning("Emergency exit for %s: %s", position.token_symbol, reason)
        if await self._sell_all(position, reason, 1000):
            await self._finalize(position, reason)

    async def close_position(self, mint: str, reason: str = "Manual close") -> None:
        position = self.active_positions.get(mint)
        if not position:
            return
        if await self._sell_all(position, reason):
            await self._finalize(position, reason)

    def get_portfolio_summary(self) -> Dict:
        positions = list(self.active_positions.values())
        capital = self.capital_manager.snapshot(positions)
        unrealized = sum(item.unrealized_pnl_sol for item in positions)
        stats = self.store.get_trade_stats()
        realized = stats["realized_pnl_sol"]
        cash_ledger = self.store.get_cash_ledger(
            float(getattr(self.client, "starting_balance_sol", self.capital_manager.reference_balance_sol) or 0)
        )
        paper_cash = getattr(self.client, "balance_sol", None)
        if paper_cash is not None:
            cash_ledger["paper_cash_balance_sol"] = float(paper_cash)
            cash_ledger["reconciliation_delta_sol"] = (
                float(paper_cash) - cash_ledger["ledger_cash_balance_sol"]
            )
        snapshot = {
            **capital, "unrealized_pnl_sol": unrealized,
            "realized_pnl_sol": realized, "total_pnl_sol": realized + unrealized,
        }
        self.store.save_snapshot(snapshot)
        return {
            **snapshot,
            "cash_ledger": cash_ledger,
            "active_positions": len(positions),
            "total_invested_sol": sum(item.total_invested_sol for item in positions),
            "total_trades": stats["sell_fills"], "win_rate": stats["win_rate"],
            "learning": self.learner.summary(),
            "positions": [
                {
                    "mint": item.token_mint, "symbol": item.token_symbol,
                    "entry": item.entry_price, "current": (
                        item.entry_price * (1 + item.unrealized_pnl_pct / 100)
                    ), "pnl_pct": item.unrealized_pnl_pct,
                    "pnl_sol": item.unrealized_pnl_sol,
                    "grid_sold_pct": item.grid_sold_pct,
                    "moon_bag_tokens": item.moon_bag_tokens,
                    "hold_seconds": (datetime.now() - item.opened_at).total_seconds(),
                }
                for item in positions
            ],
        }
