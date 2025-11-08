# app/data.py
from __future__ import annotations

import time
from typing import List, Tuple, Dict, Any
import requests

from app.core import Bar, DataProvider


class GateAdapter(DataProvider):
    """
    Minimal Gate.io public candlestick adapter.

    - Uses /api/v4/spot/candlesticks
    - Returns oldest→newest list[Bar]
    - Light in-memory cache (per (symbol, tf)) with a short TTL to avoid hammering the API
    """

    BASE_URL = "https://api.gateio.ws/api/v4"
    _TF_MAP = {
        "1m": "1m",
        "3m": "3m",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "1h": "1h",
        "4h": "4h",
        "8h": "8h",
        "1d": "1d",
        "7d": "7d",
    }

    def __init__(self, session: requests.Session | None = None, ttl_seconds: int = 5) -> None:
        self._http = session or requests.Session()
        self._cache: Dict[Tuple[str, str], Tuple[float, List[Bar]]] = {}
        self._ttl = ttl_seconds

    def last_price(self, symbol: str, tf: str = "1m") -> tuple[int, float] | None:
        bars = self.history(symbol, tf, limit=1)
        if not bars:
            return None
        b = bars[-1]
        return (b.ts, b.close)

    def history(self, symbol: str, tf: str, limit: int = 200) -> List[Bar]:
        tf_gate = self._TF_MAP.get(tf)
        if not tf_gate:
            raise ValueError(f"Unsupported timeframe '{tf}'. Allowed: {', '.join(self._TF_MAP)}")

        key = (symbol, tf_gate)
        now = time.time()
        cached = self._cache.get(key)
        if cached and now - cached[0] < self._ttl:
            return cached[1][-limit:]

        url = f"{self.BASE_URL}/spot/candlesticks"
        params = {"currency_pair": symbol, "interval": tf_gate, "limit": str(limit)}
        try:
            r = self._http.get(url, params=params, timeout=10)
            if r.status_code == 429:
                # Back off briefly on rate limit
                time.sleep(1.5)
                r = self._http.get(url, params=params, timeout=10)
            r.raise_for_status()
        except requests.RequestException as exc:
            # On network/API failure, return last good cache if present
            if cached:
                return cached[1][-limit:]
            # Otherwise, surface the error (your UI will just skip a step)
            raise RuntimeError(f"Gate.io fetch failed: {exc}") from exc

        raw = r.json()
        bars = self._parse_bars(raw)
        # Gate returns newest→oldest; ensure oldest→newest
        bars.sort(key=lambda b: b.ts)

        self._cache[key] = (now, bars)
        return bars[-limit:]

    @staticmethod
    def _parse_bars(raw: Any) -> List[Bar]:
        """
        Gate.io /spot/candlesticks typically returns a list of arrays of strings, newest first:
        [
          ["1706745600","open","close","high","low","base_vol","quote_vol"],
          ...
        ]
        Some client libs return dicts; we handle both.
        """
        out: List[Bar] = []
        for row in raw:
            if isinstance(row, list) and len(row) >= 6:
                # ts, open, close, high, low, volume
                ts = int(float(row[0]))
                o = float(row[1])
                c = float(row[2])
                h = float(row[3])
                l = float(row[4])
                v = float(row[5])
            elif isinstance(row, dict):
                # Be defensive about key names
                ts = int(float(row.get("t") or row.get("time") or row.get("timestamp")))
                o = float(row.get("o") or row.get("open"))
                c = float(row.get("c") or row.get("close"))
                h = float(row.get("h") or row.get("high"))
                l = float(row.get("l") or row.get("low"))
                v = float(row.get("v") or row.get("volume") or 0)
            else:
                continue

            out.append(Bar(ts=ts, open=o, high=h, low=l, close=c, volume=v))
        return out
