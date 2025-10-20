# app/core.py

from dataclasses import dataclass
from typing import Protocol, Optional, List, Dict
from datetime import datetime

@dataclass
class Bar:
    ts: int
    open: float
    high: float
    low: float
    close: float
    vol: float

@dataclass
class SignalEvent:
    ts: int
    direction: str  # 'long' | 'short' | 'flat'
    confidence: float  # 0..1
    meta: Dict[str, float]

class DataProvider(Protocol):
    def candles(self, symbol: str, tf: str, limit: int = 400) -> List[Bar]: ...

class Strategy(Protocol):
    def evaluate(self, bars: List[Bar]) -> Optional[SignalEvent]: ...

class ExecutionClient(Protocol):
    def paper_order(self, symbol: str, side: str, qty: float, price_hint: float | None = None) -> Dict: ...
