"""Durable SQLite state for trades, positions, snapshots and learned actors.

Railway containers can restart. Set ``DATA_DIR=/data`` when the service has
one attached Railway Volume. The trade-history file is then
``/data/trade_history.db``; the wallet list is managed separately by
``wallet_store.py`` at ``/data/wallets_list.db``.
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
from storage_paths import default_trade_history_path

logger = logging.getLogger(__name__)


class StateStore:
    """Small synchronous SQLite repository; writes are short and transactional."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or os.getenv("TRADE_HISTORY_DB_PATH") or os.getenv("STATE_DB_PATH") or os.getenv("DB_PATH") or config_path()
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
                    slippage_sol REAL NOT NULL DEFAULT 0,
                    realized_pnl_sol REAL NOT NULL DEFAULT 0,
                    tx_signature TEXT,
                    metadata_json TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_trade_events_mint ON trade_events(position_mint);
                CREATE INDEX IF NOT EXISTS idx_trade_events_created ON trade_events(created_at);
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mint TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL,
                    score REAL NOT NULL DEFAULT 0,
                    dev_buy_sol REAL NOT NULL DEFAULT 0,
                    market_cap_sol REAL NOT NULL DEFAULT 0,
                    liquidity_sol REAL NOT NULL DEFAULT 0,
                    buy_ratio REAL NOT NULL DEFAULT 0,
                    unique_wallets INTEGER NOT NULL DEFAULT 0,
                    risk_score REAL,
                    whale_sol REAL NOT NULL DEFAULT 0,
                    decision TEXT NOT NULL,
                    reason TEXT,
                    data_json TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at);
                CREATE TABLE IF NOT EXISTS signal_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_run_id TEXT NOT NULL,
                    mint TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    strategy_type TEXT NOT NULL,
                    discovered_at TEXT NOT NULL,
                    features_json TEXT NOT NULL,
                    decision TEXT NOT NULL DEFAULT 'discovered',
                    rejection_reason TEXT,
                    approved INTEGER NOT NULL DEFAULT 0,
                    entered INTEGER NOT NULL DEFAULT 0,
                    entry_price REAL,
                    max_price REAL,
                    min_price REAL,
                    max_multiple REAL,
                    max_drawdown_pct REAL,
                    exit_price REAL,
                    realized_pnl_sol REAL,
                    fees_sol REAL,
                    slippage_sol REAL,
                    hold_seconds REAL,
                    false_positive INTEGER,
                    closed_at TEXT,
                    UNIQUE(signal_run_id, strategy_type)
                );
                CREATE INDEX IF NOT EXISTS idx_signal_observations_strategy ON signal_observations(strategy_type);
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
                """
            )
            # Migrate databases created before slippage tracking was added.
            columns = {
                row[1] for row in self._connection.execute("PRAGMA table_info(trade_events)").fetchall()
            }
            if "slippage_sol" not in columns:
                self._connection.execute(
                    "ALTER TABLE trade_events ADD COLUMN slippage_sol REAL NOT NULL DEFAULT 0"
                )
            observation_columns = {
                row[1] for row in self._connection.execute(
                    "PRAGMA table_info(signal_observations)"
                ).fetchall()
            }
            if "rejection_reason" not in observation_columns:
                self._connection.execute(
                    "ALTER TABLE signal_observations ADD COLUMN rejection_reason TEXT"
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

    def record_signal(
        self,
        *,
        mint: str,
        symbol: str,
        name: str,
        score: float,
        dev_buy_sol: float,
        market_cap_sol: float,
        liquidity_sol: float,
        buy_ratio: float,
        unique_wallets: int,
        decision: str,
        reason: str = "",
        risk_score: Optional[float] = None,
        whale_sol: float = 0.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Persist a discovery/evaluation signal for later strategy analysis."""
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """INSERT INTO signals
                (mint,symbol,name,score,dev_buy_sol,market_cap_sol,liquidity_sol,
                 buy_ratio,unique_wallets,risk_score,whale_sol,decision,reason,data_json,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    mint, symbol, name, float(score), float(dev_buy_sol),
                    float(market_cap_sol), float(liquidity_sol), float(buy_ratio),
                    int(unique_wallets), risk_score, float(whale_sol), decision,
                    reason, json.dumps(metadata or {}, default=self._json_default), self._now(),
                ),
            )
            return int(cursor.lastrowid)

    def get_signal_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM signals ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [dict(row) for row in rows]

    def ensure_signal_observations(
        self,
        *,
        signal_run_id: str,
        mint: str,
        symbol: str,
        strategy_types: Iterable[str],
        discovered_at: datetime,
        features: Optional[Dict[str, Any]] = None,
        decision: str = "discovered",
        approved: bool = False,
    ) -> List[int]:
        """Create one durable observation row per strategy attribution."""
        types = sorted(set(strategy_types or ["normal_scanner"]))
        with self._lock, self._connection:
            for strategy_type in types:
                self._connection.execute(
                    """INSERT INTO signal_observations
                    (signal_run_id,mint,symbol,strategy_type,discovered_at,features_json,decision,approved)
                    VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(signal_run_id,strategy_type) DO NOTHING""",
                    (
                        signal_run_id, mint, symbol, strategy_type,
                        discovered_at.isoformat(),
                        json.dumps(features or {}, default=self._json_default),
                        decision, int(approved),
                    ),
                )
            rows = self._connection.execute(
                "SELECT id FROM signal_observations WHERE signal_run_id=?",
                (signal_run_id,),
            ).fetchall()
        return [int(row["id"]) for row in rows]

    def update_signal_observations(
        self,
        signal_run_id: str,
        *,
        strategy_types: Iterable[str],
        decision: str,
        approved: bool,
        risk_score: Optional[float] = None,
        features: Optional[Dict[str, Any]] = None,
        rejection_reason: Optional[str] = None,
    ) -> None:
        """Add final decision context to the rows created at discovery."""
        with self._lock, self._connection:
            self._connection.execute(
                """UPDATE signal_observations SET decision=?,approved=?,features_json=?,rejection_reason=?
                   WHERE signal_run_id=? AND strategy_type IN ({})""".format(
                    ",".join("?" for _ in set(strategy_types or ["normal_scanner"]))
                ),
                (
                    decision, int(approved), json.dumps(features or {}, default=self._json_default),
                    rejection_reason, signal_run_id, *sorted(set(strategy_types or ["normal_scanner"])),
                ),
            )

    def mark_signal_entry(self, signal_run_id: str, entry_price: float) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """UPDATE signal_observations SET entered=1,entry_price=?,max_price=?,min_price=?
                   WHERE signal_run_id=?""",
                (float(entry_price), float(entry_price), float(entry_price), signal_run_id),
            )

    def update_signal_market(
        self,
        signal_run_id: str,
        current_price: float,
        max_price: float,
        min_price: float,
        max_drawdown_pct: float,
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """UPDATE signal_observations SET max_price=?,min_price=?,max_multiple=CASE
                   WHEN entry_price > 0 THEN ? / entry_price ELSE NULL END,
                   max_drawdown_pct=? WHERE signal_run_id=? AND entered=1""",
                (float(max_price), float(min_price), float(max_price), float(max_drawdown_pct), signal_run_id),
            )

    def mark_signal_exit(
        self,
        signal_run_id: str,
        *,
        exit_price: float,
        realized_pnl_sol: float,
        fees_sol: float,
        slippage_sol: float,
        hold_seconds: float,
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """UPDATE signal_observations SET exit_price=?,realized_pnl_sol=?,fees_sol=?,
                   slippage_sol=?,hold_seconds=?,false_positive=?,closed_at=?
                   WHERE signal_run_id=? AND entered=1""",
                (
                    float(exit_price), float(realized_pnl_sol), float(fees_sol),
                    float(slippage_sol), float(hold_seconds), int(realized_pnl_sol <= 0),
                    self._now(), signal_run_id,
                ),
            )

    def get_rejection_report(self) -> Dict[str, Any]:
        """Summarize why signals did not reach a paper entry."""
        with self._lock:
            totals = self._connection.execute(
                """SELECT COUNT(DISTINCT signal_run_id) AS total,
                   COUNT(DISTINCT CASE WHEN approved=1 THEN signal_run_id END) AS approved,
                   COUNT(DISTINCT CASE WHEN decision='score_filtered' THEN signal_run_id END) AS score_filtered,
                   COUNT(DISTINCT CASE WHEN decision='rejected' THEN signal_run_id END) AS rejected,
                   COUNT(DISTINCT CASE WHEN decision='paused' THEN signal_run_id END) AS paused
                   FROM signal_observations"""
            ).fetchone()
            rows = self._connection.execute(
                """SELECT COALESCE(rejection_reason, decision) AS reason,
                   COUNT(DISTINCT signal_run_id) AS signals
                   FROM signal_observations
                   WHERE approved=0 AND decision IN ('score_filtered','rejected','paused')
                   GROUP BY COALESCE(rejection_reason, decision)
                   ORDER BY signals DESC"""
            ).fetchall()
        return {
            "total_signals": int(totals["total"] or 0),
            "approved": int(totals["approved"] or 0),
            "score_filtered": int(totals["score_filtered"] or 0),
            "risk_rejected": int(totals["rejected"] or 0),
            "paused": int(totals["paused"] or 0),
            "reasons": [dict(row) for row in rows],
        }

    def get_strategy_performance(self) -> List[Dict[str, Any]]:
        """Return comparable performance metrics for each strategy type."""
        with self._lock:
            rows = self._connection.execute(
                """SELECT strategy_type, COUNT(*) AS signals,
                   COALESCE(SUM(approved),0) AS approvals,
                   COALESCE(SUM(entered),0) AS entries,
                   COALESCE(SUM(realized_pnl_sol),0) AS realized_pnl_sol,
                   COALESCE(AVG(max_multiple),0) AS avg_entry_to_max_multiple,
                   COALESCE(AVG(max_drawdown_pct),0) AS avg_max_drawdown_pct,
                   COALESCE(AVG(hold_seconds),0) AS avg_hold_seconds,
                   COALESCE(SUM(fees_sol),0) AS fees_sol,
                   COALESCE(SUM(slippage_sol),0) AS slippage_sol,
                   COALESCE(SUM(false_positive),0) AS false_positives
                   FROM signal_observations GROUP BY strategy_type
                   ORDER BY strategy_type"""
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            entries = int(item["entries"] or 0)
            signals = int(item["signals"] or 0)
            item["approval_rate_pct"] = (item["approvals"] / signals * 100) if signals else 0.0
            item["entry_rate_pct"] = (entries / signals * 100) if signals else 0.0
            item["realized_expectancy_sol"] = (item["realized_pnl_sol"] / entries) if entries else 0.0
            item["false_positive_rate_pct"] = (item["false_positives"] / entries * 100) if entries else 0.0
            result.append(item)
        return result

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
        slippage_sol: float = 0.0,
        realized_pnl_sol: float = 0.0,
        tx_signature: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """INSERT INTO trade_events
                (position_mint,symbol,side,strategy,reason,sol_amount,token_amount,
                 price,fee_sol,slippage_sol,realized_pnl_sol,tx_signature,metadata_json,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                    float(slippage_sol),
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

    def learning_summary(self) -> Dict[str, Any]:
        with self._lock:
            outcome = self._connection.execute(
                "SELECT COUNT(*) AS tokens, COALESCE(SUM(profitable),0) AS profitable FROM token_outcomes"
            ).fetchone()
        return {
            "closed_tokens": int(outcome["tokens"] or 0),
            "profitable_tokens": int(outcome["profitable"] or 0),
        }

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def config_path() -> str:
    """Resolve the configured DB path without importing the global config."""
    return os.getenv("MEME_HUNTER_DB") or default_trade_history_path()


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
        actor_evidence=list(data.get("actor_evidence", [])),
        signal_run_id=data.get("signal_run_id", ""), strategy_types=list(data.get("strategy_types", [])),
        signal_features=dict(data.get("signal_features", {})), max_price=float(data.get("max_price", 0)),
        min_price=float(data.get("min_price", 0)), last_price=float(data.get("last_price", 0)),
        max_drawdown_pct=float(data.get("max_drawdown_pct", 0)),
        total_fees_sol=float(data.get("total_fees_sol", 0)), total_slippage_sol=float(data.get("total_slippage_sol", 0)),
        dca_orders=dca_orders,
        dca_complete=bool(data.get("dca_complete", False)),
        dca_pending_sol=float(data.get("dca_pending_sol", 0.0)), grid_levels=grid_levels,
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
