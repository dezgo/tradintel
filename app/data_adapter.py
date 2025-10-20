# app/data_adapter.py
from __future__ import annotations

from typing import List
from app.core import Bar
from app import gate_api


class GateAdapter:
    """Thin adapter to satisfy DataProvider.candles() using gate_api.get_candles()."""

    def candles(self, symbol: str, tf: str, limit: int = 400) -> List[Bar]:
        # Your gate_api.get_candles returns newest-first reversed to oldest-first,
        # each item shaped like your signals.Candle dataclass.
        rows = gate_api.get_candles(pair=symbol, interval=tf, limit=limit)
        out: List[Bar] = []
        for r in rows:
            # r.ts is in seconds (your guard already converts ms→s)
            out.append(
                Bar(
                    ts=int(r.ts),
                    open=float(r.open),
                    high=float(r.high),
                    low=float(r.low),
                    close=float(r.close),
                    vol=float(r.vol),
                )
            )
        return out
