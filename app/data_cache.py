# app/data_cache.py
from __future__ import annotations

import time
from typing import List, Dict
import requests

from app.core import Bar, DataProvider
from app.storage import store


class CachedDataProvider(DataProvider):
    """
    Wraps any DataProvider and caches historical bars in SQLite.

    - Checks cache first
    - Fetches missing bars from underlying provider
    - Stores new bars in cache
    - Historical data never changes, so cache never expires
    """

    def __init__(self, provider: DataProvider, source_name: str = "gate"):
        self.provider = provider
        self.source_name = source_name

    def last_price(self, symbol: str, tf: str = "1m") -> tuple[int, float] | None:
        """Always fetch live price from underlying provider (don't cache)."""
        return self.provider.last_price(symbol, tf)

    def history(self, symbol: str, tf: str, limit: int = 200) -> List[Bar]:
        """
        Get historical bars, using cache when possible.

        Strategy:
        1. Check what we have in cache
        2. If cache has enough recent bars, use cache
        3. Otherwise, fetch from provider and cache the results
        """
        now = int(time.time())

        # Check cache coverage
        coverage = store.get_bar_coverage(symbol, tf)

        # If we have cache and it's recent enough, use it
        if coverage and coverage['count'] >= limit:
            # Get most recent bars from cache
            cached = store.get_bars(symbol, tf, limit=limit * 2)  # Get extra to ensure we have enough
            if len(cached) >= limit:
                # Cache hit! Convert to Bar objects
                bars = [
                    Bar(
                        ts=b['ts'],
                        open=b['open'],
                        high=b['high'],
                        low=b['low'],
                        close=b['close'],
                        volume=b['volume']
                    )
                    for b in cached[-limit:]  # Take most recent
                ]
                return bars

        # Cache miss or insufficient data - fetch from provider
        bars = self.provider.history(symbol, tf, limit=limit)

        # Store in cache for future use
        if bars:
            bar_tuples = [
                (b.ts, b.open, b.high, b.low, b.close, b.volume)
                for b in bars
            ]
            store.store_bars(symbol, tf, bar_tuples, source=self.source_name)

        return bars


class CoinGeckoAdapter(DataProvider):
    """
    CoinGecko API adapter for daily historical data.

    - Free API, no key required
    - Daily OHLCV data for major cryptocurrencies
    - Good for long-term backtests (years of data)
    - Rate limit: 10-30 calls/minute (free tier)

    Symbol mapping:
    - BTC_USDT -> bitcoin
    - ETH_USDT -> ethereum
    - SOL_USDT -> solana
    """

    BASE_URL = "https://api.coingecko.com/api/v3"

    # Map exchange symbols to CoinGecko IDs
    SYMBOL_MAP = {
        "BTC_USDT": "bitcoin",
        "ETH_USDT": "ethereum",
        "SOL_USDT": "solana",
        "BNB_USDT": "binancecoin",
        "XRP_USDT": "ripple",
        "ADA_USDT": "cardano",
        "AVAX_USDT": "avalanche-2",
        "DOGE_USDT": "dogecoin",
        "DOT_USDT": "polkadot",
        "MATIC_USDT": "matic-network",
    }

    def __init__(self, session: requests.Session | None = None):
        self._http = session or requests.Session()
        # Set user agent to avoid 403
        self._http.headers.update({"User-Agent": "TradingBot/1.0"})

    def last_price(self, symbol: str, tf: str = "1m") -> tuple[int, float] | None:
        """CoinGecko doesn't provide minute-level data, only daily."""
        return None

    def history(self, symbol: str, tf: str, limit: int = 200) -> List[Bar]:
        """
        Fetch daily OHLCV data from CoinGecko.

        Note: CoinGecko only provides daily data, so timeframe is ignored.
        """
        # Map symbol to CoinGecko ID
        coin_id = self.SYMBOL_MAP.get(symbol)
        if not coin_id:
            raise ValueError(f"Unsupported symbol '{symbol}' for CoinGecko. Supported: {', '.join(self.SYMBOL_MAP.keys())}")

        # CoinGecko /coins/{id}/ohlc endpoint
        # vs_currency=usd, days=max (or specific number)
        url = f"{self.BASE_URL}/coins/{coin_id}/ohlc"
        params = {
            "vs_currency": "usd",
            "days": str(limit),  # Days of history
        }

        try:
            r = self._http.get(url, params=params, timeout=15)
            r.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f"CoinGecko fetch failed: {exc}") from exc

        raw = r.json()
        bars = self._parse_bars(raw)
        return bars

    @staticmethod
    def _parse_bars(raw: list) -> List[Bar]:
        """
        CoinGecko OHLC format:
        [
          [timestamp_ms, open, high, low, close],
          ...
        ]
        """
        out: List[Bar] = []
        for row in raw:
            if isinstance(row, list) and len(row) >= 5:
                ts = int(row[0] // 1000)  # Convert ms to seconds
                o = float(row[1])
                h = float(row[2])
                l = float(row[3])
                c = float(row[4])
                v = 0.0  # CoinGecko OHLC doesn't include volume in this endpoint

                out.append(Bar(ts=ts, open=o, high=h, low=l, close=c, volume=v))

        # Sort by timestamp (oldest first)
        out.sort(key=lambda b: b.ts)
        return out


def backfill_daily_data(symbols: list[str], days: int = 365) -> dict[str, str]:
    """
    Backfill daily data for multiple symbols from CoinGecko.

    Returns dict mapping symbol -> status message.
    """
    gecko = CoinGeckoAdapter()
    results = {}

    for symbol in symbols:
        try:
            # Fetch daily bars
            bars = gecko.history(symbol, "1d", limit=days)

            if bars:
                # Store in cache
                bar_tuples = [
                    (b.ts, b.open, b.high, b.low, b.close, b.volume)
                    for b in bars
                ]
                store.store_bars(symbol, "1d", bar_tuples, source="coingecko")
                results[symbol] = f"✓ Cached {len(bars)} daily bars"
            else:
                results[symbol] = "✗ No data returned"

            # Be nice to the API
            time.sleep(1.5)

        except Exception as e:
            results[symbol] = f"✗ Error: {str(e)}"

    return results
