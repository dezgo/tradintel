from __future__ import annotations

import threading
import time
from typing import Dict, Any, List

from app.bots import TradingBot


class Portfolio:
    def __init__(self, data_provider) -> None:
        # Start minimal: 1 bot on BTC/USDT 1h with $1k allocation
        self.bots: List[TradingBot] = [
            TradingBot(
                name="mr_btc_1h",
                symbol="BTC_USDT",
                tf="1h",
                data=data_provider,
                allocation=1000.0,
            )
        ]
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self, interval_sec: int = 30) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, args=(interval_sec,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def _loop(self, interval_sec: int) -> None:
        while self._running:
            for b in self.bots:
                try:
                    b.step()
                except Exception:
                    # keep MVP resilient; add logging later
                    pass
            time.sleep(interval_sec)

    def summary(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"bots": []}
        for b in self.bots:
            s = b.state
            out["bots"].append(
                {
                    "name": b.name,
                    "symbol": b.symbol,
                    "tf": b.tf,
                    "cash_alloc": round(s.cash_alloc, 2),
                    "position_qty": round(s.position_qty, 6),
                    "avg_price": round(s.avg_price, 2),
                    "last_signal": (s.last_signal.direction if s.last_signal else None),
                    "signal_meta": (s.last_signal.meta if s.last_signal else None),
                }
            )
        return out
