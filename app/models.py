from __future__ import annotations

import sqlite3
from pathlib import Path
from datetime import datetime


_DB_PATH = Path("trading.db")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            bot TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,   -- buy/sell
            qty REAL NOT NULL,
            price REAL NOT NULL
        )
        """
    )
    conn.commit()


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
    return conn


def insert_trade(bot: str, symbol: str, side: str, qty: float, price: float) -> None:
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO trades (ts, bot, symbol, side, qty, price) VALUES (?, ?, ?, ?, ?, ?)",
            (datetime.utcnow().isoformat(timespec="seconds"), bot, symbol, side, qty, price),
        )
        conn.commit()
    finally:
        conn.close()


def recent_trades(limit: int = 50) -> list[dict]:
    conn = get_db()
    try:
        cur = conn.execute(
            "SELECT id, ts, bot, symbol, side, qty, price FROM trades ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()
