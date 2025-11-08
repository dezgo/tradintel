# Backtesting Framework

Comprehensive backtesting system for testing trading strategies on historical data.

## Features

- **Performance Metrics**: Sharpe ratio, max drawdown, win rate, profit factor, and more
- **Equity Curve Tracking**: Full equity curve with timestamp resolution
- **Trade Analysis**: Detailed trade-by-trade breakdown with round-trip P&L
- **Flexible Configuration**: Test any strategy/symbol/timeframe combination
- **REST API**: Easy integration via HTTP endpoints

## Quick Start

### 1. Programmatic Usage

```python
from app.backtest import Backtester
from app.strategies import MeanReversion
from app.data import GateAdapter
import time

# Create strategy
strategy = MeanReversion(lookback=50, band=2.0, confirm_bars=2)

# Create backtester
backtester = Backtester(initial_capital=1000.0, min_notional=100.0)

# Run backtest (last 30 days)
end_ts = int(time.time())
start_ts = end_ts - (30 * 86400)

metrics = backtester.run(
    strategy=strategy,
    data_provider=GateAdapter(),
    symbol="BTC_USDT",
    timeframe="5m",
    start_ts=start_ts,
    end_ts=end_ts,
)

print(f"Total Return: {metrics.total_return:.2f}%")
print(f"Sharpe Ratio: {metrics.sharpe_ratio:.2f}")
print(f"Max Drawdown: {metrics.max_drawdown:.2f}%")
print(f"Win Rate: {metrics.win_rate:.2f}%")
```

### 2. REST API Usage

**List Available Strategies:**
```bash
curl http://localhost:5000/backtest/strategies
```

**Run Backtest:**
```bash
curl -X POST http://localhost:5000/backtest \
  -H 'Content-Type: application/json' \
  -d '{
    "strategy": "MeanReversion",
    "params": {"lookback": 50, "band": 2.0, "confirm_bars": 2},
    "symbol": "BTC_USDT",
    "timeframe": "5m",
    "days": 30,
    "initial_capital": 1000,
    "min_notional": 100
  }'
```

**Response:**
```json
{
  "metrics": {
    "total_return": 5.23,
    "sharpe_ratio": 1.45,
    "max_drawdown": 8.12,
    "win_rate": 58.33,
    "profit_factor": 1.82,
    "total_trades": 24,
    "winning_trades": 14,
    "losing_trades": 10,
    "avg_win": 12.45,
    "avg_loss": -8.30,
    "max_consecutive_losses": 3,
    "final_equity": 1052.30,
    "days": 30
  },
  "equity_curve": [
    {"ts": 1234567890, "equity": 1000.00},
    {"ts": 1234567950, "equity": 1005.23},
    ...
  ],
  "trades": [
    {"ts": 1234567890, "side": "buy", "qty": 0.05, "price": 45000.00, "notional": 2250.00},
    ...
  ],
  "config": {
    "strategy": "MeanReversion",
    "params": {"lookback": 50, "band": 2.0},
    "symbol": "BTC_USDT",
    "timeframe": "5m",
    "days": 30,
    "initial_capital": 1000
  }
}
```

### 3. Example Scripts

Run the included examples:

```bash
python backtest_example.py
```

This will run three examples:
1. Single backtest with detailed output
2. Strategy comparison (Mean Reversion vs Breakout vs Trend Follow)
3. Parameter optimization (grid search over lookback/band values)

## Performance Metrics Explained

### Sharpe Ratio
- **What**: Risk-adjusted return metric (annualized)
- **Good**: > 1.0 (excellent > 2.0)
- **Formula**: (Average Return × √Periods) / Std Dev
- **Why**: Measures return per unit of risk

### Max Drawdown
- **What**: Largest peak-to-trough decline (%)
- **Good**: < 15% (excellent < 10%)
- **Formula**: Max((Peak - Valley) / Peak) × 100
- **Why**: Shows worst-case scenario risk

### Win Rate
- **What**: Percentage of profitable trades
- **Good**: > 50% (excellent > 60%)
- **Note**: High win rate doesn't guarantee profitability (need profit factor too)

### Profit Factor
- **What**: Gross profit / Gross loss
- **Good**: > 1.5 (excellent > 2.0)
- **Formula**: Sum(Winning Trades) / |Sum(Losing Trades)|
- **Why**: Shows if winners outweigh losers

### Average Win/Loss
- **What**: Mean P&L per winning/losing trade
- **Good**: Avg Win > |Avg Loss| (preferably 2×)
- **Why**: Important for risk/reward ratio

### Max Consecutive Losses
- **What**: Longest streak of losing trades
- **Why**: Indicates psychological difficulty and risk of ruin

## Strategy Parameters

### Mean Reversion
```python
MeanReversion(
    lookback=50,      # Moving average period (20-200)
    band=2.0,         # Std dev bands (1.5-3.0)
    confirm_bars=2    # Confirmation bars (1-3)
)
```

### Breakout
```python
Breakout(
    lookback=60,      # High/low lookback period (20-120)
    confirm_bars=2    # Confirmation bars (1-3)
)
```

