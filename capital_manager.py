"""Capital allocation and live SOL-balance management."""

from __future__ import annotations

import asyncio
import logging
from typing import Iterable, Optional

from config import config
from models import Position

logger = logging.getLogger(__name__)


class CapitalManager:
    """
    Keeps new entries inside a budget instead of blindly spending the wallet.

    ``reference_balance_sol`` is captured at startup.  For example, with a
    1-SOL wallet and the default 10% setting, every new meme is capped at 0.1
    SOL, while ``max_portfolio_allocation_pct`` prevents more than 0.8 SOL of
    the initial capital from being committed at once.  The live wallet balance
    and a small SOL reserve are checked as a second, independent guard.
    """

    def __init__(self, trading_client):
        self.client = trading_client
        self.wallet_balance_sol = 0.0
        self.reference_balance_sol = 0.0
        self.reserved_sol = 0.0
        self.last_synced_at = 0.0
        self._lock = asyncio.Lock()

    def set_balance(self, balance_sol: float) -> None:
        """Set the observed free SOL balance without changing the baseline."""
        balance_sol = max(0.0, float(balance_sol))
        self.wallet_balance_sol = balance_sol
        if self.reference_balance_sol <= 0:
            self.reference_balance_sol = balance_sol

    async def refresh(self) -> float:
        """Read the wallet's free SOL balance from the RPC client."""
        try:
            balance = float(await self.client.get_balance())
            self.set_balance(balance)
            import time
            self.last_synced_at = time.time()
        except Exception as exc:  # balance refresh must not stop position management
            logger.warning("Could not refresh SOL balance: %s", exc)
        return self.wallet_balance_sol

    @staticmethod
    def _committed_sol(positions: Iterable[Position]) -> float:
        total = 0.0
        for position in positions:
            # Older persisted positions do not have remaining_cost_sol.
            remaining = getattr(position, "remaining_cost_sol", 0.0)
            total += remaining if remaining > 0 else position.total_invested_sol
        return max(0.0, total)

    def max_exposure_sol(self) -> float:
        baseline = self.reference_balance_sol or self.wallet_balance_sol
        return baseline * config.TRADING.max_portfolio_allocation_pct / 100.0

    def position_cap_sol(self) -> float:
        baseline = self.reference_balance_sol or self.wallet_balance_sol
        pct_cap = baseline * config.TRADING.allocation_per_meme_pct / 100.0
        absolute_cap = config.TRADING.max_position_per_coin
        if absolute_cap > 0:
            return min(pct_cap, absolute_cap)
        return pct_cap

    def snapshot(self, positions: Iterable[Position]) -> dict:
        committed = self._committed_sol(positions)
        reserve_available = max(0.0, self.wallet_balance_sol - config.TRADING.min_sol_reserve)
        exposure_available = max(0.0, self.max_exposure_sol() - committed - self.reserved_sol)
        return {
            "wallet_balance_sol": self.wallet_balance_sol,
            "reference_balance_sol": self.reference_balance_sol,
            "reserved_sol": self.reserved_sol,
            "committed_sol": committed,
            "max_exposure_sol": self.max_exposure_sol(),
            "available_sol": max(0.0, min(reserve_available - self.reserved_sol, exposure_available)),
            "position_cap_sol": self.position_cap_sol(),
            "min_sol_reserve": config.TRADING.min_sol_reserve,
        }

    async def reserve_for_entry(
        self, requested_sol: float, positions: Iterable[Position]
    ) -> Optional[float]:
        """Atomically reserve an entry budget, returning the approved amount."""
        async with self._lock:
            positions = list(positions)
            snap = self.snapshot(positions)
            approved = min(float(requested_sol), self.position_cap_sol(), snap["available_sol"])
            if approved < config.TRADING.min_trade_sol:
                logger.info(
                    "Entry skipped: %.4f SOL available, minimum is %.4f SOL",
                    approved,
                    config.TRADING.min_trade_sol,
                )
                return None
            self.reserved_sol += approved
            return approved

    async def release_reservation(self, amount_sol: float) -> None:
        async with self._lock:
            self.reserved_sol = max(0.0, self.reserved_sol - max(0.0, float(amount_sol)))

    def update_after_execution(self, spent_sol: float) -> None:
        """Apply the known spend locally; the next periodic refresh reconciles it."""
        self.wallet_balance_sol = max(0.0, self.wallet_balance_sol - max(0.0, spent_sol))

    def update_after_receipt(self, received_sol: float) -> None:
        self.wallet_balance_sol += max(0.0, received_sol)
