# ───────────────────────────────────────────────────────────────────────────────
# app/bots.py
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Deque
from app.core import Bar, Strategy, DataProvider, ExecutionClient

# Global decision log for debugging/monitoring (last 100 decisions)
_decision_log: Deque[Dict] = deque(maxlen=100)


def get_decision_log() -> List[Dict]:
    """Return recent trading decisions for monitoring."""
    return list(_decision_log)


def clear_decision_log() -> None:
    """Clear all trading decisions from the log."""
    _decision_log.clear()


def _log_decision(bot_name: str, symbol: str, decision_type: str, details: Dict) -> None:
    """Log a trading decision for monitoring."""
    _decision_log.append({
        "timestamp": int(time.time()),
        "bot": bot_name,
        "symbol": symbol,
        "type": decision_type,
        **details
    })


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
        starting_allocation: Optional[float] = None,
    ) -> None:
        self.name = name
        self.symbol = symbol
        self.tf = tf
        self.strategy = strategy
        self.data = data
        self.exec = exec_client
        self.allocation = float(allocation)
        # starting_allocation is the FIXED baseline for P&L calculations
        # It never changes, even when rebalancing modifies allocation
        self.starting_allocation = float(starting_allocation) if starting_allocation is not None else float(allocation)
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

        # Log strategy signal
        if target_exp != 0:
            _log_decision(self.name, self.symbol, "signal", {
                "signal": target_exp,
                "price": price,
                "current_position": self.metrics.pos_qty,
                "target_position": target_qty,
                "delta": delta,
                "strategy": type(self.strategy).__name__
            })

        min_notional = 100.0  # don't trade if change is < $100
        if abs(delta) * price < min_notional:
            # still update equity mark-to-market, but skip order
            self.metrics.avg_price = price
            self.metrics.equity = self.metrics.cash + self.metrics.pos_qty * price
            if abs(target_exp) > 0.01:  # Only log if there was a meaningful signal
                _log_decision(self.name, self.symbol, "skip_min_notional", {
                    "signal": target_exp,
                    "delta_notional": abs(delta) * price,
                    "min_required": min_notional,
                    "price": price
                })
            return

        # Trade cooldown: prevent trading more than once per 5 minutes
        now = int(time.time())
        if self._last_trade_ts is not None and (now - self._last_trade_ts) < 300:
            # Still update equity but skip trading
            self.metrics.avg_price = price
            self.metrics.equity = self.metrics.cash + self.metrics.pos_qty * price
            _log_decision(self.name, self.symbol, "skip_cooldown", {
                "signal": target_exp,
                "seconds_since_last_trade": now - self._last_trade_ts,
                "cooldown_remaining": 300 - (now - self._last_trade_ts),
                "price": price
            })
            return

        # Check if trading is paused globally
        try:
            from app import _trading_paused
            if _trading_paused:
                # Still update equity but skip trading
                self.metrics.avg_price = price
                self.metrics.equity = self.metrics.cash + self.metrics.pos_qty * price
                if abs(target_exp) > 0.01:  # Only log if there was a meaningful signal
                    _log_decision(self.name, self.symbol, "skip_trading_paused", {
                        "signal": target_exp,
                        "delta_notional": abs(delta) * price,
                        "price": price,
                        "reason": "Trading is globally paused"
                    })
                return
        except ImportError:
            pass  # If flag not available, continue trading

        if abs(delta) > 1e-9:
            side = "buy" if delta > 0 else "sell"
            trade_qty = abs(delta)
            trade_cost = trade_qty * price

            # 3a) Enforce no leverage: cap buys by available cash
            if side == "buy" and trade_cost > self.metrics.cash:
                trade_qty = self.metrics.cash / price
                delta = trade_qty  # positive
                trade_cost = trade_qty * price

            # 3b) Use limit orders for maker fees (0% vs 0.1% taker)
            # Place limit slightly away from market to increase maker fill probability
            if side == "buy":
                limit_price = price * 0.9995  # 0.05% below market
            else:
                limit_price = price * 1.0005  # 0.05% above market

            # Execute limit order
            result = self.exec.limit_order(self.symbol, side, trade_qty, limit_price)

            # 3c) Update cash/position accounting including fees
            filled_qty = result.get("filled_qty", trade_qty)
            avg_price = result.get("avg_price", limit_price)
            fee = result.get("fee", 0.0)
            is_maker = result.get("is_maker", False)

            if side == "buy":
                total_cost = filled_qty * avg_price + fee  # cost + fees
                self.metrics.cash -= total_cost
                self.metrics.pos_qty += filled_qty
            else:  # sell
                proceeds = filled_qty * avg_price - fee  # proceeds minus fees
                self.metrics.cash += proceeds
                self.metrics.pos_qty -= filled_qty

            self.metrics.trades += 1
            self._last_trade_ts = now  # Update last trade timestamp

            # Log the executed trade
            _log_decision(self.name, self.symbol, "trade_executed", {
                "signal": target_exp,
                "side": side,
                "quantity": filled_qty,
                "limit_price": limit_price,
                "fill_price": avg_price,
                "fee": fee,
                "is_maker": is_maker,
                "notional": filled_qty * avg_price,
                "new_position": self.metrics.pos_qty,
                "strategy": type(self.strategy).__name__
            })

        # 4) Mark-to-market
        self.metrics.avg_price = price
        self.metrics.equity = self.metrics.cash + self.metrics.pos_qty * price

        # 5) Score (EMA of return) with clamp for UI readability
        ret = (self.metrics.equity - self.allocation) / max(1e-9, self.allocation)
        alpha = 0.1
        self.metrics.score = (1 - alpha) * self.metrics.score + alpha * ret
        self.metrics.score = max(-0.2, min(0.2, self.metrics.score))