### Trend Follow
```python
TrendFollow(
    fast=20,          # Fast MA period (10-50)
    slow=100,         # Slow MA period (50-200)
    confirm_bars=2    # Confirmation bars (1-3)
)
```

## Best Practices

### 1. Use Appropriate Timeframes
- **1m bars**: Very noisy, lots of false signals
- **5m bars**: Good balance (recommended for most strategies)
- **15m-1h bars**: More stable, fewer trades
- **Daily bars**: Long-term strategies only

### 2. Test Sufficient History
- **Minimum**: 30 days
- **Recommended**: 90 days
- **Ideal**: 180-365 days

### 3. Avoid Overfitting
- Don't optimize on 100% of data
- Use **walk-forward testing**:
  - Optimize on months 1-2
  - Test on month 3
  - Re-optimize on months 2-3
  - Test on month 4
  - Repeat...

### 4. Check Multiple Metrics
Don't just optimize Sharpe ratio! Consider:
- **Sharpe** + **Max Drawdown** (risk/reward balance)
- **Profit Factor** + **Win Rate** (consistency)
- **Total Trades** (statistical significance - need 30+ trades)

### 5. Compare to Buy & Hold
Always compare strategy returns to simply buying and holding.

## Parameter Optimization

### Grid Search (Simple)

```python
from app.backtest import Backtester
from app.strategies import MeanReversion
from app.data import GateAdapter

data = GateAdapter()
best_sharpe = -999
best_params = None

for lookback in [20, 50, 100, 150]:
    for band in [1.5, 2.0, 2.5, 3.0]:
        strategy = MeanReversion(lookback=lookback, band=band)
        backtester = Backtester(initial_capital=1000.0)
        metrics = backtester.run(
            strategy=strategy,
            data_provider=data,
            symbol="BTC_USDT",
            timeframe="5m",
            start_ts=start_ts,
            end_ts=end_ts,
        )

        if metrics.sharpe_ratio > best_sharpe:
            best_sharpe = metrics.sharpe_ratio
            best_params = (lookback, band)

print(f"Best: lookback={best_params[0]}, band={best_params[1]}, Sharpe={best_sharpe:.2f}")
```

### Walk-Forward Optimization (Advanced)

See `app/auto_params.py` for implementation of automatic walk-forward parameter optimization.

## API Reference

### `Backtester` Class

```python
class Backtester:
    def __init__(
        self,
        initial_capital: float = 1000.0,
        min_notional: float = 100.0,
        commission_rate: float = 0.0,  # 0.001 = 0.1% per trade
    )

    def run(
        self,
        strategy: Strategy,
        data_provider: DataProvider,
        symbol: str,
        timeframe: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        lookback: int = 200,
    ) -> BacktestMetrics

    def get_equity_curve(self) -> List[Dict[str, Any]]
    def get_trades(self) -> List[Dict[str, Any]]
```

### `BacktestMetrics` Class

All performance metrics returned from a backtest:

```python
@dataclass
class BacktestMetrics:
    total_return: float        # % return
    sharpe_ratio: float        # Annualized Sharpe
    max_drawdown: float        # % peak-to-trough
    win_rate: float            # % of winning trades
    profit_factor: float       # gross_profit / gross_loss
    total_trades: int          # Total number of trades
    winning_trades: int        # Number of winners
    losing_trades: int         # Number of losers
    avg_win: float             # Average winning trade ($)
    avg_loss: float            # Average losing trade ($)
    avg_trade: float           # Average trade P&L ($)
    max_consecutive_losses: int
    final_equity: float        # Final equity value
    days: int                  # Trading days in backtest
```

## Next Steps: Automated Strategy Discovery

Now that you have backtesting, you can build:

1. **Parameter Optimizer**: Automatically find best parameters for each strategy
2. **Strategy Comparator**: Rank all strategies by performance
3. **Meta-Manager**: Allocate capital based on backtest results
4. **Walk-Forward Engine**: Continuously re-optimize parameters
5. **Strategy Generator**: Create new strategy combinations

See the main README for roadmap details.

## Troubleshooting

### "Not enough data" Error
- Increase `days` parameter
- Use longer timeframe (e.g., 5m instead of 1m)
- Check if symbol has historical data available

### Sharpe Ratio is 0 or NaN
- Not enough trades (need 10+ for meaningful Sharpe)
- All trades have same P&L (no variance)
- Strategy didn't trade at all

### All Trades are Losses
- Strategy may not work on this timeframe/symbol
- Parameters may be poorly tuned
- Try different confirmation bars or timeframe

### Backtest is Slow
- Use longer timeframes (5m instead of 1m)
- Reduce `days` parameter
- Gate.io API has rate limits (1440+ bars may take time)

## Contributing

To add a new strategy:

1. Create strategy class in `app/strategies/`
2. Implement `on_bar()` method
3. Add to strategy map in `app/__init__.py`
4. Test with backtest framework

Example:
```python
class MyStrategy(Strategy):
    def __init__(self, param1: int = 10):
        self.param1 = param1

    def on_bar(self, bars: Iterable[Bar]) -> float:
        # Return -1 (short), 0 (flat), or +1 (long)
        ...
```
