# ───────────────────────────────────────────────────────────────────────────────
# app/core.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Iterable, Dict, List, Optional


@dataclass
class Bar:
    ts: int  # epoch seconds
    open: float
    high: float
    low: float
    close: float
    volume: float


class Strategy(Protocol):
    """Strategy contract. Stateless or stateful; returns desired position [-1..1]."""

    def on_bar(self, bars: Iterable[Bar]) -> float:
        """Given the most recent bars (oldest→newest), return target exposure in [-1, 1].
        -1 = fully short, 0 = flat, +1 = fully long.
        """
        pass


class DataProvider(Protocol):
    """Kline provider. Implementors must be thread-safe or externally synchronized."""

    def history(self, symbol: str, tf: str, limit: int = 200) -> List[Bar]:
        pass


class ExecutionClient(Protocol):
    """Paper/Live execution surface used by TradingBot."""

    def paper_order(
        self, symbol: str, side: str, qty: float, price_hint: Optional[float] = None
    ) -> Dict:
        pass
