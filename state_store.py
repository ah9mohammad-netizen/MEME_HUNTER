"""Durable SQLite state for trades, positions, snapshots and learned actors.

Railway containers can restart.  Set ``STATE_DB_PATH``/``DB_PATH`` to a file on
an attached Railway volume (for example ``/data/meme_hunter.sqlite3``) if the
history must survive redeploys as well as restarts.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from models import DCAOrder, GridLevel, Position, TokenStatus

logger = logging.getLogger(__name__)


class StateStore:
    """Small synchronous SQLite repository; writes are short and transactional."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or os.getenv("STATE_DB_PATH") or os.getenv("DB_PATH") or config_path()
        if self.path != ":memory:":
            Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._create_schema()

    def _create_schema(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS positions (
                    mint TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    status TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trade_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    position_mint TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    reason TEXT,
                    sol_amount REAL NOT NULL DEFAULT 0,
                    token_amount REAL NOT NULL DEFAULT 0,
                    price REAL NOT NULL DEFAULT 0,
                    fee_sol REAL NOT NULL DEFAULT 0,
                    realized_pnl_sol REAL NOT NULL DEFAULT 0,
                    tx_signature TEXT,
                    metadata_json TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_trade_events_mint ON trade_events(position_mint);
                CREATE INDEX IF NOT EXISTS idx_trade_events_created ON trade_events(created_at);
                CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    wallet_balance_sol REAL NOT NULL,
                    reserved_sol REAL NOT NULL,
                    available_sol REAL NOT NULL,
                    committed_sol REAL NOT NULL,
                    unrealized_pnl_sol REAL NOT NULL,
                    realized_pnl_sol REAL NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS token_outcomes (
                    mint TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    pnl_sol REAL NOT NULL,
                    profitable INTEGER NOT NULL,
                    actors_json TEXT NOT NULL,
                    closed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wallet_candidates (
                    address TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    source TEXT NOT NULL,
                    wallet_type TEXT NOT NULL,
                    min_buy_threshold REAL NOT NULL DEFAULT 0.5,
                    observations INTEGER NOT NULL DEFAULT 0,
                    profitable_observations INTEGER NOT NULL DEFAULT 0,
                    total_pnl_sol REAL NOT NULL DEFAULT 0,
                    last_token_mint TEXT,
                    last_seen TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    metadata_json TEXT
                );
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.utcnow().isoformat(timespec="seconds") + "Z"

    @staticmethod
    def _json_default(value: Any):
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, datetime):
            return value.isoformat()
        if is_dataclass(value):
            return asdict(value)
        raise TypeError(f"Cannot serialize {type(value)!r}")

    def save_position(self, position: Position) -> None:
        payload = json.dumps(asdict(position), default=self._json_default)
        status = position.status.value if isinstance(position.status, TokenStatus) else str(position.status)
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO positions(mint,symbol,status,data_json,updated_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(mint) DO UPDATE SET symbol=excluded.symbol,
                   status=excluded.status,data_json=excluded.data_json,updated_at=excluded.updated_at""",
                (position.token_mint, position.token_symbol, status, payload, self._now()),
            )

    def load_open_positions(self) -> List[Position]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT data_json FROM positions WHERE status NOT IN ('closed','rejected','rugged')"
            ).fetchall()
        positions = []
        for row in rows:
            try:
                positions.append(position_from_dict(json.loads(row["data_json"])))
            except Exception as exc:
                logger.error("Ignoring invalid persisted position: %s", exc)
        return positions

    def record_trade(
        self,
        *,
        position_mint: str,
        symbol: str,
        side: str,
        strategy: str,
        reason: str = "",
        sol_amount: float = 0.0,
        token_amount: float = 0.0,
        price: float = 0.0,
        fee_sol: float = 0.0,
        realized_pnl_sol: float = 0.0,
        tx_signature: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """INSERT INTO trade_events
                (position_mint,symbol,side,strategy,reason,sol_amount,token_amount,
                 price,fee_sol,realized_pnl_sol,tx_signature,metadata_json,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    position_mint,
                    symbol,
                    side,
                    strategy,
                    reason,
                    float(sol_amount),
                    float(token_amount),
                    float(price),
                    float(fee_sol),
                    float(realized_pnl_sol),
                    tx_signature,
                    json.dumps(metadata or {}, default=self._json_default),
                    self._now(),
                ),
            )
            return int(cursor.lastrowid)

    def get_trade_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM trade_events ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [dict(row) for row in rows]

    def get_trade_stats(self) -> Dict[str, float]:
        with self._lock:
            row = self._connection.execute(
                """SELECT COUNT(*) AS fills,
                   COALESCE(SUM(realized_pnl_sol),0) AS realized_pnl,
                   COALESCE(SUM(CASE WHEN realized_pnl_sol > 0 THEN 1 ELSE 0 END),0) AS wins
                   FROM trade_events WHERE side='sell'"""
            ).fetchone()
        fills = int(row["fills"] or 0)
        wins = int(row["wins"] or 0)
        return {
            "sell_fills": fills,
            "realized_pnl_sol": float(row["realized_pnl"] or 0),
            "winning_fills": wins,
            "win_rate": (wins / fills * 100.0) if fills else 0.0,
        }

    def save_snapshot(self, snapshot: Dict[str, float]) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO portfolio_snapshots
                (wallet_balance_sol,reserved_sol,available_sol,committed_sol,
                 unrealized_pnl_sol,realized_pnl_sol,created_at)
                VALUES(?,?,?,?,?,?,?)""",
                (
                    snapshot.get("wallet_balance_sol", 0), snapshot.get("reserved_sol", 0),
                    snapshot.get("available_sol", 0), snapshot.get("committed_sol", 0),
                    snapshot.get("unrealized_pnl_sol", 0), snapshot.get("realized_pnl_sol", 0),
                    self._now(),
                ),
            )

    def record_token_outcome(
        self, mint: str, symbol: str, pnl_sol: float, actors: Iterable[Dict[str, Any]]
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO token_outcomes(mint,symbol,pnl_sol,profitable,actors_json,closed_at)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(mint) DO UPDATE SET symbol=excluded.symbol,pnl_sol=excluded.pnl_sol,
                   profitable=excluded.profitable,actors_json=excluded.actors_json,closed_at=excluded.closed_at""",
                (
                    mint,
                    symbol,
                    float(pnl_sol),
                    int(pnl_sol > 0),
                    json.dumps(list(actors), default=self._json_default),
                    self._now(),
                ),
            )

    def upsert_wallet_candidate(
        self,
        *,
        address: str,
        name: str = "Learned actor",
        source: str = "learned",
        wallet_type: str = "learned",
        min_buy_threshold: float = 0.5,
        profitable: bool = False,
        pnl_sol: float = 0.0,
        token_mint: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        address = str(address).strip()
        if not address or len(address) < 32:
            return
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO wallet_candidates
                  (address,name,source,wallet_type,min_buy_threshold,observations,
                   profitable_observations,total_pnl_sol,last_token_mint,last_seen,metadata_json)
                  VALUES(?,?,?,?,?,1,?,?,?,?,?)
                  ON CONFLICT(address) DO UPDATE SET name=excluded.name,
                   observations=wallet_candidates.observations+1,
                   profitable_observations=wallet_candidates.profitable_observations+excluded.profitable_observations,
                   total_pnl_sol=wallet_candidates.total_pnl_sol+excluded.total_pnl_sol,
                   last_token_mint=excluded.last_token_mint,last_seen=excluded.last_seen,
                   metadata_json=excluded.metadata_json""",
                (
                    address, name, source, wallet_type, float(min_buy_threshold),
                    int(profitable), float(pnl_sol), token_mint, self._now(),
                    json.dumps(metadata or {}, default=self._json_default),
                ),
            )

    def get_wallet_candidates(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT * FROM wallet_candidates WHERE enabled=1
                   ORDER BY profitable_observations DESC,total_pnl_sol DESC LIMIT ?""",
                (int(limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    def learning_summary(self) -> Dict[str, Any]:
        with self._lock:
            outcome = self._connection.execute(
                "SELECT COUNT(*) AS tokens, COALESCE(SUM(profitable),0) AS profitable FROM token_outcomes"
            ).fetchone()
            wallets = self._connection.execute(
                "SELECT COUNT(*) AS candidates FROM wallet_candidates WHERE enabled=1"
            ).fetchone()
        return {
            "closed_tokens": int(outcome["tokens"] or 0),
            "profitable_tokens": int(outcome["profitable"] or 0),
            "learned_wallets": int(wallets["candidates"] or 0),
        }

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def config_path() -> str:
    """Resolve the configured DB path without importing the global config."""
    return os.getenv("MEME_HUNTER_DB", "meme_hunter.sqlite3")


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if not value:
        return datetime.now()
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except (TypeError, ValueError):
        return datetime.now()


def position_from_dict(data: Dict[str, Any]) -> Position:
    """Rehydrate a Position saved by :meth:`StateStore.save_position`."""
    dca_orders = [
        DCAOrder(
            order_id=item.get("order_id", ""), leg_number=int(item.get("leg_number", 0)),
            amount_sol=float(item.get("amount_sol", 0)), expected_price=float(item.get("expected_price", 0)),
            actual_price=float(item.get("actual_price", 0)), status=item.get("status", "pending"),
            tx_signature=item.get("tx_signature"), created_at=_parse_datetime(item.get("created_at")),
            filled_at=_parse_datetime(item["filled_at"]) if item.get("filled_at") else None,
        )
        for item in data.get("dca_orders", [])
    ]
    grid_levels = [
        GridLevel(
            level=int(item.get("level", 0)), price_pct_of_entry=float(item.get("price_pct_of_entry", 0)),
            amount_pct=float(item.get("amount_pct", 0)), status=item.get("status", "active"),
            triggered_price=item.get("triggered_price"),
            triggered_at=_parse_datetime(item["triggered_at"]) if item.get("triggered_at") else None,
        )
        for item in data.get("grid_levels", [])
    ]
    kwargs = dict(
        token_mint=data["token_mint"], token_symbol=data["token_symbol"],
        entry_price=float(data.get("entry_price", 0)), total_invested_sol=float(data.get("total_invested_sol", 0)),
        total_tokens=float(data.get("total_tokens", 0)), remaining_cost_sol=float(data.get("remaining_cost_sol", 0)),
        entry_signal_score=float(data.get("entry_signal_score", 0)),
        associated_wallets=list(data.get("associated_wallets", [])),
        actor_evidence=list(data.get("actor_evidence", [])), dca_orders=dca_orders,
        dca_complete=bool(data.get("dca_complete", False)), grid_levels=grid_levels,
        grid_sold_pct=float(data.get("grid_sold_pct", 0)), stop_loss_price=float(data.get("stop_loss_price", 0)),
        trailing_stop_price=float(data.get("trailing_stop_price", 0)),
        trailing_stop_active=bool(data.get("trailing_stop_active", False)),
        peak_price=float(data.get("peak_price", data.get("entry_price", 0))),
        moon_bag_tokens=float(data.get("moon_bag_tokens", 0)), moon_bag_active=bool(data.get("moon_bag_active", True)),
        grid_initial_tokens=float(data.get("grid_initial_tokens", data.get("total_tokens", 0))),
        status=TokenStatus(data.get("status", TokenStatus.HOLDING.value)),
        unrealized_pnl_sol=float(data.get("unrealized_pnl_sol", 0)),
        unrealized_pnl_pct=float(data.get("unrealized_pnl_pct", 0)),
        realized_pnl_sol=float(data.get("realized_pnl_sol", 0)),
        opened_at=_parse_datetime(data.get("opened_at")), updated_at=_parse_datetime(data.get("updated_at")),
    )
    if kwargs["remaining_cost_sol"] <= 0 and kwargs["total_tokens"] > 0:
        kwargs["remaining_cost_sol"] = kwargs["entry_price"] * kwargs["total_tokens"]
    return Position(**kwargs)
