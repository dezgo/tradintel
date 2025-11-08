# ───────────────────────────────────────────────────────────────────────────────
# app/genetic_evolution.py
"""
Genetic Evolution Engine - Autonomously discovers trading strategies.

Maintains a population of strategy genomes that evolve over generations:
1. Evaluate fitness of each genome via backtesting
2. Select top performers (low drawdown → high Sharpe → high return)
3. Generate next generation via mutation and crossover
4. Repeat continuously

The system starts with seed strategies and evolves them to find novel
combinations that perform well under current market conditions.
"""
from __future__ import annotations

import time
import random
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from app.strategy_genome import StrategyGenome, GenomeStrategy
from app.backtest import Backtester, BacktestMetrics
from app.data import GateAdapter
from app.data_cache import CachedDataProvider
from app.storage import store


@dataclass
class EvolvedStrategy:
    """An evolved strategy with its fitness score."""
    genome: StrategyGenome
    symbol: str
    timeframe: str
    metrics: BacktestMetrics
    score: float
    generation: int
    tested_ts: int


def calculate_fitness(metrics: BacktestMetrics) -> float:
    """
    Calculate fitness score for a genome (same as optimizer scoring).

    Priority: Low drawdown > High Sharpe > High return
    Score = 100 - Drawdown + (Sharpe × 10) + (Return × 0.1)
    """
    if metrics.total_trades == 0:
        return 0.0  # No trades = worthless

    score = 100.0
    score -= metrics.max_drawdown  # Lower is better
    score += metrics.sharpe_ratio * 10  # Higher is better
    score += metrics.total_return * 0.1  # Higher is better

    return score


def create_seed_genomes() -> List[StrategyGenome]:
    """
    Create initial population of seed genomes.

    These are simple but diverse starting strategies that will evolve
    into more complex and optimized forms.
    """
    seeds = []

    # Seed 1: Simple RSI mean reversion
    genome1 = StrategyGenome(
        indicators=[
            {"type": "RSI", "period": 14}
        ],
        entry_long={
            "conditions": [
                {"type": "indicator_compare", "left": "RSI", "op": "<", "right": 30}
            ],
            "logic": "AND"
        },
        exit_long={
            "conditions": [
                {"type": "indicator_compare", "left": "RSI", "op": ">", "right": 70}
            ],
            "logic": "OR"
        },
        confirm_bars=2
    )
    seeds.append(genome1)

    # Seed 2: SMA crossover with trend
    genome2 = StrategyGenome(
        indicators=[
            {"type": "SMA", "period": 20, "source": "close"},
            {"type": "SMA", "period": 50, "source": "close"}
        ],
        entry_long={
            "conditions": [
                {"type": "price_compare", "left": "close", "op": ">", "right": "SMA_20"},
                {"type": "indicator_compare", "left": "SMA_20", "op": ">", "right": "SMA_50"}
            ],
            "logic": "AND"
        },
        exit_long={
            "conditions": [
                {"type": "price_compare", "left": "close", "op": "<", "right": "SMA_20"}
            ],
            "logic": "OR"
        },
        confirm_bars=2
    )
    seeds.append(genome2)

    # Seed 3: Bollinger Bands bounce
    genome3 = StrategyGenome(
        indicators=[
            {"type": "BB", "period": 20, "std_dev": 2.0},
            {"type": "RSI", "period": 14}
        ],
        entry_long={
            "conditions": [
                {"type": "price_compare", "left": "close", "op": "<", "right": "BB_lower"},
                {"type": "indicator_compare", "left": "RSI", "op": "<", "right": 40}
            ],
            "logic": "AND"
        },
        exit_long={
            "conditions": [
                {"type": "price_compare", "left": "close", "op": ">", "right": "BB_upper"}
            ],
            "logic": "OR"
        },
        confirm_bars=2
    )
    seeds.append(genome3)

    # Seed 4: EMA trend following
    genome4 = StrategyGenome(
        indicators=[
            {"type": "EMA", "period": 20, "source": "close"},
            {"type": "ATR", "period": 14}
        ],
        entry_long={
            "conditions": [
                {"type": "price_compare", "left": "close", "op": ">", "right": "EMA_20"}
            ],
            "logic": "AND"
        },
        exit_long={
            "conditions": [
                {"type": "price_compare", "left": "close", "op": "<", "right": "EMA_20"}
            ],
            "logic": "OR"
        },
        confirm_bars=3
    )
    seeds.append(genome4)

    # Seed 5: Multi-indicator confluence
    genome5 = StrategyGenome(
        indicators=[
            {"type": "SMA", "period": 50, "source": "close"},
            {"type": "RSI", "period": 14},
            {"type": "BB", "period": 20, "std_dev": 2.0}
        ],
        entry_long={
            "conditions": [
                {"type": "price_compare", "left": "close", "op": ">", "right": "SMA_50"},
                {"type": "indicator_compare", "left": "RSI", "op": "<", "right": 50},
                {"type": "price_compare", "left": "close", "op": ">", "right": "BB_lower"}
            ],
            "logic": "AND"
        },
        exit_long={
            "conditions": [
                {"type": "indicator_compare", "left": "RSI", "op": ">", "right": 70}
            ],
            "logic": "OR"
        },
        confirm_bars=2
    )
    seeds.append(genome5)

    return seeds


