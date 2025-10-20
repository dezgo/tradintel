# app/strategies/mean_reversion.py

from typing import Optional, List
from app.core import Strategy, SignalEvent, Bar
from app.signals import rsi, ema  # reuse your helpers


class MeanReversion(Strategy):
    def __init__(self, rsi_len=14, lo=30, hi=70):
        self.rsi_len = rsi_len
        self.lo = lo
        self.hi = hi

    def evaluate(self, bars: List[Bar]) -> Optional[SignalEvent]:
        if len(bars) < self.rsi_len + 3:
            return None
        closes = [b.close for b in bars]
        r = rsi(closes, self.rsi_len)
        cur = r[-2]  # last CLOSED bar
        if cur < self.lo:
            return SignalEvent(ts=bars[-2].ts, direction="long", confidence=min(1.0, (self.lo - cur)/20), meta={"rsi": cur})
        if cur > self.hi:
            return SignalEvent(ts=bars[-2].ts, direction="short", confidence=min(1.0, (cur - self.hi)/20), meta={"rsi": cur})
        return None
