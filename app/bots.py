# app/bots.py

from dataclasses import dataclass, field
from typing import List, Optional
from app.core import Strategy, DataProvider, ExecutionClient, Bar, SignalEvent


@dataclass
class BotState:
    cash_alloc: float
    position_qty: float = 0.0
    avg_price: float = 0.0
    equity: float = 0.0
    last_signal: Optional[SignalEvent] = None


class TradingBot:
    def __init__(self, name: str, symbol: str, tf: str,
                 strategy: Strategy, data: DataProvider, exec_client: ExecutionClient,
                 allocation: float, risk_per_trade: float = 0.01):
        self.name = name
        self.symbol = symbol
        self.tf = tf
        self.strategy = strategy
        self.data = data
        self.exec = exec_client
        self.state = BotState(cash_alloc=allocation)
        self.risk_per_trade = risk_per_trade

    def step(self):
        bars: List[Bar] = self.data.candles(self.symbol, self.tf, limit=400)
        if len(bars) < 50:
            return
        sig = self.strategy.evaluate(bars)
        self.state.last_signal = sig
        if not sig:
            return

        px = bars[-2].close
        # naive sizing: use % of allocation
        trade_cash = self.state.cash_alloc * self.risk_per_trade * sig.confidence
        qty = max(0.0, trade_cash / px)
        if qty <= 0:
            return

        side = "buy" if sig.direction == "long" else ("sell" if sig.direction == "short" else None)
        if not side:
            return

        fill = self.exec.paper_order(self.symbol, side, qty, price_hint=px)
        self._apply_fill(side, qty, fill.get("price", px))

    def _apply_fill(self, side: str, qty: float, price: float):
        # very simple netting model; expand later
        if side == "buy":
            new_qty = self.state.position_qty + qty
            self.state.avg_price = ((self.state.avg_price * self.state.position_qty) + price * qty) / max(new_qty, 1e-9)
            self.state.position_qty = new_qty
            self.state.cash_alloc -= qty * price
        else:
            self.state.position_qty -= qty
            self.state.cash_alloc += qty * price
