# app/sources.py
from typing import List, Dict, Any
import requests
from .signals import Candle

GATE_BASE = "https://api.gateio.ws/api/v4"
BINANCE_BASE = "https://api.binance.com"


def _get(url: str, params: Dict[str, Any] | None = None, timeout: int = 12):
    r = requests.get(url, params=params or {}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def get_candles_gate(pair: str, interval: str, limit: int = 500) -> List[Candle]:
    """
    Gate spot candles. API returns newest-first; we reverse to oldest-first.
    Format: [t, volume, close, high, low, open]
    t is seconds since epoch (string), sometimes ms; guard for ms.
    """
    data = _get(
        f"{GATE_BASE}/spot/candlesticks",
        params={"currency_pair": pair, "interval": interval, "limit": limit},
    )
    out: List[Candle] = []
    for item in reversed(data):
        ts = int(item[0])
        if ts > 10_000_000_000:  # ms -> s guard
            ts //= 1000
        out.append(
            Candle(
                ts=ts,
                open=float(item[5]),
                high=float(item[3]),
                low=float(item[4]),
                close=float(item[2]),
                vol=float(item[1]),
            )
        )
    return out


def get_candles_binance(pair: str, interval: str, limit: int = 500) -> List[Candle]:
    """
    Binance spot klines: [openTime, open, high, low, close, volume, closeTime, ...]
    We store ts = openTime (seconds). Pair like 'ETH_USDT' -> 'ETHUSDT'.
    """
    symbol = pair.replace("_", "")
    data = _get(
        f"{BINANCE_BASE}/api/v3/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
    )
    out: List[Candle] = []
    for k in data:
        open_time_ms = int(k[0])
        out.append(
            Candle(
                ts=open_time_ms // 1000,
                open=float(k[1]),
                high=float(k[2]),
                low=float(k[3]),
                close=float(k[4]),
                vol=float(k[5]),
            )
        )
    return out
