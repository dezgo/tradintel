# app/managers.py

from typing import List
from dataclasses import dataclass

@dataclass
class Performance:
    pnl: float
    sharpe: float
    drawdown: float

class StrategyManager:
    def __init__(self, name: str, bots: List[TradingBot], min_alloc=0.1, max_alloc=0.8):
        self.name = name
        self.bots = bots
        self.min_alloc = min_alloc
        self.max_alloc = max_alloc

    def step(self):
        for b in self.bots:
            b.step()

    def rebalance_within_team(self):
        # toy rule: weight by last 7d Sharpe (persist later)
        # for now, weight equally if no stats
        total = sum(b.state.cash_alloc for b in self.bots) or 1.0
        target_each = total / len(self.bots)
        for b in self.bots:
            b.state.cash_alloc = target_each

class PortfolioManager:
    def __init__(self, managers: List[StrategyManager]):
        self.managers = managers

    def step(self):
        for m in self.managers:
            m.step()

    def global_rebalance(self):
        # toy: equal weight teams; replace with performance-based
        total = sum(sum(b.state.cash_alloc for b in m.bots) for m in self.managers) or 1.0
        per_team = total / len(self.managers)
        for m in self.managers:
            team_total = sum(b.state.cash_alloc for b in m.bots) or 1.0
            scale = per_team / team_total
            for b in m.bots:
                b.state.cash_alloc *= scale
