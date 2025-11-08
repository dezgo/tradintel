# app/storage.py
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Dict, Iterable, Optional, Tuple

_DB_DEFAULT = os.getenv("BOT_DB", "trading.db")


class Storage:
    """Thread-safe SQLite wrapper for bot state, trades, params, and snapshots."""

    def __init__(self, db_path: str | os.PathLike[str] = _DB_DEFAULT) -> None:
        self.path = str(db_path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init()

    def _init(self) -> None:
        cur = self._conn.cursor()
        cur.execute("PRAGMA user_version")
        ver = int(cur.fetchone()[0])
        if ver < 1:
            cur.executescript(
                """
                BEGIN;
                CREATE TABLE IF NOT EXISTS bots (
                    name TEXT PRIMARY KEY,
                    manager TEXT,
                    symbol TEXT NOT NULL,
                    tf TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    allocation REAL NOT NULL,
                    cash REAL NOT NULL,
                    pos_qty REAL NOT NULL,
                    avg_price REAL NOT NULL,
                    equity REAL NOT NULL,
                    score REAL NOT NULL,
                    trades INTEGER NOT NULL,
                    updated_ts INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    bot_name TEXT NOT NULL REFERENCES bots(name) ON DELETE CASCADE,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    qty REAL NOT NULL,
                    price REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS equity_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    scope TEXT NOT NULL,    -- 'bot' | 'manager' | 'portfolio'
                    name TEXT NOT NULL,
                    equity REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS param_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    bot_name TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    params_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                PRAGMA user_version = 1;
                COMMIT;
                """
            )
            self._conn.commit()

        # Migrate to version 2: add bars table for historical data caching
        if ver < 2:
            cur.executescript(
                """
                BEGIN;
                CREATE TABLE IF NOT EXISTS bars (
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    ts INTEGER NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL,
                    source TEXT NOT NULL,  -- 'gate', 'coingecko', etc.
                    PRIMARY KEY (symbol, timeframe, ts)
                );
                CREATE INDEX IF NOT EXISTS idx_bars_symbol_tf ON bars(symbol, timeframe);
                CREATE INDEX IF NOT EXISTS idx_bars_ts ON bars(ts);
                PRAGMA user_version = 2;
                COMMIT;
                """
            )
            self._conn.commit()

    # ── Trades ────────────────────────────────────────────────────────────────
    def record_trade(
        self, bot_name: str, symbol: str, side: str, qty: float, price: float, ts: Optional[int] = None
    ) -> None:
        ts = int(ts or time.time())
        with self._lock:
            self._conn.execute(
                "INSERT INTO trades(ts, bot_name, symbol, side, qty, price) VALUES(?,?,?,?,?,?)",
                (ts, bot_name, symbol, side, float(qty), float(price)),
            )
            self._conn.commit()

    # ── Bot state ─────────────────────────────────────────────────────────────
    def upsert_bot(
        self,
        *,
        name: str,
        manager: Optional[str],
        symbol: str,
        tf: str,
        strategy: str,
        params: Dict[str, Any],
        allocation: float,
        cash: float,
        pos_qty: float,
        avg_price: float,
        equity: float,
        score: float,
        trades: int,
    ) -> None:
        now = int(time.time())
        pjson = json.dumps(params, separators=(",", ":"))
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO bots(name, manager, symbol, tf, strategy, params_json, allocation, cash, pos_qty, avg_price, equity, score, trades, updated_ts)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(name) DO UPDATE SET
                    manager=excluded.manager,
                    symbol=excluded.symbol,
                    tf=excluded.tf,
                    strategy=excluded.strategy,
                    params_json=excluded.params_json,
                    allocation=excluded.allocation,
                    cash=excluded.cash,
                    pos_qty=excluded.pos_qty,
                    avg_price=excluded.avg_price,
                    equity=excluded.equity,
                    score=excluded.score,
                    trades=excluded.trades,
                    updated_ts=excluded.updated_ts
                """,
                (name, manager, symbol, tf, strategy, pjson, allocation, cash, pos_qty, avg_price, equity, score, trades, now),
            )
            self._conn.commit()

    def load_bots(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT name, manager, symbol, tf, strategy, params_json, allocation, cash, pos_qty, avg_price, equity, score, trades FROM bots"
            )
            rows = cur.fetchall()
        out: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            name, manager, symbol, tf, strategy, pjson, allocation, cash, pos_qty, avg_price, equity, score, trades = r
            out[name] = {
                "manager": manager,
                "symbol": symbol,
                "tf": tf,
                "strategy": strategy,
                "params": json.loads(pjson),
                "allocation": float(allocation),
                "cash": float(cash),
                "pos_qty": float(pos_qty),
                "avg_price": float(avg_price),
                "equity": float(equity),
                "score": float(score),
                "trades": int(trades),
            }
        return out

    # ── Params ────────────────────────────────────────────────────────────────
    def record_params(self, bot_name: str, strategy: str, params: Dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO param_history(ts, bot_name, strategy, params_json) VALUES(?,?,?,?)",
                (int(time.time()), bot_name, strategy, json.dumps(params, separators=(",", ":"))),
            )
            self._conn.commit()

    # ── Equity snapshots ──────────────────────────────────────────────────────
    def snapshot_equity(
        self, *, portfolio_name: str, managers: Iterable[Tuple[str, float]], bots: Iterable[Tuple[str, float]]
    ) -> None:
        ts = int(time.time())
        with self._lock:
            total = 0.0
            for name, eq in managers:
                total += float(eq)
                self._conn.execute(
                    "INSERT INTO equity_history(ts, scope, name, equity) VALUES(?,?,?,?)",
                    (ts, "manager", name, float(eq)),
                )
            for name, eq in bots:
                self._conn.execute(
                    "INSERT INTO equity_history(ts, scope, name, equity) VALUES(?,?,?,?)",
                    (ts, "bot", name, float(eq)),
                )
            self._conn.execute(
                "INSERT INTO equity_history(ts, scope, name, equity) VALUES(?,?,?,?)",
                (ts, "portfolio", portfolio_name, total),
            )
            self._conn.commit()

    # ── Trade queries ──────────────────────────────────────────────────────────
    def list_trades(
            self,
            *,
            limit: int = 100,
            since_id: int | None = None,
            bot_name: str | None = None,
            symbol: str | None = None,
            manager: str | None = None,
    ) -> list[dict]:
        """
        Return recent trades (most recent first) with optional filters.
        """
        sql = [
            "SELECT t.id, t.ts, t.bot_name, b.manager, t.symbol, t.side, t.qty, t.price",
            "FROM trades t LEFT JOIN bots b ON b.name = t.bot_name",
            "WHERE 1=1",
        ]
        args: list = []

        if since_id is not None:
            sql.append("AND t.id > ?")
            args.append(int(since_id))
        if bot_name:
            sql.append("AND t.bot_name = ?")
            args.append(bot_name)
        if symbol:
            sql.append("AND t.symbol = ?")
            args.append(symbol)
        if manager:
            sql.append("AND b.manager = ?")
            args.append(manager)

        sql.append("ORDER BY t.id DESC")
        sql.append("LIMIT ?")
        args.append(int(limit))

        with self._lock:
            cur = self._conn.execute(" ".join(sql), args)
            rows = cur.fetchall()

        return [
            {
                "id": r[0],
                "ts": int(r[1]),
                "bot": r[2],
                "manager": r[3],
                "symbol": r[4],
                "side": r[5],
                "qty": float(r[6]),
                "price": float(r[7]),
            }
            for r in rows
        ]

    def trade_counts(self) -> dict[str, int]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT bot_name, COUNT(*) FROM trades GROUP BY bot_name"
            )
            rows = cur.fetchall()
        return {name: int(cnt) for (name, cnt) in rows}

    # ── Round-trips (buy→sell or sell→buy cycles) ─────────────────────────────
    def list_roundtrips(
            self,
            *,
            limit: int = 100,
            bot_name: str | None = None,
            symbol: str | None = None,
            manager: str | None = None,
            fee_bps: float = 0.0,  # optional fees per side in basis points
    ) -> list[dict]:
        """
        Build closed round-trips from raw trades using FIFO lot matching.
        Works even if the bot never goes net-flat (partial closes produce round-trips).
        Returns most-recent-first up to `limit`.
        """
        sql = [
            "SELECT t.id, t.ts, t.bot_name, b.manager, t.symbol, t.side, t.qty, t.price",
            "FROM trades t LEFT JOIN bots b ON b.name = t.bot_name",
            "WHERE 1=1",
        ]
        args: list = []
        if bot_name:
            sql.append("AND t.bot_name = ?");
            args.append(bot_name)
        if symbol:
            sql.append("AND t.symbol = ?");
            args.append(symbol)
        if manager:
            sql.append("AND b.manager = ?");
            args.append(manager)
        sql.append("ORDER BY t.id ASC")

        with self._lock:
            rows = self._conn.execute(" ".join(sql), args).fetchall()

        from collections import deque
        groups: dict[tuple[str, str], list[tuple]] = {}
        for r in rows:
            _, ts, bot, mng, sym, side, qty, price = r
            groups.setdefault((bot, sym), []).append(
                (int(ts), bot, mng, sym, side.upper(), float(qty), float(price))
            )

        out: list[dict] = []
        fee = float(fee_bps) / 10000.0

        for (bot, sym), trades in groups.items():
            # FIFO lots: each lot = (open_ts, side, remaining_qty, vwap_price, manager_at_open)
            lots: deque[tuple[int, str, float, float, str]] = deque()

            for ts, _bot, manager, _sym, side, qty, price in trades:
                if qty <= 0:
                    continue
                # apply fee as slippage on price (optional)
                px_eff = price * (1 + (fee if side == "BUY" else -fee))

                if not lots or lots[0][1] == side:
                    # same-direction → add a lot (average handled by matching process)
                    lots.append((ts, side, qty, px_eff, manager))
                    continue

                # opposite side → match FIFO
                remain = qty
                while remain > 1e-12 and lots and lots[0][1] != side:
                    open_ts, open_side, lot_qty, lot_px, open_mgr = lots[0]
                    take = min(lot_qty, remain)
                    lot_qty -= take
                    remain -= take

                    # Round-trip from this partial match
                    side_label = "LONG" if open_side == "BUY" else "SHORT"
                    entry_vwap = lot_px
                    exit_vwap = px_eff
                    if side_label == "LONG":
                        pnl = (exit_vwap - entry_vwap) * take
                        pnl_pct = (exit_vwap - entry_vwap) / entry_vwap
                    else:
                        pnl = (entry_vwap - exit_vwap) * take
                        pnl_pct = (entry_vwap - exit_vwap) / entry_vwap

                    out.append({
                        "bot": bot,
                        "manager": open_mgr,
                        "symbol": sym,
                        "side": side_label,
                        "qty": take,
                        "entry_price": entry_vwap,
                        "exit_price": exit_vwap,
                        "pnl": pnl,
                        "pnl_pct": pnl_pct,
                        "open_ts": int(open_ts),
                        "close_ts": int(ts),
                        "duration_s": int(ts - open_ts),
                    })

                    if lot_qty <= 1e-12:
                        lots.popleft()
                    else:
                        lots[0] = (open_ts, open_side, lot_qty, lot_px, open_mgr)

                # any remaining qty becomes a new lot (could be a flip)
                if remain > 1e-12:
                    lots.append((ts, side, remain, px_eff, manager))

        out.sort(key=lambda d: d["close_ts"], reverse=True)
        return out[:limit]

    # ── Open positions (unclosed cycles) ───────────────────────────────────────
    # inside class Storage

    def list_open_positions(
            self,
            *,
            bot_name: str | None = None,
            symbol: str | None = None,
            manager: str | None = None,
            mark_prices: dict[str, float] | None = None,  # optional symbol->price for unrealized PnL
    ) -> list[dict]:
        sql = [
            "SELECT t.ts, t.bot_name, b.manager, t.symbol, t.side, t.qty, t.price",
            "FROM trades t LEFT JOIN bots b ON b.name = t.bot_name",
            "WHERE 1=1",
        ]
        args: list = []
        if bot_name: sql += ["AND t.bot_name = ?"]; args += [bot_name]
        if symbol:   sql += ["AND t.symbol = ?"];   args += [symbol]
        if manager:  sql += ["AND b.manager = ?"];  args += [manager]
        sql += ["ORDER BY t.id ASC"]

        with self._lock:
            rows = self._conn.execute(" ".join(sql), args).fetchall()

        # net position + avg cost per (bot, symbol)
        by_key: dict[tuple[str, str], dict] = {}
        for ts, bot, mng, sym, side, qty, price in rows:
            key = (bot, sym)
            d = by_key.setdefault(key, {"bot": bot, "manager": mng, "symbol": sym,
                                        "net_qty": 0.0, "entry_qty": 0.0, "entry_cost": 0.0,
                                        "open_ts": int(ts)})
            signed = float(qty) if side.upper() == "BUY" else -float(qty)
            prev = d["net_qty"]
            d["net_qty"] = prev + signed
            if prev == 0.0:
                d["open_ts"] = int(ts)
            # maintain avg entry on adds; reduce on partial closes
            if (d["net_qty"] >= 0 and side.upper() == "BUY") or (d["net_qty"] < 0 and side.upper() == "SELL"):
                d["entry_qty"] += float(qty)
                d["entry_cost"] += float(qty) * float(price)
            else:
                reduce_q = min(abs(signed), d["entry_qty"])
                if reduce_q > 0:
                    avg = d["entry_cost"] / d["entry_qty"] if d["entry_qty"] else 0.0
                    d["entry_qty"] -= reduce_q
                    d["entry_cost"] -= reduce_q * avg

        out: list[dict] = []
        for d in by_key.values():
            if abs(d["net_qty"]) < 1e-12:
                continue
            side_lbl = "LONG" if d["net_qty"] > 0 else "SHORT"
            qty = abs(d["net_qty"])
            avg_cost = (d["entry_cost"] / d["entry_qty"]) if d["entry_qty"] else 0.0
            mark = (mark_prices or {}).get(d["symbol"])
            unreal = None
            if mark is not None:
                unreal = (mark - avg_cost) * qty if side_lbl == "LONG" else (avg_cost - mark) * qty
            out.append({
                "bot": d["bot"], "manager": d["manager"], "symbol": d["symbol"],
                "side": side_lbl, "qty": qty, "avg_cost": avg_cost,
                "open_ts": d["open_ts"], "unrealized": unreal
            })
        return sorted(out, key=lambda x: x["open_ts"], reverse=True)

    # ── Historical bars cache ──────────────────────────────────────────────────
    def store_bars(self, symbol: str, timeframe: str, bars: list[tuple[int, float, float, float, float, float]], source: str = "gate") -> None:
        """
        Store historical bars in cache. bars = [(ts, open, high, low, close, volume), ...]
        Uses INSERT OR IGNORE to avoid duplicates.
        """
        with self._lock:
            self._conn.executemany(
                "INSERT OR IGNORE INTO bars(symbol, timeframe, ts, open, high, low, close, volume, source) VALUES(?,?,?,?,?,?,?,?,?)",
                [(symbol, timeframe, int(ts), float(o), float(h), float(l), float(c), float(v), source) for ts, o, h, l, c, v in bars]
            )
            self._conn.commit()

    def get_bars(self, symbol: str, timeframe: str, start_ts: int | None = None, end_ts: int | None = None, limit: int | None = None) -> list[dict]:
        """
        Retrieve cached bars for symbol+timeframe, optionally filtered by time range.
        Returns list of dicts sorted by timestamp (oldest first).
        """
        sql = ["SELECT ts, open, high, low, close, volume, source FROM bars WHERE symbol = ? AND timeframe = ?"]
        args: list = [symbol, timeframe]

        if start_ts is not None:
            sql.append("AND ts >= ?")
            args.append(int(start_ts))
        if end_ts is not None:
            sql.append("AND ts <= ?")
            args.append(int(end_ts))

        sql.append("ORDER BY ts ASC")

        if limit is not None:
            sql.append("LIMIT ?")
            args.append(int(limit))

        with self._lock:
            cur = self._conn.execute(" ".join(sql), args)
            rows = cur.fetchall()

        return [
            {
                "ts": int(r[0]),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[5]),
                "source": r[6],
            }
            for r in rows
        ]

    def get_bar_coverage(self, symbol: str, timeframe: str) -> dict[str, Any] | None:
        """
        Get coverage statistics for cached bars (min/max timestamp, count).
        Returns None if no bars cached.
        """
        with self._lock:
            cur = self._conn.execute(
                "SELECT MIN(ts), MAX(ts), COUNT(*) FROM bars WHERE symbol = ? AND timeframe = ?",
                (symbol, timeframe)
            )
            row = cur.fetchone()

        if not row or row[2] == 0:
            return None

        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "start_ts": int(row[0]),
            "end_ts": int(row[1]),
            "count": int(row[2]),
        }


store = Storage(_DB_DEFAULT)  # simple singleton