class GeneticEvolver:
    """Evolves trading strategies using genetic algorithms."""

    def __init__(
        self,
        population_size: int = 20,
        survivors: int = 5,
        mutation_rate: float = 0.7,
        crossover_rate: float = 0.3
    ):
        """
        Initialize genetic evolver.

        Args:
            population_size: Number of genomes in each generation
            survivors: Number of top performers to keep for breeding
            mutation_rate: Probability of mutation when creating offspring
            crossover_rate: Probability of crossover when creating offspring
        """
        self.population_size = population_size
        self.survivors = survivors
        self.mutation_rate = mutation_rate
        self.crossover_rate = crossover_rate

        # Data provider
        self.data_provider = CachedDataProvider(GateAdapter(), source_name="gate")

        # Test configuration
        self.symbols = ["BTC_USDT", "ETH_USDT", "SOL_USDT"]
        self.timeframe = "1d"
        self.days = 365
        self.initial_capital = 1000.0
        self.min_notional = 100.0

        # Current generation
        self.generation = 0
        self.population: List[StrategyGenome] = []

    def initialize_population(self) -> None:
        """Create initial population from seed genomes."""
        seeds = create_seed_genomes()

        # Start with seeds
        self.population = seeds.copy()

        # Fill rest of population with mutated seeds
        while len(self.population) < self.population_size:
            parent = random.choice(seeds)
            mutated = parent.mutate()
            self.population.append(mutated)

        print(f"[Evolution] Initialized population with {len(self.population)} genomes")

    def evaluate_genome(
        self,
        genome: StrategyGenome,
        symbol: str
    ) -> Optional[EvolvedStrategy]:
        """
        Evaluate a genome's fitness by backtesting it.

        Returns EvolvedStrategy with fitness score, or None if backtest failed.
        """
        try:
            # Create strategy from genome
            strategy = GenomeStrategy(genome)

            # Calculate date range
            end_ts = int(time.time())
            start_ts = end_ts - (self.days * 86400)

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

            # Calculate fitness
            score = calculate_fitness(metrics)

            return EvolvedStrategy(
                genome=genome,
                symbol=symbol,
                timeframe=self.timeframe,
                metrics=metrics,
                score=score,
                generation=self.generation,
                tested_ts=int(time.time()),
            )

        except Exception as e:
            print(f"[Evolution]   Evaluation failed: {e}")
            return None

    def evolve_generation(self) -> List[EvolvedStrategy]:
        """
        Evolve one generation across all symbols.

        Returns list of top performers from this generation.
        """
        self.generation += 1
        print(f"\n[Evolution] === Generation {self.generation} ===")

        all_results = []

        # Test each genome on each symbol
        for symbol in self.symbols:
            print(f"[Evolution] Testing {len(self.population)} genomes on {symbol}...")

            symbol_results = []
            for i, genome in enumerate(self.population):
                result = self.evaluate_genome(genome, symbol)

                if result:
                    symbol_results.append(result)

                    if i % 5 == 0:  # Progress update every 5 genomes
                        print(f"[Evolution]   {i+1}/{len(self.population)} genomes tested...")

            # Sort by fitness (best first)
            symbol_results.sort(key=lambda r: r.score, reverse=True)

            # Report top 3 for this symbol
            print(f"[Evolution] Top 3 for {symbol}:")
            for i, result in enumerate(symbol_results[:3], 1):
                print(f"[Evolution]   {i}. Score: {result.score:.1f} (Return: {result.metrics.total_return:.1f}%, Sharpe: {result.metrics.sharpe_ratio:.2f}, DD: {result.metrics.max_drawdown:.1f}%, Trades: {result.metrics.total_trades})")

            all_results.extend(symbol_results)

        # Sort all results by fitness
        all_results.sort(key=lambda r: r.score, reverse=True)

        # Select survivors (top performers across all symbols)
        survivors = all_results[:self.survivors]

        print(f"\n[Evolution] Generation {self.generation} complete!")
        print(f"[Evolution] Top performer overall:")
        if survivors:
            best = survivors[0]
            print(f"[Evolution]   Symbol: {best.symbol}")
            print(f"[Evolution]   Score: {best.score:.1f}")
            print(f"[Evolution]   Return: {best.metrics.total_return:.1f}%")
            print(f"[Evolution]   Sharpe: {best.metrics.sharpe_ratio:.2f}")
            print(f"[Evolution]   Drawdown: {best.metrics.max_drawdown:.1f}%")
            print(f"[Evolution]   Trades: {best.metrics.total_trades}")

        # Create next generation
        self._create_next_generation([s.genome for s in survivors])

        # Save top performers to database
        self._save_evolved_strategies(survivors[:10])  # Save top 10

        return survivors

    def _create_next_generation(self, survivors: List[StrategyGenome]) -> None:
        """
        Create next generation from survivors using mutation and crossover.
        """
        next_population = []

        # Keep survivors
        next_population.extend(survivors)

        # Fill rest with offspring
        while len(next_population) < self.population_size:
            operation = random.random()

            if operation < self.mutation_rate:
                # Mutation: mutate a random survivor
                parent = random.choice(survivors)
                child = parent.mutate()
                next_population.append(child)

            elif operation < self.mutation_rate + self.crossover_rate:
                # Crossover: combine two random survivors
                parent1 = random.choice(survivors)
                parent2 = random.choice(survivors)
                child = StrategyGenome.crossover(parent1, parent2)
                next_population.append(child)

            else:
                # Random mutation of existing population member
                parent = random.choice(next_population)
                child = parent.mutate()
                next_population.append(child)

        self.population = next_population[:self.population_size]
        print(f"[Evolution] Created generation {self.generation + 1} with {len(self.population)} genomes")

    def _save_evolved_strategies(self, strategies: List[EvolvedStrategy]) -> None:
        """Save evolved strategies to database."""
        for strategy in strategies:
            store.save_evolved_strategy(
                genome=strategy.genome.to_dict(),
                symbol=strategy.symbol,
                timeframe=strategy.timeframe,
                score=strategy.score,
                total_return=strategy.metrics.total_return,
                sharpe_ratio=strategy.metrics.sharpe_ratio,
                max_drawdown=strategy.metrics.max_drawdown,
                total_trades=strategy.metrics.total_trades,
                win_rate=strategy.metrics.win_rate,
                generation=strategy.generation,
                days=self.days,
                tested_ts=strategy.tested_ts,
            )

    def run_continuous(self, interval_hours: int = 24) -> None:
        """
        Run evolution continuously in a loop.

        Args:
            interval_hours: Hours between evolution cycles (default: 24)
        """
        print(f"[Evolution] Starting continuous evolution (every {interval_hours}h)")
        print(f"[Evolution] Population size: {self.population_size}")
        print(f"[Evolution] Survivors per generation: {self.survivors}")
        print(f"[Evolution] Mutation rate: {self.mutation_rate}")
        print(f"[Evolution] Crossover rate: {self.crossover_rate}")

        # Initialize population if not already done
        if not self.population:
            self.initialize_population()

        while True:
            try:
                # Evolve one generation
                self.evolve_generation()

                # Sleep until next cycle
                print(f"[Evolution] Sleeping for {interval_hours}h until next generation...")
                time.sleep(interval_hours * 3600)

            except Exception as e:
                print(f"[Evolution] Error in evolution cycle: {e}")
                # Sleep for 1 hour on error before retrying
                time.sleep(3600)
