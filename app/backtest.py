# ───────────────────────────────────────────────────────────────────────────────
# app/backtest.py
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from app.core import Bar, Strategy, DataProvider


@dataclass
class Trade:
    """Represents a single trade execution during backtest."""
    ts: int
    side: str  # "buy" or "sell"
    qty: float
    price: float

    @property
    def notional(self) -> float:
        return self.qty * self.price


@dataclass
class BacktestMetrics:
    """Performance metrics from a backtest run."""
    total_return: float = 0.0  # % return
    sharpe_ratio: float = 0.0  # Annualized Sharpe
    max_drawdown: float = 0.0  # % peak-to-trough
    win_rate: float = 0.0  # % of winning trades
    profit_factor: float = 0.0  # gross_profit / gross_loss
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    avg_trade: float = 0.0
    max_consecutive_losses: int = 0
    final_equity: float = 0.0
    days: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            'total_return': round(self.total_return, 2),
            'sharpe_ratio': round(self.sharpe_ratio, 2),
            'max_drawdown': round(self.max_drawdown, 2),
            'win_rate': round(self.win_rate, 2),
            'profit_factor': round(self.profit_factor, 2),
            'total_trades': self.total_trades,
            'winning_trades': self.winning_trades,
            'losing_trades': self.losing_trades,
            'avg_win': round(self.avg_win, 2),
            'avg_loss': round(self.avg_loss, 2),
            'avg_trade': round(self.avg_trade, 2),
            'max_consecutive_losses': self.max_consecutive_losses,
            'final_equity': round(self.final_equity, 2),
            'days': self.days,
        }


