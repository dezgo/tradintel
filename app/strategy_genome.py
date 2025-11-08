# ───────────────────────────────────────────────────────────────────────────────
# app/strategy_genome.py
"""
Strategy Genome System - Represents trading strategies as genetic code.

Strategies are defined as flexible rule sets that can mutate and combine:
- Indicators: SMA, EMA, RSI, MACD, Bollinger Bands, ATR
- Conditions: Comparisons between indicators, prices, thresholds
- Logic: AND/OR combinations of conditions
- Parameters: Periods, multipliers, thresholds

This allows infinite strategy variations without writing new Python code.
"""
from __future__ import annotations

import random
import copy
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from collections import deque

from app.core import Bar, Strategy


# ──────────────────────────────────────────────────────────────────────────────
# Indicator Calculators
# ──────────────────────────────────────────────────────────────────────────────

def calculate_sma(values: List[float], period: int) -> Optional[float]:
    """Calculate Simple Moving Average."""
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def calculate_ema(values: List[float], period: int) -> Optional[float]:
    """Calculate Exponential Moving Average."""
    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)
    ema = sum(values[:period]) / period  # Start with SMA

    for value in values[period:]:
        ema = (value - ema) * multiplier + ema

    return ema


def calculate_rsi(values: List[float], period: int = 14) -> Optional[float]:
    """Calculate Relative Strength Index."""
    if len(values) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))

    if len(gains) < period:
        return None

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    return rsi


def calculate_bollinger_bands(values: List[float], period: int, std_dev: float) -> Optional[tuple[float, float, float]]:
    """Calculate Bollinger Bands (lower, middle, upper)."""
    if len(values) < period:
        return None

    recent = values[-period:]
    middle = sum(recent) / period

    variance = sum((x - middle) ** 2 for x in recent) / period
    std = variance ** 0.5

    upper = middle + (std * std_dev)
    lower = middle - (std * std_dev)

    return (lower, middle, upper)


def calculate_atr(bars: List[Bar], period: int = 14) -> Optional[float]:
    """Calculate Average True Range."""
    if len(bars) < period + 1:
        return None

    true_ranges = []
    for i in range(1, len(bars)):
        high = bars[i].high
        low = bars[i].low
        prev_close = bars[i - 1].close

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return None

    return sum(true_ranges[-period:]) / period


