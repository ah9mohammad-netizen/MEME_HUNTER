"""Small, explainable post-trade learning loop.

This is not an unsafe self-modifying model.  It records outcomes and promotes
wallets seen behind profitable tokens to a *learned candidate* list.  Learned
wallets remain alert-only/copy-trade-disabled until the operator reviews them.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional

from config import config
from models import Position
from state_store import StateStore

logger = logging.getLogger(__name__)


class TradeLearner:
    def __init__(self, store: StateStore, wallet_registry=None):
        self.store = store
        self.wallet_registry = wallet_registry

    @staticmethod
    def _actors(position: Position) -> List[Dict[str, Any]]:
        actors: List[Dict[str, Any]] = []
        seen = set()
        for evidence in getattr(position, "actor_evidence", []) or []:
            if isinstance(evidence, str):
                evidence = {"address": evidence}
            address = str(evidence.get("address", evidence.get("wallet", ""))).strip()
            if address and address not in seen:
                actors.append(dict(evidence, address=address))
                seen.add(address)
        for address in getattr(position, "associated_wallets", []) or []:
            address = str(address).strip()
            if address and address not in seen:
                actors.append({"address": address, "source": "signal"})
                seen.add(address)
        return actors

    def record_closed_position(self, position: Position, reason: str = "") -> None:
        """Persist an outcome and update the learned-wallet evidence table."""
        pnl = float(position.realized_pnl_sol + position.unrealized_pnl_sol)
        actors = self._actors(position)
        self.store.record_token_outcome(position.token_mint, position.token_symbol, pnl, actors)

        if not config.TRADING.auto_learn_wallets or pnl < config.TRADING.learned_wallet_min_profit_sol:
            return

        for actor in actors:
            address = actor["address"]
            self.store.upsert_wallet_candidate(
                address=address,
                name=actor.get("wallet_name", actor.get("name", f"Learned {address[:8]}")),
                source="learned",
                wallet_type=actor.get("wallet_type", actor.get("type", "learned")),
                min_buy_threshold=float(actor.get("sol_amount", 0.5) or 0.5),
                profitable=True,
                pnl_sol=pnl,
                token_mint=position.token_mint,
                metadata={"reason": reason, "signal_score": position.entry_signal_score},
            )
            # Add immediately to the in-memory tracker for future alerts, but
            # explicitly keep copy trading disabled.
            if self.wallet_registry is not None:
                try:
                    self.wallet_registry.add_wallet(
                        address=address,
                        name=actor.get("wallet_name", actor.get("name", f"Learned {address[:8]}")),
                        source="learned",
                        min_buy_threshold=float(actor.get("sol_amount", 0.5) or 0.5),
                        copy_trade=False,
                    )
                except Exception as exc:
                    logger.warning("Could not add learned wallet %s: %s", address[:12], exc)

    def confidence_adjustment(self, wallet_addresses: Iterable[str]) -> float:
        """Return a small evidence-based score adjustment for future signals.

        It only activates after two observations, preventing one lucky trade
        from deciding the next entry.  The adjustment is intentionally capped.
        """
        candidates = {
            item["address"]: item for item in self.store.get_wallet_candidates(limit=500)
        }
        adjustment = 0.0
        for address in set(wallet_addresses):
            item = candidates.get(address)
            if not item or item["observations"] < 2:
                continue
            win_rate = item["profitable_observations"] / item["observations"]
            adjustment += 5.0 if win_rate >= 0.60 else (-5.0 if win_rate < 0.40 else 0.0)
        return max(-10.0, min(10.0, adjustment))

    def summary(self) -> Dict[str, Any]:
        return self.store.learning_summary()
