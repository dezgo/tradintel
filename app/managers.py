# ───────────────────────────────────────────────────────────────────────────────
# app/managers.py
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict
from app.bots import TradingBot
from app.storage import store


@dataclass
class StrategyManager:
    name: str
    bots: List[TradingBot]
    min_alloc_frac: float = 0.05
    max_alloc_frac: float = 0.80
    _step_counter: int = 0

    def step(self) -> None:
        # 1) Ensure bots exist in DB BEFORE any trades happen
        for b in self.bots:
            store.upsert_bot(
                name=b.name,
                manager=self.name,
                symbol=b.symbol,
                tf=b.tf,
                strategy=type(b.strategy).__name__,
                params=(b.strategy.to_params() if hasattr(b.strategy, "to_params") else {}),
                allocation=b.allocation,
                cash=b.metrics.cash,
                pos_qty=b.metrics.pos_qty,
                avg_price=b.metrics.avg_price,
                equity=b.metrics.equity,
                score=b.metrics.score,
                trades=b.metrics.trades,
            )

        # 2) Run bots (may record trades now that bot rows exist)
        for b in self.bots:
            b.step()

        # 3) Rebalance only every 5 steps (5 minutes) to reduce allocation churn
        if self._step_counter % 5 == 0:
            self._rebalance_within_strategy()
        self._step_counter += 1

        # 4) Persist updated state
        for b in self.bots:
            store.upsert_bot(
                name=b.name,
                manager=self.name,
                symbol=b.symbol,
                tf=b.tf,
                strategy=type(b.strategy).__name__,
                params=(b.strategy.to_params() if hasattr(b.strategy, "to_params") else {}),
                allocation=b.allocation,
                cash=b.metrics.cash,
                pos_qty=b.metrics.pos_qty,
                avg_price=b.metrics.avg_price,
                equity=b.metrics.equity,
                score=b.metrics.score,
                trades=b.metrics.trades,
            )

    def _rebalance_within_strategy(self) -> None:
        scores = [max(0.0, b.metrics.score) for b in self.bots]
        total = sum(scores) or 1.0
        fracs = [s / total for s in scores]
        # clamp
        fracs = [min(self.max_alloc_frac, max(self.min_alloc_frac, f)) for f in fracs]
        # renormalize
        s = sum(fracs)
        fracs = [f / s for f in fracs]
        # apply new target allocations proportionally to current strategy AUM
        strat_equity = sum(b.metrics.equity for b in self.bots)
        for b, f in zip(self.bots, fracs):
            b.allocation = strat_equity * f


@dataclass
class PortfolioManager:
    managers: List[StrategyManager]
    min_alloc_frac: float = 0.10
    max_alloc_frac: float = 0.70
    _step_counter: int = 0

    def step(self) -> None:
        for m in self.managers:
            m.step()
        # Rebalance across strategies only every 5 steps (5 minutes)
        if self._step_counter % 5 == 0:
            self._rebalance_across_strategies()
        self._step_counter += 1

    def snapshot(self) -> Dict:
        counts = store.trade_counts()  # DB authoritative counts

        # Calculate portfolio-level metrics
        all_bots = [b for m in self.managers for b in m.bots]
        total_allocation = sum(b.allocation for b in all_bots)
        total_equity = sum(b.metrics.equity for b in all_bots)
        total_pnl = total_equity - total_allocation

        # Calculate realized P&L from database (excluding stablecoin conversions)
        realized_pnl = store.calculate_realized_pnl(exclude_stablecoin_pairs=True)
        unrealized_pnl = total_pnl - realized_pnl

        # Calculate today's P&L (UTC based for now)
        todays_pnl = store.calculate_todays_pnl()

        return {
            "portfolio_metrics": {
                "starting_capital": total_allocation,
                "current_value": total_equity,
                "total_pnl": total_pnl,
                "realized_pnl": realized_pnl,
                "unrealized_pnl": unrealized_pnl,
                "total_return_pct": (total_pnl / total_allocation * 100) if total_allocation > 0 else 0,
                "todays_pnl": todays_pnl,
            },
            "strategies": [
                {
                    "name": m.name,
                    "equity": sum(b.metrics.equity for b in m.bots),
                    "bots": [
                        {
                            "name": b.name,
                            "symbol": b.symbol,
                            "tf": b.tf,
                            "equity": b.metrics.equity,
                            "score": b.metrics.score,
                            "trades": b.metrics.trades,
                            "trades_db": counts.get(b.name, 0),  # from SQLite
                        }
                        for b in m.bots
                    ],
                }
                for m in self.managers
            ]
        }

    def _rebalance_across_strategies(self) -> None:
        equities = [sum(b.metrics.equity for b in m.bots) for m in self.managers]
        scores = [max(0.0, sum(b.metrics.score for b in m.bots) / max(1, len(m.bots))) for m in self.managers]
        total_score = sum(scores) or 1.0
        fracs = [s / total_score for s in scores]
        fracs = [min(self.max_alloc_frac, max(self.min_alloc_frac, f)) for f in fracs]
        s = sum(fracs)
        fracs = [f / s for f in fracs]
        total_equity = sum(equities)
        targets = [total_equity * f for f in fracs]
        # push targets down to bots proportionally to their current weights inside the strategy
        for m, target in zip(self.managers, targets):
            bot_equities = [b.metrics.equity for b in m.bots]
            subtotal = sum(bot_equities) or 1.0
            for b, eq in zip(m.bots, bot_equities):
                share = eq / subtotal
                b.allocation = target * share

