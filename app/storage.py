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

        # Migrate to version 3: add saved_backtests table
        if ver < 3:
            cur.executescript(
                """
                BEGIN;
                CREATE TABLE IF NOT EXISTS saved_backtests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    strategy TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    initial_capital REAL NOT NULL,
                    min_notional REAL NOT NULL,
                    created_ts INTEGER NOT NULL
                );
                PRAGMA user_version = 3;
                COMMIT;
                """
            )
            self._conn.commit()

        # Migrate to version 4: add optimization_results table
        if ver < 4:
            cur.executescript(
                """
                BEGIN;
                CREATE TABLE IF NOT EXISTS optimization_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    score REAL NOT NULL,
                    total_return REAL NOT NULL,
                    sharpe_ratio REAL NOT NULL,
                    max_drawdown REAL NOT NULL,
                    total_trades INTEGER NOT NULL,
                    win_rate REAL NOT NULL,
                    tested_ts INTEGER NOT NULL,
                    UNIQUE(strategy, symbol, timeframe, params_json)
                );
                CREATE INDEX IF NOT EXISTS idx_opt_score ON optimization_results(score DESC);
                CREATE INDEX IF NOT EXISTS idx_opt_strategy ON optimization_results(strategy, symbol, timeframe);
                PRAGMA user_version = 4;
                COMMIT;
                """
            )
            self._conn.commit()

        # Migrate to version 5: add days column to saved_backtests and optimization_results
        if ver < 5:
            cur.executescript(
                """
                BEGIN;
                ALTER TABLE saved_backtests ADD COLUMN days INTEGER DEFAULT 365;
                ALTER TABLE optimization_results ADD COLUMN days INTEGER DEFAULT 365;
                PRAGMA user_version = 5;
                COMMIT;
                """
            )
            self._conn.commit()

        # Migrate to version 6: add evolved_strategies table for genetic algorithm results
        if ver < 6:
            cur.executescript(
                """
                BEGIN;
                CREATE TABLE IF NOT EXISTS evolved_strategies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    genome_json TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    score REAL NOT NULL,
                    total_return REAL NOT NULL,
                    sharpe_ratio REAL NOT NULL,
                    max_drawdown REAL NOT NULL,
                    total_trades INTEGER NOT NULL,
                    win_rate REAL NOT NULL,
                    generation INTEGER NOT NULL,
                    days INTEGER NOT NULL,
                    tested_ts INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_evolved_score ON evolved_strategies(score DESC);
                CREATE INDEX IF NOT EXISTS idx_evolved_generation ON evolved_strategies(generation DESC);
                CREATE INDEX IF NOT EXISTS idx_evolved_symbol ON evolved_strategies(symbol, timeframe);
                PRAGMA user_version = 6;
                COMMIT;
                """
            )
            self._conn.commit()

        # Migrate to version 7: add fee tracking to trades table
        if ver < 7:
            cur.executescript(
                """
                BEGIN;
                ALTER TABLE trades ADD COLUMN fee REAL DEFAULT 0.0;
                ALTER TABLE trades ADD COLUMN is_maker INTEGER DEFAULT 0;
                CREATE INDEX IF NOT EXISTS idx_trades_bot ON trades(bot_name, ts DESC);
                PRAGMA user_version = 7;
                COMMIT;
                """
            )
            self._conn.commit()

        # Migrate to version 8: add starting_allocation to track fixed P&L baseline
        if ver < 8:
            cur.executescript(
                """
                BEGIN;
                -- Add starting_allocation column, defaulting to current allocation
                ALTER TABLE bots ADD COLUMN starting_allocation REAL;
                -- Initialize starting_allocation to current allocation for existing bots
                UPDATE bots SET starting_allocation = allocation WHERE starting_allocation IS NULL;
                PRAGMA user_version = 8;
                COMMIT;
                """
            )
            self._conn.commit()

    # ── Trades ────────────────────────────────────────────────────────────────
    def record_trade(
        self,
        bot_name: str,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        ts: Optional[int] = None,
        fee: float = 0.0,
        is_maker: bool = False
    ) -> None:
        ts = int(ts or time.time())
        with self._lock:
            self._conn.execute(
                "INSERT INTO trades(ts, bot_name, symbol, side, qty, price, fee, is_maker) VALUES(?,?,?,?,?,?,?,?)",
                (ts, bot_name, symbol, side, float(qty), float(price), float(fee), int(is_maker)),
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
        starting_allocation: Optional[float] = None,
        cash: float,
        pos_qty: float,
        avg_price: float,
        equity: float,
        score: float,
        trades: int,
    ) -> None:
        now = int(time.time())
        pjson = json.dumps(params, separators=(",", ":"))
        # If starting_allocation not provided, use current allocation (for new bots)
        start_alloc = starting_allocation if starting_allocation is not None else allocation
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO bots(name, manager, symbol, tf, strategy, params_json, allocation, starting_allocation, cash, pos_qty, avg_price, equity, score, trades, updated_ts)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(name) DO UPDATE SET
                    manager=excluded.manager,
                    symbol=excluded.symbol,
                    tf=excluded.tf,
                    strategy=excluded.strategy,
                    params_json=excluded.params_json,
                    allocation=excluded.allocation,
                    starting_allocation=COALESCE(excluded.starting_allocation, bots.starting_allocation, excluded.allocation),
                    cash=excluded.cash,
                    pos_qty=excluded.pos_qty,
                    avg_price=excluded.avg_price,
                    equity=excluded.equity,
                    score=excluded.score,
                    trades=excluded.trades,
                    updated_ts=excluded.updated_ts
                """,
                (name, manager, symbol, tf, strategy, pjson, allocation, start_alloc, cash, pos_qty, avg_price, equity, score, trades, now),
            )
            self._conn.commit()

    def load_bots(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT name, manager, symbol, tf, strategy, params_json, allocation, starting_allocation, cash, pos_qty, avg_price, equity, score, trades FROM bots"
            )
            rows = cur.fetchall()
        out: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            name, manager, symbol, tf, strategy, pjson, allocation, starting_allocation, cash, pos_qty, avg_price, equity, score, trades = r
            out[name] = {
                "manager": manager,
                "symbol": symbol,
                "tf": tf,
                "strategy": strategy,
                "params": json.loads(pjson),
                "allocation": float(allocation),
                "starting_allocation": float(starting_allocation) if starting_allocation is not None else float(allocation),
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
            "SELECT t.id, t.ts, t.bot_name, b.manager, t.symbol, t.side, t.qty, t.price, t.fee, t.is_maker",
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
                "fee": float(r[8] or 0),
                "is_maker": bool(r[9]),
            }
            for r in rows
        ]

    def fee_statistics(
            self,
            *,
            bot_name: str | None = None,
            manager: str | None = None,
    ) -> dict:
        """
        Return fee statistics including total fees, maker/taker breakdown.
        """
        sql_base = [
            "SELECT",
            "  COUNT(*) as total_trades,",
            "  SUM(t.fee) as total_fees,",
            "  SUM(CASE WHEN t.is_maker = 1 THEN t.fee ELSE 0 END) as maker_fees,",
            "  SUM(CASE WHEN t.is_maker = 0 THEN t.fee ELSE 0 END) as taker_fees,",
            "  SUM(CASE WHEN t.is_maker = 1 THEN 1 ELSE 0 END) as maker_count,",
            "  SUM(CASE WHEN t.is_maker = 0 THEN 1 ELSE 0 END) as taker_count,",
            "  SUM(t.qty * t.price) as total_volume",
            "FROM trades t LEFT JOIN bots b ON b.name = t.bot_name",
            "WHERE 1=1",
        ]
        args: list = []

        if bot_name:
            sql_base.append("AND t.bot_name = ?")
            args.append(bot_name)
        if manager:
            sql_base.append("AND b.manager = ?")
            args.append(manager)

        with self._lock:
            cur = self._conn.execute(" ".join(sql_base), args)
            row = cur.fetchone()

        if not row or row[0] == 0:
            return {
                "total_trades": 0,
                "total_fees": 0.0,
                "maker_fees": 0.0,
                "taker_fees": 0.0,
                "maker_count": 0,
                "taker_count": 0,
                "maker_ratio": 0.0,
                "total_volume": 0.0,
                "fee_percentage": 0.0,
            }

        total_trades = int(row[0])
        total_fees = float(row[1] or 0)
        maker_fees = float(row[2] or 0)
        taker_fees = float(row[3] or 0)
        maker_count = int(row[4] or 0)
        taker_count = int(row[5] or 0)
        total_volume = float(row[6] or 0)

        return {
            "total_trades": total_trades,
            "total_fees": total_fees,
            "maker_fees": maker_fees,
            "taker_fees": taker_fees,
            "maker_count": maker_count,
            "taker_count": taker_count,
            "maker_ratio": maker_count / total_trades if total_trades > 0 else 0,
            "total_volume": total_volume,
            "fee_percentage": (total_fees / total_volume * 100) if total_volume > 0 else 0,
        }

    def trade_counts(self) -> dict[str, int]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT bot_name, COUNT(*) FROM trades GROUP BY bot_name"
            )
            rows = cur.fetchall()
        return {name: int(cnt) for (name, cnt) in rows}

    def calculate_realized_pnl(self, exclude_stablecoin_pairs: bool = True) -> float:
        """
        Calculate total realized P&L from closed round-trips.
        Optionally excludes stablecoin-to-stablecoin conversions (USDC_USDT, etc.).
        """
        # Define stablecoin pairs to exclude
        stablecoin_pairs = {'USDC_USDT', 'BUSD_USDT', 'USDT_USDC', 'USDT_BUSD'}

        # Get all round-trips (no limit)
        roundtrips = self.list_roundtrips(limit=100000)

        total_pnl = 0.0
        for rt in roundtrips:
            symbol = rt.get('symbol', '')

            # Skip stablecoin conversions if requested
            if exclude_stablecoin_pairs and symbol in stablecoin_pairs:
                continue

            # Add the P&L from this round-trip
            pnl = rt.get('pnl', 0.0)
            total_pnl += pnl

        return total_pnl

    def calculate_todays_pnl(self) -> float:
        """
        Calculate total P&L from trades executed today (Sydney timezone midnight to now).
        Uses round-trips closed today, excluding stablecoin conversions.
        """
        import datetime
        from zoneinfo import ZoneInfo

        # Calculate today's start timestamp (Sydney timezone midnight)
        sydney_tz = ZoneInfo("Australia/Sydney")
        now = datetime.datetime.now(sydney_tz)
        today_start = datetime.datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=sydney_tz)
        today_ts = int(today_start.timestamp())

        # Define stablecoin pairs to exclude
        stablecoin_pairs = {'USDC_USDT', 'BUSD_USDT', 'USDT_USDC', 'USDT_BUSD'}

        # Get all round-trips
        roundtrips = self.list_roundtrips(limit=100000)

        todays_pnl = 0.0
        for rt in roundtrips:
            # Check if round-trip closed today (use exit_ts)
            exit_ts = rt.get('exit_ts', 0)
            if exit_ts < today_ts:
                continue

            symbol = rt.get('symbol', '')

            # Skip stablecoin conversions
            if symbol in stablecoin_pairs:
                continue

            # Add the P&L from this round-trip
            pnl = rt.get('pnl', 0.0)
            todays_pnl += pnl

        return todays_pnl

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

    # ── Saved backtests ────────────────────────────────────────────────────────
    def save_backtest(self, *, name: str, strategy: str, symbol: str, timeframe: str,
                      params: Dict[str, Any], initial_capital: float, min_notional: float, days: int = 365) -> int:
        """Save a backtest configuration. Returns the saved ID."""
        params_json = json.dumps(params, separators=(",", ":"))
        now = int(time.time())

        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO saved_backtests(name, strategy, symbol, timeframe, params_json, initial_capital, min_notional, days, created_ts)
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(name) DO UPDATE SET
                    strategy=excluded.strategy,
                    symbol=excluded.symbol,
                    timeframe=excluded.timeframe,
                    params_json=excluded.params_json,
                    initial_capital=excluded.initial_capital,
                    min_notional=excluded.min_notional,
                    days=excluded.days,
                    created_ts=excluded.created_ts
                """,
                (name, strategy, symbol, timeframe, params_json, float(initial_capital), float(min_notional), int(days), now)
            )
            self._conn.commit()
            return cur.lastrowid

    def list_saved_backtests(self) -> list[dict]:
        """List all saved backtest configurations."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT id, name, strategy, symbol, timeframe, params_json, initial_capital, min_notional, days, created_ts FROM saved_backtests ORDER BY created_ts DESC"
            )
            rows = cur.fetchall()

        return [
            {
                "id": int(r[0]),
                "name": r[1],
                "strategy": r[2],
                "symbol": r[3],
                "timeframe": r[4],
                "params": json.loads(r[5]),
                "initial_capital": float(r[6]),
                "min_notional": float(r[7]),
                "days": int(r[8]),
                "created_ts": int(r[9]),
            }
            for r in rows
        ]

    def delete_saved_backtest(self, backtest_id: int) -> bool:
        """Delete a saved backtest configuration. Returns True if deleted."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM saved_backtests WHERE id = ?", (int(backtest_id),))
            self._conn.commit()
            return cur.rowcount > 0

    # ── Optimization results ───────────────────────────────────────────────────
    def save_optimization_result(
        self,
        *,
        strategy: str,
        symbol: str,
        timeframe: str,
        params: Dict[str, Any],
        score: float,
        total_return: float,
        sharpe_ratio: float,
        max_drawdown: float,
        total_trades: int,
        win_rate: float,
        days: int,
        tested_ts: int,
    ) -> int:
        """Save an optimization result. Updates if same config exists."""
        params_json = json.dumps(params, separators=(",", ":"))

        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO optimization_results(
                    strategy, symbol, timeframe, params_json, score,
                    total_return, sharpe_ratio, max_drawdown, total_trades, win_rate, days, tested_ts
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(strategy, symbol, timeframe, params_json) DO UPDATE SET
                    score=excluded.score,
                    total_return=excluded.total_return,
                    sharpe_ratio=excluded.sharpe_ratio,
                    max_drawdown=excluded.max_drawdown,
                    total_trades=excluded.total_trades,
                    win_rate=excluded.win_rate,
                    days=excluded.days,
                    tested_ts=excluded.tested_ts
                """,
                (
                    strategy,
                    symbol,
                    timeframe,
                    params_json,
                    float(score),
                    float(total_return),
                    float(sharpe_ratio),
                    float(max_drawdown),
                    int(total_trades),
                    float(win_rate),
                    int(days),
                    int(tested_ts),
                ),
            )
            self._conn.commit()
            return cur.lastrowid

    def list_optimization_results(
        self, strategy: str = None, symbol: str = None, limit: int = 100
    ) -> list[dict]:
        """
        List optimization results, optionally filtered by strategy/symbol.
        Returns top results sorted by score (best first).
        """
        sql = [
            """
            SELECT id, strategy, symbol, timeframe, params_json, score,
                   total_return, sharpe_ratio, max_drawdown, total_trades, win_rate, days, tested_ts
            FROM optimization_results
            WHERE 1=1
            """
        ]
        args = []

        if strategy:
            sql.append("AND strategy = ?")
            args.append(strategy)

        if symbol:
            sql.append("AND symbol = ?")
            args.append(symbol)

        sql.append("ORDER BY score DESC LIMIT ?")
        args.append(int(limit))

        with self._lock:
            cur = self._conn.execute(" ".join(sql), args)
            rows = cur.fetchall()

        return [
            {
                "id": int(r[0]),
                "strategy": r[1],
                "symbol": r[2],
                "timeframe": r[3],
                "params": json.loads(r[4]),
                "score": float(r[5]),
                "total_return": float(r[6]),
                "sharpe_ratio": float(r[7]),
                "max_drawdown": float(r[8]),
                "total_trades": int(r[9]),
                "win_rate": float(r[10]),
                "days": int(r[11]),
                "tested_ts": int(r[12]),
            }
            for r in rows
        ]

    # ── Evolved strategies (genetic algorithm) ─────────────────────────────────
    def save_evolved_strategy(
        self,
        *,
        genome: Dict[str, Any],
        symbol: str,
        timeframe: str,
        score: float,
        total_return: float,
        sharpe_ratio: float,
        max_drawdown: float,
        total_trades: int,
        win_rate: float,
        generation: int,
        days: int,
        tested_ts: int,
    ) -> int:
        """Save an evolved strategy from genetic algorithm."""
        genome_json = json.dumps(genome, separators=(",", ":"))

        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO evolved_strategies(
                    genome_json, symbol, timeframe, score,
                    total_return, sharpe_ratio, max_drawdown, total_trades, win_rate,
                    generation, days, tested_ts
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    genome_json,
                    symbol,
                    timeframe,
                    float(score),
                    float(total_return),
                    float(sharpe_ratio),
                    float(max_drawdown),
                    int(total_trades),
                    float(win_rate),
                    int(generation),
                    int(days),
                    int(tested_ts),
                ),
            )
            self._conn.commit()
            return cur.lastrowid

    def list_evolved_strategies(
        self, symbol: str = None, min_score: float = None, limit: int = 100
    ) -> list[dict]:
        """
        List evolved strategies, optionally filtered by symbol and minimum score.
        Returns top results sorted by score (best first).
        """
        sql = [
            """
            SELECT id, genome_json, symbol, timeframe, score,
                   total_return, sharpe_ratio, max_drawdown, total_trades, win_rate,
                   generation, days, tested_ts
            FROM evolved_strategies
            WHERE 1=1
            """
        ]
        args = []

        if symbol:
            sql.append("AND symbol = ?")
            args.append(symbol)

        if min_score is not None:
            sql.append("AND score >= ?")
            args.append(float(min_score))

        sql.append("ORDER BY score DESC LIMIT ?")
        args.append(int(limit))

        with self._lock:
            cur = self._conn.execute(" ".join(sql), args)
            rows = cur.fetchall()

        return [
            {
                "id": int(r[0]),
                "genome": json.loads(r[1]),
                "symbol": r[2],
                "timeframe": r[3],
                "score": float(r[4]),
                "total_return": float(r[5]),
                "sharpe_ratio": float(r[6]),
                "max_drawdown": float(r[7]),
                "total_trades": int(r[8]),
                "win_rate": float(r[9]),
                "generation": int(r[10]),
                "days": int(r[11]),
                "tested_ts": int(r[12]),
            }
            for r in rows
        ]

    def get_top_evolved_strategies_for_portfolio(self, num_strategies: int = 5, min_score: float = 0.0) -> list[dict]:
        """
        Get top N evolved strategies across all symbols for live trading.
        Only returns profitable strategies (score > min_score).
        Sorted by score (best first).

        Returns: List of dicts with genome, symbol, timeframe, score, etc.
        """
        return self.list_evolved_strategies(
            symbol=None,  # All symbols
            min_score=min_score,
            limit=num_strategies
        )

    def get_evolved_strategy(self, strategy_id: int) -> dict | None:
        """Get a specific evolved strategy by ID."""
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT id, genome_json, symbol, timeframe, score,
                       total_return, sharpe_ratio, max_drawdown, total_trades, win_rate,
                       generation, days, tested_ts
                FROM evolved_strategies
                WHERE id = ?
                """,
                (int(strategy_id),)
            )
            row = cur.fetchone()

        if not row:
            return None

        return {
            "id": int(row[0]),
            "genome": json.loads(row[1]),
            "symbol": row[2],
            "timeframe": row[3],
            "score": float(row[4]),
            "total_return": float(row[5]),
            "sharpe_ratio": float(row[6]),
            "max_drawdown": float(row[7]),
            "total_trades": int(row[8]),
            "win_rate": float(row[9]),
            "generation": int(row[10]),
            "days": int(row[11]),
            "tested_ts": int(row[12]),
        }

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

    # ── Settings ───────────────────────────────────────────────────────────────
    def get_setting(self, key: str, default: Any = None) -> Any:
        """Get a setting value from database. Returns default if not found."""
        with self._lock:
            cur = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cur.fetchone()

        if not row:
            return default

        # Try to parse as JSON, fallback to raw string
        try:
            return json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            return row[0]

    def set_setting(self, key: str, value: Any) -> None:
        """Set a setting value in database. Value will be JSON-encoded."""
        value_json = json.dumps(value) if not isinstance(value, str) else value
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO settings(key, value) VALUES(?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, value_json)
            )
            self._conn.commit()


store = Storage(_DB_DEFAULT)  # simple singleton
