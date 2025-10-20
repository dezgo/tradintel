# app/gate_api.py
from typing import List, Dict, Any
import requests
from .signals import Candle

PUBLIC_BASE_URL = "https://api.gateio.ws/api/v4"


def _get(url: str, params: Dict[str, Any] | None = None, timeout: int = 12):
    r = requests.get(url, params=params or {}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def get_candles(pair: str, interval: str, limit: int = 400) -> List[Candle]:
    """
    Gate spot candlesticks: returns newest-first; we reverse to oldest-first.
    interval examples: 15m, 1h, 4h, 1d
    Docs: /spot/candlesticks
    """
    data = _get(
        f"{PUBLIC_BASE_URL}/spot/candlesticks",
        params={"currency_pair": pair, "interval": interval, "limit": limit},
    )
    out: List[Candle] = []
    for item in reversed(data):
        # API format: [t, volume, close, high, low, open] (strings)
        ts = int(item[0])
        if ts > 10_000_000_000:  # ms → s guard
            ts //= 1000
        out.append(Candle(
            ts=ts,
            open=float(item[5]),
            high=float(item[3]),
            low=float(item[4]),
            close=float(item[2]),
            vol=float(item[1]),
        ))
    return out


def get_order_book(pair: str, limit: int = 15) -> Dict[str, Any]:
    """
    Public order book (top N levels).
    Docs: /spot/order_book
    Returns: {"pair", "asks": [(price, amount)...], "bids": [...], "ts": int}
    """
    data = _get(
        f"{PUBLIC_BASE_URL}/spot/order_book",
        params={"currency_pair": pair, "limit": limit},
    )
    # API shape: {"current": "timestamp", "update": "...", "asks": [["p","q"],...], "bids": [["p","q"],...]}
    asks = [(float(p), float(q)) for p, q in data.get("asks", [])]
    bids = [(float(p), float(q)) for p, q in data.get("bids", [])]
    ts = int(data.get("current") or 0)
    return {"pair": pair, "asks": asks, "bids": bids, "ts": ts}


def get_currencies() -> List[Dict[str, Any]]:
    """
    List spot currencies (metadata only, no balances – balances require auth).
    Docs: /spot/currencies
    Returns a simplified list with a few handy fields.
    """
    data = _get(f"{PUBLIC_BASE_URL}/spot/currencies")
    out: List[Dict[str, Any]] = []
    for c in data:
        out.append({
            "currency": c.get("currency"),
            "delisted": bool(c.get("delisted", False)),
            "withdraw_disabled": bool(c.get("withdraw_disabled", False)),
            "deposit_disabled": bool(c.get("deposit_disabled", False)),
            "trade_disabled": bool(c.get("trade_disabled", False)),
            "chain": c.get("chain"),
            "precision": c.get("precision"),
            "withdraw_fixed_fee": c.get("withdraw_fixed_fee"),
            "withdraw_percent_fee": c.get("withdraw_percent_fee"),
            "withdraw_min": c.get("withdraw_min"),
        })
    return out
