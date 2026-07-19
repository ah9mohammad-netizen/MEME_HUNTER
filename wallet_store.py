"""SQLite repository for the separate whale/KOL wallet list."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from storage_paths import default_wallets_path


class WalletStore:
    """Keep wallet definitions and profitable-token evidence in one small DB."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or os.getenv("WALLETS_DB_PATH") or default_wallets_path()
        if self.path != ":memory:":
            Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._create_schema()

    def _create_schema(self) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS wallets (
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
                )"""
            )
            self._connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_wallets_score
                   ON wallets(profitable_observations DESC,total_pnl_sol DESC)"""
            )

    @staticmethod
    def _now() -> str:
        return datetime.utcnow().isoformat(timespec="seconds") + "Z"

    def ensure_wallet(
        self,
        *,
        address: str,
        name: str = "Tracked wallet",
        source: str = "manual",
        wallet_type: str = "whale",
        min_buy_threshold: float = 0.5,
    ) -> None:
        """Persist a discovered/configured wallet without adding an outcome."""
        address = str(address).strip()
        if len(address) < 32:
            return
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO wallets(address,name,source,wallet_type,min_buy_threshold,last_seen)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(address) DO UPDATE SET name=excluded.name,
                   source=excluded.source,wallet_type=excluded.wallet_type,
                   min_buy_threshold=excluded.min_buy_threshold,last_seen=excluded.last_seen""",
                (address, name, source, wallet_type, float(min_buy_threshold), self._now()),
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
        if len(address) < 32:
            return
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO wallets
                  (address,name,source,wallet_type,min_buy_threshold,observations,
                   profitable_observations,total_pnl_sol,last_token_mint,last_seen,metadata_json)
                  VALUES(?,?,?,?,?,1,?,?,?,?,?)
                  ON CONFLICT(address) DO UPDATE SET name=excluded.name,
                   observations=wallets.observations+1,
                   profitable_observations=wallets.profitable_observations+excluded.profitable_observations,
                   total_pnl_sol=wallets.total_pnl_sol+excluded.total_pnl_sol,
                   last_token_mint=excluded.last_token_mint,last_seen=excluded.last_seen,
                   metadata_json=excluded.metadata_json""",
                (
                    address,
                    name,
                    source,
                    wallet_type,
                    float(min_buy_threshold),
                    int(profitable),
                    float(pnl_sol),
                    token_mint,
                    self._now(),
                    json.dumps(metadata or {}),
                ),
            )

    def get_wallet_candidates(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT * FROM wallets WHERE enabled=1
                   ORDER BY profitable_observations DESC,total_pnl_sol DESC LIMIT ?""",
                (int(limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_wallet(self, address: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM wallets WHERE address=?", (address,)
            ).fetchone()
        return dict(row) if row else None

    def learning_summary(self) -> Dict[str, int]:
        with self._lock:
            row = self._connection.execute(
                """SELECT COUNT(*) AS wallets,
                   COALESCE(SUM(profitable_observations),0) AS profitable_observations
                   FROM wallets WHERE enabled=1 AND source='learned'"""
            ).fetchone()
        return {
            "learned_wallets": int(row["wallets"] or 0),
            "profitable_observations": int(row["profitable_observations"] or 0),
        }

    def close(self) -> None:
        with self._lock:
            self._connection.close()