class Backtester:
    """
    Backtests a strategy on historical data.

    Simulates the same logic as TradingBot but on historical bars,
    tracking equity curve and trade performance.
    """

    def __init__(
        self,
        initial_capital: float = 1000.0,
        min_notional: float = 100.0,
        commission_rate: float = 0.0,  # e.g., 0.001 = 0.1%
    ):
        self.initial_capital = initial_capital
        self.min_notional = min_notional
        self.commission_rate = commission_rate

        # State (reset on each run)
        self.cash: float = initial_capital
        self.position: float = 0.0
        self.avg_price: float = 0.0
        self.trades: List[Trade] = []
        self.equity_curve: List[tuple[int, float]] = []  # (timestamp, equity)
        self.bars_processed: List[Bar] = []

    def run(
        self,
        strategy: Strategy,
        data_provider: DataProvider,
        symbol: str,
        timeframe: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        lookback: int = 200,
    ) -> BacktestMetrics:
        """
        Run backtest on historical data.

        Args:
            strategy: Strategy instance to test
            data_provider: Data provider for fetching bars
            symbol: Trading symbol (e.g., "BTC_USDT")
            timeframe: Timeframe (e.g., "1m", "5m", "1h")
            start_ts: Start timestamp (epoch seconds), None = all available
            end_ts: End timestamp (epoch seconds), None = all available
            lookback: Number of bars to fetch initially for strategy warmup

        Returns:
            BacktestMetrics with performance statistics
        """
        # Reset state
        self.cash = self.initial_capital
        self.position = 0.0
        self.avg_price = 0.0
        self.trades = []
        self.equity_curve = []
        self.bars_processed = []

        # Fetch historical bars (Gate.io API limit is ~1000 bars per request)
        # This gives us:
        # - 1m bars: ~16 hours of data
        # - 5m bars: ~3.5 days of data
        # - 1h bars: ~41 days of data
        # - 1d bars: ~2.7 years of data
        all_bars = data_provider.history(symbol, timeframe, limit=1000)

        # Filter by date range if specified
        if start_ts is not None:
            all_bars = [b for b in all_bars if b.ts >= start_ts]
        if end_ts is not None:
            all_bars = [b for b in all_bars if b.ts <= end_ts]

        if not all_bars:
            return BacktestMetrics()

        # Process bars one by one
        for i, current_bar in enumerate(all_bars):
            # Give strategy the last N bars (including current)
            start_idx = max(0, i - lookback + 1)
            bars_window = all_bars[start_idx:i + 1]

            # Get strategy signal
            target_exposure = strategy.on_bar(bars_window)

            # Calculate target position
            price = current_bar.close
            equity = self.cash + self.position * price
            target_notional = equity * target_exposure
            target_qty = target_notional / max(1e-9, price)

            # Calculate trade delta
            delta = target_qty - self.position

            # Execute trade if delta is significant enough
            if abs(delta) * price >= self.min_notional:
                self._execute_trade(current_bar.ts, delta, price)

            # Record equity at this bar
            equity = self.cash + self.position * price
            self.equity_curve.append((current_bar.ts, equity))
            self.bars_processed.append(current_bar)

        # Calculate and return metrics
        return self._calculate_metrics()

    def _execute_trade(self, ts: int, delta: float, price: float) -> None:
        """Execute a trade, updating cash and position."""
        if abs(delta) < 1e-9:
            return

        side = "buy" if delta > 0 else "sell"
        qty = abs(delta)

        # Enforce no leverage on buys
        if side == "buy":
            max_qty = self.cash / price
            if qty > max_qty:
                qty = max_qty
                delta = qty  # Adjust delta

        # Calculate cost including commission
        trade_cost = qty * price
        commission = trade_cost * self.commission_rate

        # Update cash and position
        if side == "buy":
            self.cash -= (trade_cost + commission)
            self.position += qty
        else:  # sell
            self.cash += (trade_cost - commission)
            self.position -= qty

        # Record trade
        self.trades.append(Trade(ts=ts, side=side, qty=qty, price=price))
        self.avg_price = price

    def _calculate_metrics(self) -> BacktestMetrics:
        """Calculate performance metrics from equity curve and trades."""
        if not self.equity_curve:
            return BacktestMetrics()

        metrics = BacktestMetrics()

        # Basic metrics
        final_equity = self.equity_curve[-1][1]
        metrics.final_equity = final_equity
        metrics.total_return = ((final_equity - self.initial_capital) / self.initial_capital) * 100
        metrics.total_trades = len(self.trades)

        # Time period
        start_ts = self.equity_curve[0][0]
        end_ts = self.equity_curve[-1][0]
        metrics.days = (end_ts - start_ts) / 86400

        # Sharpe ratio (annualized)
        if len(self.equity_curve) > 1:
            returns = []
            for i in range(1, len(self.equity_curve)):
                prev_equity = self.equity_curve[i - 1][1]
                curr_equity = self.equity_curve[i][1]
                ret = (curr_equity - prev_equity) / max(1e-9, prev_equity)
                returns.append(ret)

            if returns:
                avg_return = sum(returns) / len(returns)
                variance = sum((r - avg_return) ** 2 for r in returns) / len(returns)
                std_dev = math.sqrt(variance)

                # Annualize (assuming 1m bars = 1440 bars/day, 365 days/year)
                if std_dev > 1e-9:
                    # Simplified: scale by sqrt of periods per year
                    periods_per_year = 365 * 24 * 60  # 1m bars per year
                    metrics.sharpe_ratio = (avg_return * math.sqrt(periods_per_year)) / std_dev

        # Max drawdown
        peak_equity = self.initial_capital
        max_dd = 0.0
        for _, equity in self.equity_curve:
            if equity > peak_equity:
                peak_equity = equity
            drawdown = ((peak_equity - equity) / peak_equity) * 100
            if drawdown > max_dd:
                max_dd = drawdown
        metrics.max_drawdown = max_dd

        # Trade analysis (pair buys with sells to find round trips)
        roundtrips = self._calculate_roundtrips()

        if roundtrips:
            winning = [rt for rt in roundtrips if rt > 0]
            losing = [rt for rt in roundtrips if rt < 0]

            metrics.winning_trades = len(winning)
            metrics.losing_trades = len(losing)
            metrics.win_rate = (len(winning) / len(roundtrips)) * 100 if roundtrips else 0

            metrics.avg_win = sum(winning) / len(winning) if winning else 0
            metrics.avg_loss = sum(losing) / len(losing) if losing else 0
            metrics.avg_trade = sum(roundtrips) / len(roundtrips)

            # Profit factor
            gross_profit = sum(winning) if winning else 0
            gross_loss = abs(sum(losing)) if losing else 0
            metrics.profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

            # Max consecutive losses
            max_consec = 0
            current_consec = 0
            for rt in roundtrips:
                if rt < 0:
                    current_consec += 1
                    max_consec = max(max_consec, current_consec)
                else:
                    current_consec = 0
            metrics.max_consecutive_losses = max_consec

        return metrics

    def _calculate_roundtrips(self) -> List[float]:
        """
        Calculate P&L for each round trip (entry + exit).

        A round trip is a buy followed by a sell (or vice versa).
        Returns list of P&L values for each round trip.
        """
        roundtrips = []
        entry_price = None
        entry_side = None
        position_size = 0.0

        for trade in self.trades:
            if entry_price is None:
                # First trade = entry
                entry_price = trade.price
                entry_side = trade.side
                position_size = trade.qty
            else:
                # Check if this trade closes/reduces position
                if (entry_side == "buy" and trade.side == "sell") or \
                   (entry_side == "sell" and trade.side == "buy"):
                    # Calculate P&L for this round trip
                    if entry_side == "buy":
                        pnl = (trade.price - entry_price) * min(position_size, trade.qty)
                    else:  # entry was sell (short)
                        pnl = (entry_price - trade.price) * min(position_size, trade.qty)

                    roundtrips.append(pnl)

                    # Update position
                    position_size -= trade.qty
                    if position_size <= 1e-9:
                        # Position fully closed
                        entry_price = None
                        entry_side = None
                        position_size = 0.0
                else:
                    # Adding to position
                    position_size += trade.qty

        return roundtrips

    def get_equity_curve(self) -> List[Dict[str, Any]]:
        """Return equity curve as list of dicts for JSON serialization."""
        return [
            {'ts': ts, 'equity': round(equity, 2)}
            for ts, equity in self.equity_curve
        ]

    def get_trades(self) -> List[Dict[str, Any]]:
        """Return trades as list of dicts for JSON serialization."""
        return [
            {
                'ts': t.ts,
                'side': t.side,
                'qty': round(t.qty, 6),
                'price': round(t.price, 2),
                'notional': round(t.notional, 2),
            }
            for t in self.trades
        ]
