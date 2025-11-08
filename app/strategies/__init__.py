# ───────────────────────────────────────────────────────────────────────────────
# app/strategies/__init__.py
from __future__ import annotations

from collections import deque
from typing import Iterable, Deque
from app.core import Bar, Strategy


# ----- Built-in parameter grids (reasonable defaults for 1m/5m/1h) -----
MR_GRID = [
    {"lookback": 20, "band": 2.0},
    {"lookback": 50, "band": 2.0},
    {"lookback": 100, "band": 2.5},
]

BO_GRID = [
    {"lookback": 20},
    {"lookback": 60},
    {"lookback": 120},
]

TF_GRID = [
    {"fast": 10, "slow": 50},
    {"fast": 20, "slow": 100},
    {"fast": 50, "slow": 200},
]


def _sma(vals: Iterable[float], n: int) -> float:
    xs = list(vals)
    return sum(xs[-n:]) / float(n) if len(xs) >= n else sum(xs) / max(1, len(xs))


class MeanReversion(Strategy):
    def __init__(self, lookback: int = 20, band: float = 2.0, confirm_bars: int = 2):
        self.lookback = lookback
        self.band = band
        self.confirm_bars = confirm_bars
        self._closes: Deque[float] = deque(maxlen=max(lookback, 50))
        self._signal_bars: int = 0
        self._current_signal: float = 0.0

    def on_bar(self, bars: Iterable[Bar]) -> float:
        for b in bars:
            self._closes.append(b.close)
        if len(self._closes) < self.lookback:
            return 0.0
        ma = _sma(self._closes, self.lookback)
        # crude stdev proxy
        dev = (_sma([abs(c - ma) for c in self._closes], self.lookback) or 1.0)
        last = self._closes[-1]

        # Calculate raw signal
        raw_signal = 0.0
        if last < ma - self.band * dev:
            raw_signal = +1.0
        elif last > ma + self.band * dev:
            raw_signal = -1.0

        # Require confirmation: same signal for N consecutive bars
        if raw_signal == self._current_signal:
            self._signal_bars += 1
        else:
            self._signal_bars = 1
            self._current_signal = raw_signal

        if self._signal_bars >= self.confirm_bars:
            return raw_signal
        return 0.0

    def to_params(self) -> dict:
        return {"lookback": self.lookback, "band": self.band}


class Breakout(Strategy):
    def __init__(self, lookback: int = 50, confirm_bars: int = 2):
        self.lookback = lookback
        self.confirm_bars = confirm_bars
        self._highs: Deque[float] = deque(maxlen=lookback)
        self._lows: Deque[float] = deque(maxlen=lookback)
        self._signal_bars: int = 0
        self._current_signal: float = 0.0

    def on_bar(self, bars: Iterable[Bar]) -> float:
        for b in bars:
            self._highs.append(b.high)
            self._lows.append(b.low)
        if len(self._highs) < self.lookback:
            return 0.0
        last = bars[-1].close if hasattr(bars, "__getitem__") else list(bars)[-1].close

        # Calculate raw signal
        raw_signal = 0.0
        if last >= max(self._highs):
            raw_signal = +1.0
        elif last <= min(self._lows):
            raw_signal = -1.0

        # Require confirmation: same signal for N consecutive bars
        if raw_signal == self._current_signal:
            self._signal_bars += 1
        else:
            self._signal_bars = 1
            self._current_signal = raw_signal

        if self._signal_bars >= self.confirm_bars:
            return raw_signal
        return 0.0

    def to_params(self) -> dict:
        return {"lookback": self.lookback}


class TrendFollow(Strategy):
    def __init__(self, fast: int = 10, slow: int = 50, confirm_bars: int = 2):
        self.fast = fast
        self.slow = slow
        self.confirm_bars = confirm_bars
        self._closes: Deque[float] = deque(maxlen=max(slow, 200))
        self._signal_bars: int = 0
        self._current_signal: float = 0.0

    def on_bar(self, bars: Iterable[Bar]) -> float:
        for b in bars:
            self._closes.append(b.close)
        if len(self._closes) < self.slow:
            return 0.0
        ma_f = _sma(self._closes, self.fast)
        ma_s = _sma(self._closes, self.slow)

        # Calculate raw signal
        raw_signal = 0.0
        if ma_f > ma_s:
            raw_signal = +1.0
        elif ma_f < ma_s:
            raw_signal = -1.0

        # Require confirmation: same signal for N consecutive bars
        if raw_signal == self._current_signal:
            self._signal_bars += 1
        else:
            self._signal_bars = 1
            self._current_signal = raw_signal

        if self._signal_bars >= self.confirm_bars:
            return raw_signal
        return 0.0

    def to_params(self) -> dict:
        return {"fast": self.fast, "slow": self.slow}
