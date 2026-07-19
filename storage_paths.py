"""Resolve the two database files inside one Railway volume."""

from __future__ import annotations

import os
from pathlib import Path


VOLUME_DIR = "/data"


def data_dir() -> str:
    """Return the persistent data directory when available.

    Railway mounts its single Volume at ``/data``.  Local development falls
    back to ``./data`` so importing the bot never requires root permissions.
    ``DATA_DIR`` can override either behavior.
    """
    configured = os.getenv("DATA_DIR")
    if configured:
        return configured
    if Path(VOLUME_DIR).is_dir() and os.access(VOLUME_DIR, os.W_OK):
        return VOLUME_DIR
    return "data"


def default_trade_history_path() -> str:
    return str(Path(data_dir()) / "trade_history.db")


def default_wallets_path() -> str:
    return str(Path(data_dir()) / "wallets_list.db")
