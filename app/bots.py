# ───────────────────────────────────────────────────────────────────────────────
# app/bots.py
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional
from app.core import Bar, Strategy, DataProvider, ExecutionClient


@dataclass
class BotMetrics:
    equity: float = 0.0
    cash: float = 0.0
    pos_qty: float = 0.0
    avg_price: float = 0.0
    cum_pnl: float = 0.0
    trades: int = 0
    score: float = 0.0  # manager-usable performance score (EMA of returns)


class TradingBot:
    """Atomic bot: single symbol + timeframe + strategy + allocation."""

    def __init__(
        self,
        name: str,
        symbol: str,
        tf: str,
        strategy: Strategy,
        data: DataProvider,
        exec_client: ExecutionClient,
        allocation: float,
        risk_per_trade: float = 0.01,
    ) -> None:
        self.name = name
        self.symbol = symbol
        self.tf = tf
        self.strategy = strategy
        self.data = data
        self.exec = exec_client
        self.allocation = float(allocation)
        self.risk_per_trade = float(risk_per_trade)
        self.metrics = BotMetrics(cash=self.allocation, equity=self.allocation)
        self._last_bar_ts: int | None = None
        self._last_trade_ts: int | None = None  # Track last trade time for cooldown

    # Simplified stepping: compute target exposure, rebalance position notionally
    def step(self) -> None:
        bars: List[Bar] = self.data.history(self.symbol, self.tf, limit=200)
        if not bars:
            return
        last = bars[-1]
        # Only act once per new bar
        if self._last_bar_ts == last.ts:
            return
        self._last_bar_ts = last.ts
        price = last.close

        # 1) Strategy target exposure [-1..1]
        target_exp = self.strategy.on_bar(bars)

        # 2) Target *notional* position and qty with NO leverage
        equity_now = self.metrics.cash + self.metrics.pos_qty * price
        target_notional = equity_now * target_exp
        target_qty = target_notional / max(1e-9, price)

        # 3) Delta to trade
        delta = target_qty - self.metrics.pos_qty

        min_notional = 100.0  # don't trade if change is < $100
        if abs(delta) * price < min_notional:
            # still update equity mark-to-market, but skip order
            self.metrics.avg_price = price
            self.metrics.equity = self.metrics.cash + self.metrics.pos_qty * price
            return

        # Trade cooldown: prevent trading more than once per 5 minutes
        now = int(time.time())
        if self._last_trade_ts is not None and (now - self._last_trade_ts) < 300:
            # Still update equity but skip trading
            self.metrics.avg_price = price
            self.metrics.equity = self.metrics.cash + self.metrics.pos_qty * price
            return

        if abs(delta) > 1e-9:
            side = "buy" if delta > 0 else "sell"
            trade_qty = abs(delta)
            trade_cost = trade_qty * price

            # 3a) Enforce no leverage: cap buys by available cash
            if side == "buy" and trade_cost > self.metrics.cash:
                trade_qty = self.metrics.cash / price
                delta = trade_qty  # positive
                trade_cost = trade_qty * price

            # 3b) Execute + update cash/position
            self.exec.paper_order(self.symbol, side, trade_qty, price_hint=price)
            if side == "buy":
                self.metrics.cash -= trade_cost
                self.metrics.pos_qty += trade_qty
            else:  # sell
                self.metrics.cash += trade_cost
                self.metrics.pos_qty -= trade_qty

            self.metrics.trades += 1
            self._last_trade_ts = now  # Update last trade timestamp

        # 4) Mark-to-market
        self.metrics.avg_price = price
        self.metrics.equity = self.metrics.cash + self.metrics.pos_qty * price

        # 5) Score (EMA of return) with clamp for UI readability
        ret = (self.metrics.equity - self.allocation) / max(1e-9, self.allocation)
        alpha = 0.1
        self.metrics.score = (1 - alpha) * self.metrics.score + alpha * ret
        self.metrics.score = max(-0.2, min(0.2, self.metrics.score))
