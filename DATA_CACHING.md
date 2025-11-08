# Data Caching System

The trading bot now includes intelligent data caching to improve performance and provide access to historical data beyond what's available from the Gate.io API.

## Features

### 1. **Automatic Caching**
- All historical bars fetched from Gate.io are automatically cached in SQLite
- Subsequent requests use cached data instead of making API calls
- Significantly faster backtests (especially when running multiple parameter combinations)
- Reduces API rate limiting issues

### 2. **Multiple Data Sources**
- **Gate.io**: Primary source for minute/hourly bars (~1000 bars max)
- **CoinGecko**: Daily data for major cryptocurrencies (up to 10 years history)
- Extensible architecture - easy to add more data sources

### 3. **Persistent Storage**
- Cached data stored in the same SQLite database as trades/positions
- Historical bars never change, so cache never expires
- Survives app restarts
- No manual cleanup needed

## Usage

### Web Interface

1. Navigate to `/data-ui` (or click the "Data" button in the navigation)
2. See what data is currently cached
3. Backfill historical data from CoinGecko:
   - Select symbols (BTC, ETH, SOL, etc.)
   - Choose number of days (up to 3650 = 10 years)
   - Click "Fetch Data"

### Programmatic Usage

```python
from app.data import GateAdapter
from app.data_cache import CachedDataProvider, CoinGeckoAdapter, backfill_daily_data

# Use cached Gate.io data
gate = GateAdapter()
cached = CachedDataProvider(gate, source_name="gate")

# This call checks cache first, then fetches from Gate.io if needed
bars = cached.history("BTC_USDT", "5m", limit=1000)

# Backfill daily data from CoinGecko
results = backfill_daily_data(
    symbols=["BTC_USDT", "ETH_USDT", "SOL_USDT"],
    days=365
)
print(results)
# {"BTC_USDT": "✓ Cached 365 daily bars", ...}

# Use CoinGecko directly
gecko = CoinGeckoAdapter()
daily_bars = gecko.history("BTC_USDT", "1d", limit=365)
```

### API Endpoints

#### GET /data/coverage
Get cache coverage for all symbols/timeframes.

```bash
curl http://localhost:5000/data/coverage
```

Response:
```json
{
  "items": [
    {
      "symbol": "BTC_USDT",
      "timeframe": "1d",
      "start_ts": 1609459200,
      "end_ts": 1704067200,
      "count": 365
    }
  ]
}
```

#### POST /data/backfill
Backfill historical data from CoinGecko.

```bash
curl -X POST http://localhost:5000/data/backfill \
  -H 'Content-Type: application/json' \
  -d '{
    "symbols": ["BTC_USDT", "ETH_USDT", "SOL_USDT"],
    "days": 365
  }'
```

Response:
```json
{
  "results": {
    "BTC_USDT": "✓ Cached 365 daily bars",
    "ETH_USDT": "✓ Cached 365 daily bars",
    "SOL_USDT": "✓ Cached 365 daily bars"
  }
}
```

## Data Sources

### Gate.io
- **Timeframes**: 1m, 5m, 15m, 30m, 1h, 4h, 1d
- **Limit**: ~1000 bars per request
- **Available Data**:
  - 1m bars: ~16 hours
  - 5m bars: ~3.5 days
  - 1h bars: ~41 days
  - 1d bars: ~2.7 years
- **Rate Limit**: Unknown, but has 429 handling with backoff

### CoinGecko (Free Tier)
- **Timeframes**: 1d only (daily OHLCV)
- **Symbols**: BTC, ETH, SOL, BNB, XRP, ADA, AVAX, DOGE, DOT, MATIC
- **Limit**: Up to 10 years of daily data
- **Rate Limit**: 10-30 calls/minute
- **Pros**:
  - No API key required
  - Reliable historical data
  - Long history available
- **Cons**:
  - Daily data only (no intraday)
  - Volume data not included in OHLC endpoint

## Architecture

### Database Schema

```sql
CREATE TABLE bars (
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    ts INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    source TEXT NOT NULL,  -- 'gate', 'coingecko', etc.
    PRIMARY KEY (symbol, timeframe, ts)
);
```

### CachedDataProvider Flow

```
history(symbol, tf, limit)
  ↓
Check cache coverage
  ↓
Cache hit? → Return cached bars
  ↓
Cache miss → Fetch from provider
  ↓
Store in cache
  ↓
Return bars
```