# ──────────────────────────────────────────────────────────────────────────────
# Strategy Genome
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class StrategyGenome:
    """
    Genetic representation of a trading strategy.

    Example genome:
    {
        "indicators": [
            {"type": "SMA", "period": 20, "source": "close"},
            {"type": "RSI", "period": 14},
            {"type": "BB", "period": 20, "std_dev": 2.0}
        ],
        "entry_long": {
            "conditions": [
                {"type": "indicator_compare", "left": "RSI", "op": "<", "right": 30},
                {"type": "price_compare", "left": "close", "op": ">", "right": "BB_lower"}
            ],
            "logic": "AND"
        },
        "exit_long": {
            "conditions": [
                {"type": "indicator_compare", "left": "RSI", "op": ">", "right": 70}
            ],
            "logic": "OR"
        },
        "confirm_bars": 2
    }
    """
    indicators: List[Dict[str, Any]] = field(default_factory=list)
    entry_long: Dict[str, Any] = field(default_factory=dict)
    exit_long: Dict[str, Any] = field(default_factory=dict)
    entry_short: Dict[str, Any] = field(default_factory=dict)
    exit_short: Dict[str, Any] = field(default_factory=dict)
    confirm_bars: int = 2

    def to_dict(self) -> Dict[str, Any]:
        """Convert genome to dictionary."""
        return {
            "indicators": self.indicators,
            "entry_long": self.entry_long,
            "exit_long": self.exit_long,
            "entry_short": self.entry_short,
            "exit_short": self.exit_short,
            "confirm_bars": self.confirm_bars,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> StrategyGenome:
        """Create genome from dictionary."""
        return cls(
            indicators=data.get("indicators", []),
            entry_long=data.get("entry_long", {}),
            exit_long=data.get("exit_long", {}),
            entry_short=data.get("entry_short", {}),
            exit_short=data.get("exit_short", {}),
            confirm_bars=data.get("confirm_bars", 2),
        )

    def mutate(self) -> StrategyGenome:
        """Create a mutated copy of this genome."""
        new_genome = copy.deepcopy(self)

        # Choose mutation type randomly
        mutation = random.choice([
            "add_indicator",
            "remove_indicator",
            "modify_indicator",
            "modify_condition",
            "modify_threshold",
            "modify_confirm_bars",
        ])

        if mutation == "add_indicator":
            new_genome._add_random_indicator()
        elif mutation == "remove_indicator" and len(new_genome.indicators) > 1:
            new_genome.indicators.pop(random.randint(0, len(new_genome.indicators) - 1))
        elif mutation == "modify_indicator" and new_genome.indicators:
            idx = random.randint(0, len(new_genome.indicators) - 1)
            new_genome._mutate_indicator(idx)
        elif mutation == "modify_condition":
            new_genome._mutate_condition()
        elif mutation == "modify_threshold":
            new_genome._mutate_threshold()
        elif mutation == "modify_confirm_bars":
            new_genome.confirm_bars = random.randint(1, 5)

        return new_genome

    def _add_random_indicator(self):
        """Add a random indicator to the genome."""
        indicator_type = random.choice(["SMA", "EMA", "RSI", "BB", "ATR"])

        if indicator_type in ["SMA", "EMA"]:
            self.indicators.append({
                "type": indicator_type,
                "period": random.choice([10, 20, 50, 100, 200]),
                "source": random.choice(["close", "high", "low"])
            })
        elif indicator_type == "RSI":
            self.indicators.append({
                "type": "RSI",
                "period": random.choice([7, 14, 21, 28])
            })
        elif indicator_type == "BB":
            self.indicators.append({
                "type": "BB",
                "period": random.choice([10, 20, 30]),
                "std_dev": random.choice([1.5, 2.0, 2.5, 3.0])
            })
        elif indicator_type == "ATR":
            self.indicators.append({
                "type": "ATR",
                "period": random.choice([7, 14, 21])
            })

    def _mutate_indicator(self, idx: int):
        """Mutate an existing indicator's parameters."""
        indicator = self.indicators[idx]

        if "period" in indicator:
            # Mutate period by ±20%
            current = indicator["period"]
            indicator["period"] = max(5, current + random.randint(-int(current * 0.2), int(current * 0.2)))

        if "std_dev" in indicator:
            # Mutate std_dev
            indicator["std_dev"] = max(1.0, indicator["std_dev"] + random.uniform(-0.5, 0.5))

    def _mutate_condition(self):
        """Mutate entry/exit conditions."""
        # For simplicity, just flip the logic
        if random.random() < 0.5 and self.entry_long.get("logic"):
            self.entry_long["logic"] = "OR" if self.entry_long["logic"] == "AND" else "AND"

    def _mutate_threshold(self):
        """Mutate threshold values in conditions."""
        for condition_set in [self.entry_long, self.exit_long]:
            if "conditions" in condition_set:
                for condition in condition_set["conditions"]:
                    if "right" in condition and isinstance(condition["right"], (int, float)):
                        # Mutate numeric thresholds
                        current = condition["right"]
                        condition["right"] = current + random.uniform(-10, 10)

    @classmethod
    def crossover(cls, parent1: StrategyGenome, parent2: StrategyGenome) -> StrategyGenome:
        """Create a child genome by combining two parents."""
        child = cls()

        # Mix indicators from both parents
        all_indicators = parent1.indicators + parent2.indicators
        num_indicators = min(len(all_indicators), random.randint(2, 5))
        child.indicators = random.sample(all_indicators, num_indicators)

        # Randomly choose entry/exit logic from either parent
        child.entry_long = copy.deepcopy(random.choice([parent1.entry_long, parent2.entry_long]))
        child.exit_long = copy.deepcopy(random.choice([parent1.exit_long, parent2.exit_long]))

        # Mix confirm_bars
        child.confirm_bars = random.choice([parent1.confirm_bars, parent2.confirm_bars])

        return child


# ──────────────────────────────────────────────────────────────────────────────
# Genome-based Strategy Executor
# ──────────────────────────────────────────────────────────────────────────────

class GenomeStrategy(Strategy):
    """Executes a strategy based on a genome definition."""

    def __init__(self, genome: StrategyGenome):
        self.genome = genome
        self.confirm_bars = genome.confirm_bars

        # State tracking
        self.bars_buffer: deque = deque(maxlen=300)  # Keep last 300 bars
        self.signal_count = 0
        self.current_signal = 0.0

    def on_bar(self, bars: List[Bar]) -> float:
        """Process bars and return target exposure (-1 to +1)."""
        # Update buffer
        for bar in bars:
            self.bars_buffer.append(bar)

        if len(self.bars_buffer) < 50:  # Need minimum bars for indicators
            return 0.0

        # Calculate all indicators
        indicator_values = self._calculate_indicators()

        # Evaluate entry/exit conditions
        raw_signal = 0.0

        if self._evaluate_conditions(self.genome.entry_long, indicator_values):
            raw_signal = 1.0
        elif self._evaluate_conditions(self.genome.exit_long, indicator_values):
            raw_signal = 0.0

        # Require confirmation
        if raw_signal == self.current_signal:
            self.signal_count += 1
        else:
            self.signal_count = 1
            self.current_signal = raw_signal

        if self.signal_count >= self.confirm_bars:
            return raw_signal

        # Return previous signal if not confirmed
        return self.current_signal if self.signal_count > 0 else 0.0

    def _calculate_indicators(self) -> Dict[str, Any]:
        """Calculate all indicators defined in the genome."""
        values = {}
        bars_list = list(self.bars_buffer)
        closes = [b.close for b in bars_list]
        highs = [b.high for b in bars_list]
        lows = [b.low for b in bars_list]

        for indicator in self.genome.indicators:
            ind_type = indicator["type"]

            if ind_type == "SMA":
                period = indicator["period"]
                source = indicator.get("source", "close")
                source_data = closes if source == "close" else (highs if source == "high" else lows)
                values[f"SMA_{period}"] = calculate_sma(source_data, period)

            elif ind_type == "EMA":
                period = indicator["period"]
                source = indicator.get("source", "close")
                source_data = closes if source == "close" else (highs if source == "high" else lows)
                values[f"EMA_{period}"] = calculate_ema(source_data, period)

            elif ind_type == "RSI":
                period = indicator["period"]
                values[f"RSI"] = calculate_rsi(closes, period)

            elif ind_type == "BB":
                period = indicator["period"]
                std_dev = indicator["std_dev"]
                bb = calculate_bollinger_bands(closes, period, std_dev)
                if bb:
                    values["BB_lower"], values["BB_middle"], values["BB_upper"] = bb

            elif ind_type == "ATR":
                period = indicator["period"]
                values["ATR"] = calculate_atr(bars_list, period)

        # Add current price
        if bars_list:
            values["close"] = bars_list[-1].close
            values["high"] = bars_list[-1].high
            values["low"] = bars_list[-1].low

        return values

    def _evaluate_conditions(self, rule: Dict[str, Any], indicators: Dict[str, Any]) -> bool:
        """Evaluate a set of conditions (entry or exit rule)."""
        if not rule or "conditions" not in rule:
            return False

        conditions = rule["conditions"]
        logic = rule.get("logic", "AND")

        results = []
        for condition in conditions:
            result = self._evaluate_single_condition(condition, indicators)
            results.append(result)

        if logic == "AND":
            return all(results)
        else:  # OR
            return any(results)

    def _evaluate_single_condition(self, condition: Dict[str, Any], indicators: Dict[str, Any]) -> bool:
        """Evaluate a single condition."""
        cond_type = condition.get("type")

        if cond_type == "indicator_compare":
            left_name = condition["left"]
            right_value = condition["right"]
            op = condition["op"]

            left_val = indicators.get(left_name)
            if left_val is None:
                return False

            # Right can be indicator name or numeric value
            if isinstance(right_value, str):
                right_val = indicators.get(right_value)
                if right_val is None:
                    return False
            else:
                right_val = right_value

            return self._compare(left_val, op, right_val)

        elif cond_type == "price_compare":
            left_name = condition["left"]  # e.g., "close"
            right_name = condition["right"]  # e.g., "SMA_20"
            op = condition["op"]

            left_val = indicators.get(left_name)
            right_val = indicators.get(right_name)

            if left_val is None or right_val is None:
                return False

            return self._compare(left_val, op, right_val)

        return False

    def _compare(self, left: float, op: str, right: float) -> bool:
        """Compare two values."""
        if op == ">":
            return left > right
        elif op == "<":
            return left < right
        elif op == ">=":
            return left >= right
        elif op == "<=":
            return left <= right
        elif op == "==":
            return abs(left - right) < 1e-9
        else:
            return False

    def to_params(self) -> dict:
        """Return genome as parameters."""
        return self.genome.to_dict()
