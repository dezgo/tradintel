#!/usr/bin/env python3
"""
Example: How to use the backtesting framework.

This script demonstrates:
1. Running a backtest programmatically
2. Testing different strategies and parameters
3. Comparing results
"""

from app.backtest import Backtester
from app.strategies import MeanReversion, Breakout, TrendFollow
from app.data import GateAdapter
import time


def run_single_backtest():
    """Example: Run a single backtest."""
    print("=" * 80)
    print("EXAMPLE 1: Single Backtest - Mean Reversion on BTC")
    print("=" * 80)

    # Create strategy
    strategy = MeanReversion(lookback=50, band=2.0, confirm_bars=2)

    # Create data provider
    data = GateAdapter()

    # Create backtester
    backtester = Backtester(
        initial_capital=1000.0,
        min_notional=100.0,
    )

    # Calculate date range (last 30 days)
    end_ts = int(time.time())
    start_ts = end_ts - (30 * 86400)

    # Run backtest
    print(f"\nBacktesting MeanReversion(lookback=50, band=2.0) on BTC_USDT...")
    print(f"Period: Last 30 days")
    print(f"Initial Capital: $1000")

    metrics = backtester.run(
        strategy=strategy,
        data_provider=data,
        symbol="BTC_USDT",
        timeframe="5m",  # Use 5m bars (more stable than 1m)
        start_ts=start_ts,
        end_ts=end_ts,
    )

    # Print results
    print("\n" + "─" * 80)
    print("RESULTS:")
    print("─" * 80)
    print(f"Total Return:        {metrics.total_return:>8.2f}%")
    print(f"Sharpe Ratio:        {metrics.sharpe_ratio:>8.2f}")
    print(f"Max Drawdown:        {metrics.max_drawdown:>8.2f}%")
    print(f"Win Rate:            {metrics.win_rate:>8.2f}%")
    print(f"Profit Factor:       {metrics.profit_factor:>8.2f}")
    print(f"Total Trades:        {metrics.total_trades:>8}")
    print(f"Winning Trades:      {metrics.winning_trades:>8}")
    print(f"Losing Trades:       {metrics.losing_trades:>8}")
    print(f"Avg Win:             ${metrics.avg_win:>8.2f}")
    print(f"Avg Loss:            ${metrics.avg_loss:>8.2f}")
    print(f"Max Consec Losses:   {metrics.max_consecutive_losses:>8}")
    print(f"Final Equity:        ${metrics.final_equity:>8.2f}")
    print("─" * 80)

    return metrics


def compare_strategies():
    """Example: Compare different strategies on the same symbol."""
    print("\n\n" + "=" * 80)
    print("EXAMPLE 2: Compare Strategies on BTC_USDT")
    print("=" * 80)

    data = GateAdapter()
    end_ts = int(time.time())
    start_ts = end_ts - (30 * 86400)

    strategies = [
        ("Mean Reversion", MeanReversion(lookback=50, band=2.0, confirm_bars=2)),
        ("Breakout", Breakout(lookback=60, confirm_bars=2)),
        ("Trend Follow", TrendFollow(fast=20, slow=100, confirm_bars=2)),
    ]

    results = []

    for name, strategy in strategies:
        print(f"\nTesting {name}...")
        backtester = Backtester(initial_capital=1000.0, min_notional=100.0)
        metrics = backtester.run(
            strategy=strategy,
            data_provider=data,
            symbol="BTC_USDT",
            timeframe="5m",
            start_ts=start_ts,
            end_ts=end_ts,
        )
        results.append((name, metrics))

    # Print comparison
    print("\n" + "=" * 80)
    print("COMPARISON RESULTS:")
    print("=" * 80)
    print(f"{'Strategy':<20} {'Return %':>10} {'Sharpe':>8} {'Max DD %':>10} {'Win %':>8} {'Trades':>8}")
    print("─" * 80)

    for name, m in results:
        print(f"{name:<20} {m.total_return:>10.2f} {m.sharpe_ratio:>8.2f} "
              f"{m.max_drawdown:>10.2f} {m.win_rate:>8.2f} {m.total_trades:>8}")

    print("=" * 80)

    # Find best strategy
    best = max(results, key=lambda x: x[1].sharpe_ratio)
    print(f"\n🏆 Best Strategy (by Sharpe): {best[0]} (Sharpe: {best[1].sharpe_ratio:.2f})")


def test_parameter_grid():
    """Example: Test different parameters for the same strategy."""
    print("\n\n" + "=" * 80)
    print("EXAMPLE 3: Parameter Optimization - Mean Reversion")
    print("=" * 80)

    data = GateAdapter()
    end_ts = int(time.time())
    start_ts = end_ts - (30 * 86400)

    # Test different lookback periods
    lookbacks = [20, 50, 100]
    bands = [2.0, 2.5]

    print(f"\nTesting {len(lookbacks) * len(bands)} parameter combinations...")

    results = []
    for lookback in lookbacks:
        for band in bands:
            strategy = MeanReversion(lookback=lookback, band=band, confirm_bars=2)
            backtester = Backtester(initial_capital=1000.0, min_notional=100.0)
            metrics = backtester.run(
                strategy=strategy,
                data_provider=data,
                symbol="BTC_USDT",
                timeframe="5m",
                start_ts=start_ts,
                end_ts=end_ts,
            )
            results.append(((lookback, band), metrics))
            print(f"  lookback={lookback:3}, band={band:.1f} -> "
                  f"Return={metrics.total_return:>7.2f}%, Sharpe={metrics.sharpe_ratio:>6.2f}")

    # Find best parameters
    best = max(results, key=lambda x: x[1].sharpe_ratio)
    params, metrics = best
    print("\n" + "─" * 80)
    print(f"🎯 Best Parameters: lookback={params[0]}, band={params[1]}")
    print(f"   Return: {metrics.total_return:.2f}%")
    print(f"   Sharpe: {metrics.sharpe_ratio:.2f}")
    print("─" * 80)


if __name__ == "__main__":
    # Run examples
    try:
        run_single_backtest()
        compare_strategies()
        test_parameter_grid()

        print("\n\n" + "=" * 80)
        print("✅ All examples completed successfully!")
        print("\nNext steps:")
        print("1. Start the Flask app: python run.py")
        print("2. Test via API:")
        print("   curl -X POST http://localhost:5000/backtest \\")
        print("     -H 'Content-Type: application/json' \\")
        print("     -d '{\"strategy\":\"MeanReversion\",\"params\":{\"lookback\":50,\"band\":2.0},\"symbol\":\"BTC_USDT\",\"timeframe\":\"5m\",\"days\":30}'")
        print("=" * 80)

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
