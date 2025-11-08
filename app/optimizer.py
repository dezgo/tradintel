# ───────────────────────────────────────────────────────────────────────────────
# app/optimizer.py
"""
Automated strategy optimizer that runs continuously in the background.

Tests all parameter combinations for each strategy and scores them based on:
1. Low drawdown (most important)
2. High Sharpe ratio
3. High return

Automatically saves top performers to the database.
"""
from __future__ import annotations

import time
import itertools
from typing import Dict, List, Any
from dataclasses import dataclass

from app.backtest import Backtester, BacktestMetrics
from app.strategies import MeanReversion, Breakout, TrendFollow, MR_GRID, BO_GRID, TF_GRID
from app.data import GateAdapter
from app.data_cache import CachedDataProvider
from app.storage import store


@dataclass
class OptimizationResult:
    """Result from testing a single parameter combination."""
    strategy: str
    symbol: str
    timeframe: str
    params: Dict[str, Any]
    metrics: BacktestMetrics
    score: float
    tested_ts: int


def calculate_score(metrics: BacktestMetrics) -> float:
    """
    Calculate composite score for a backtest result.

    Priority: Low drawdown > High Sharpe > High return

    Scoring formula:
    - Start with 100 points
    - Subtract drawdown penalty (drawdown is bad)
    - Add Sharpe bonus (Sharpe is good)
    - Add return bonus (return is good, but less important)

    Examples:
    - 5% drawdown, Sharpe 2.0, 50% return → 100 - 5 + 20 + 5 = 120
    - 20% drawdown, Sharpe 1.0, 100% return → 100 - 20 + 10 + 10 = 100
    - 2% drawdown, Sharpe 3.0, 30% return → 100 - 2 + 30 + 3 = 131
    """
    if metrics.total_trades == 0:
        return 0.0  # No trades = worthless

    score = 100.0

    # Drawdown penalty (most important) - weighted 1x
    # Lower drawdown = higher score
    score -= metrics.max_drawdown

    # Sharpe bonus (important) - weighted 10x
    # Higher Sharpe = higher score
    score += metrics.sharpe_ratio * 10

    # Return bonus (least important) - weighted 0.1x
    # Higher return = higher score, but not as much as Sharpe
    score += metrics.total_return * 0.1

    return score


class StrategyOptimizer:
    """Continuously optimizes strategy parameters in the background."""

    def __init__(self):
        self.data_provider = CachedDataProvider(GateAdapter(), source_name="gate")
        self.symbols = ["BTC_USDT", "ETH_USDT", "SOL_USDT"]
        self.timeframe = "1d"
        self.days = 365  # Test on 1 year of data
        self.initial_capital = 1000.0
        self.min_notional = 100.0

        # Strategy configurations
        self.strategies = {
            "MeanReversion": (MeanReversion, MR_GRID),
            "Breakout": (Breakout, BO_GRID),
            "TrendFollow": (TrendFollow, TF_GRID),
        }

    def optimize_strategy(
        self,
        strategy_name: str,
        symbol: str,
    ) -> List[OptimizationResult]:
        """
        Test all parameter combinations for a strategy on a symbol.
        Returns list of results sorted by score (best first).
        """
        if strategy_name not in self.strategies:
            return []

        strategy_class, param_grid = self.strategies[strategy_name]
        results = []

        # Calculate date range
        end_ts = int(time.time())
        start_ts = end_ts - (self.days * 86400)

        print(f"[Optimizer] Testing {strategy_name} on {symbol} with {len(param_grid)} parameter combinations...")

        for params in param_grid:
            try:
                # Add confirm_bars to params if not present
                if "confirm_bars" not in params:
                    params["confirm_bars"] = 2

                # Create strategy instance
                strategy = strategy_class(**params)

                # Run backtest
                backtester = Backtester(
                    initial_capital=self.initial_capital,
                    min_notional=self.min_notional,
                )

                metrics = backtester.run(
                    strategy=strategy,
                    data_provider=self.data_provider,
                    symbol=symbol,
                    timeframe=self.timeframe,
                    start_ts=start_ts,
                    end_ts=end_ts,
                )

                # Calculate score
                score = calculate_score(metrics)

                result = OptimizationResult(
                    strategy=strategy_name,
                    symbol=symbol,
                    timeframe=self.timeframe,
                    params=params,
                    metrics=metrics,
                    score=score,
                    tested_ts=int(time.time()),
                )

                results.append(result)

                print(f"[Optimizer]   {params} → Score: {score:.1f} (Return: {metrics.total_return:.1f}%, Sharpe: {metrics.sharpe_ratio:.2f}, DD: {metrics.max_drawdown:.1f}%, Trades: {metrics.total_trades})")

            except Exception as e:
                print(f"[Optimizer]   {params} → Error: {e}")

        # Sort by score (best first)
        results.sort(key=lambda r: r.score, reverse=True)

        return results

    def run_full_optimization(self) -> Dict[str, List[OptimizationResult]]:
        """
        Run optimization for all strategies on all symbols.
        Returns dict mapping strategy_symbol to top 5 results.
        """
        all_results = {}

        for strategy_name in self.strategies.keys():
            for symbol in self.symbols:
                key = f"{strategy_name}_{symbol}"

                # Run optimization
                results = self.optimize_strategy(strategy_name, symbol)

                # Keep top 5
                top_5 = results[:5]
                all_results[key] = top_5

                # Save to database
                if top_5:
                    print(f"[Optimizer] Top result for {strategy_name} on {symbol}:")
                    best = top_5[0]
                    print(f"[Optimizer]   Score: {best.score:.1f}, Params: {best.params}")
                    print(f"[Optimizer]   Return: {best.metrics.total_return:.1f}%, Sharpe: {best.metrics.sharpe_ratio:.2f}, DD: {best.metrics.max_drawdown:.1f}%")

                    # Store in database
                    self._save_results(top_5)

        return all_results

    def _save_results(self, results: List[OptimizationResult]) -> None:
        """Save optimization results to database."""
        for result in results:
            store.save_optimization_result(
                strategy=result.strategy,
                symbol=result.symbol,
                timeframe=result.timeframe,
                params=result.params,
                score=result.score,
                total_return=result.metrics.total_return,
                sharpe_ratio=result.metrics.sharpe_ratio,
                max_drawdown=result.metrics.max_drawdown,
                total_trades=result.metrics.total_trades,
                win_rate=result.metrics.win_rate,
                tested_ts=result.tested_ts,
            )

    def run_continuous(self, interval_hours: int = 24):
        """
        Run optimization continuously in a loop.
        Re-optimizes every interval_hours (default: 24 hours).
        """
        print(f"[Optimizer] Starting continuous optimization (every {interval_hours}h)")

        while True:
            try:
                print(f"[Optimizer] Starting optimization cycle...")
                self.run_full_optimization()
                print(f"[Optimizer] Optimization cycle complete. Sleeping for {interval_hours}h...")
                time.sleep(interval_hours * 3600)

            except Exception as e:
                print(f"[Optimizer] Error in optimization cycle: {e}")
                # Sleep for 1 hour on error before retrying
                time.sleep(3600)