## Best Practices

### 1. Backfill Before Backtesting
If you plan to backtest on daily timeframes or need long history:
```python
# One-time setup
backfill_daily_data(["BTC_USDT", "ETH_USDT", "SOL_USDT"], days=730)
```

### 2. Use Cached Provider in Production
The backtester automatically uses `CachedDataProvider`, but if you're building custom tools:
```python
# Good
from app.data_cache import CachedDataProvider
provider = CachedDataProvider(GateAdapter())

# Less efficient
provider = GateAdapter()  # No caching
```

### 3. Monitor Cache Coverage
Check `/data-ui` periodically to see what data you have cached. This helps identify gaps.

### 4. Batch Backfills
When backtesting multiple strategies, backfill data once beforehand rather than letting each backtest fetch it individually:
```python
# Before running 100 backtests
backfill_daily_data(["BTC_USDT"], days=365)

# Now run backtests (all use cached data)
for params in param_grid:
    metrics = backtester.run(...)
```

## Performance Impact

### Without Caching
- Each backtest: 1-2 API calls (~500ms each)
- 100 backtests: 100-200 API calls (~50-100 seconds)
- Risk of hitting rate limits

### With Caching
- First backtest: 1-2 API calls + cache write (~500ms)
- Subsequent backtests: Cache read (~5-10ms)
- 100 backtests: 1-2 API calls total (~1 second)

**Speed improvement: 50-100x for repeated backtests on the same data**

## Adding New Data Sources

To add a new data source (e.g., Binance, Coinbase):

1. Create adapter class implementing `DataProvider` interface:
```python
class BinanceAdapter(DataProvider):
    def last_price(self, symbol: str, tf: str) -> tuple[int, float] | None:
        # Implementation
        pass

    def history(self, symbol: str, tf: str, limit: int) -> List[Bar]:
        # Implementation
        pass
```

2. Wrap it with `CachedDataProvider`:
```python
binance = BinanceAdapter()
cached = CachedDataProvider(binance, source_name="binance")
```

3. Use it in backtests or live trading:
```python
metrics = backtester.run(
    strategy=strategy,
    data_provider=cached,
    symbol="BTC_USDT",
    timeframe="5m"
)
```

## Troubleshooting

### "No cached data yet" message
- You haven't run any backtests or backfilled data
- Solution: Use the backfill form in `/data-ui` or run a backtest (it will auto-cache)

### CoinGecko backfill fails
- Possible reasons:
  - Rate limit exceeded (wait a minute)
  - Symbol not supported (check SYMBOL_MAP in data_cache.py)
  - Network error
- Solution: Check browser console for error details

### Cache growing too large
- SQLite with WAL mode handles large databases well
- If needed, manually delete old data:
```python
from app.storage import store
# Delete bars older than 1 year for 1m timeframe
cutoff = int(time.time()) - (365 * 86400)
store._conn.execute("DELETE FROM bars WHERE timeframe = '1m' AND ts < ?", (cutoff,))
store._conn.commit()
```

## Future Enhancements

Potential improvements to the data caching system:

1. **Incremental Updates**: Fetch only missing bars instead of re-fetching entire range
2. **Multiple Sources Per Symbol**: Try Gate.io first, fall back to CoinGecko if unavailable
3. **Data Quality Checks**: Detect gaps, anomalies, or suspicious bars
4. **Compression**: Store older bars in compressed format to save space
5. **More Data Sources**: Binance, Coinbase, Kraken, etc.
6. **Automatic Backfilling**: Background task that keeps cache up-to-date
7. **Data Export**: Export cached data to CSV/Parquet for external analysis

## Database Migrations

The caching system uses database schema version 2. If you have an existing database (version 1), it will automatically migrate when you start the app:

```python
# Version 1: Only trades, positions, bots
# Version 2: Adds bars table for caching

# Migration happens automatically in Storage._init()
```

To check your database version:
```bash
sqlite3 trading.db "PRAGMA user_version"
# Should show: 2
```

## Summary

The data caching system provides:
- ✅ Faster backtests (50-100x speedup for repeated tests)
- ✅ Reduced API calls and rate limiting
- ✅ Access to long-term historical data (10+ years via CoinGecko)
- ✅ Persistent storage across app restarts
- ✅ Easy web interface for data management
- ✅ Extensible architecture for adding new data sources

Use `/data-ui` to explore and manage your cached data!
