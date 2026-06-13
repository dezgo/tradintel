"""Regression tests for the money-accuracy fixes.

Each test pins one previously-broken behaviour so it can't silently regress:
  - timeout/cancelled orders must NOT be booked as fills   (app/bots.py)
  - filled orders update cash/position correctly           (app/bots.py)
  - the bot score uses the FIXED starting_allocation        (app/bots.py)
  - the Sharpe ratio annualizes by the actual timeframe     (app/backtest.py)
  - today's P&L matches round-trips by close_ts             (app/storage.py)

These are deterministic and offline — no exchange, no network.
"""
import math
import os
import tempfile
import time

import pytest

from app.core import Bar
from app.bots import TradingBot
from app.backtest import Backtester
from app.storage import Storage


# ── Test doubles ────────────────────────────────────────────────────────────
class _StubData:
    """DataProvider that replays a fixed list of bars."""
    def __init__(self, bars):
        self._bars = bars

    def history(self, symbol, tf, limit=200, start_ts=None, end_ts=None):
        return self._bars[-limit:]

    def last_price(self, symbol, tf="1m"):
        b = self._bars[-1]
        return (b.ts, b.close)


class _ConstStrategy:
    """Strategy that always requests the same exposure."""
    def __init__(self, exposure):
        self.exposure = exposure

    def on_bar(self, bars):
        return self.exposure


class _StubExec:
    """ExecutionClient whose limit_order returns a canned result."""
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def limit_order(self, symbol, side, qty, limit_price, timeout=60.0):
        self.calls += 1
        return dict(self.result)

    def paper_order(self, symbol, side, qty, price_hint=None):
        return {"status": "filled", "qty": 0.0}


def _bars(prices, start=1_000_000):
    return [
        Bar(ts=start + i * 60, open=p, high=p, low=p, close=p, volume=1.0)
        for i, p in enumerate(prices)
    ]


@pytest.fixture
def unpaused():
    """TradingBot.step() honours a global 'trading_paused' flag that defaults to
    True ('paused for safety'). Flip it off on the (isolated, temp-DB) singleton
    so the bot tests can actually execute trades."""
    from app.storage import store
    store.set_setting("trading_paused", False)
    yield
    store.set_setting("trading_paused", True)


# ── app/bots.py: order-fill accounting ──────────────────────────────────────
def test_timeout_order_is_not_booked(unpaused):
    """A timed-out order must leave cash/position untouched and record no trade.

    Previously filled_qty defaulted to the *intended* qty, so an unfilled order
    inflated the position as if it had completed.
    """
    bot = TradingBot(
        "t", "BTC_USDT", "1m",
        _ConstStrategy(1.0),
        _StubData(_bars([100.0] * 5)),
        _StubExec({"status": "timeout", "filled_qty": 0}),
        allocation=1000.0,
    )
    cash_before = bot.metrics.cash
    bot.step()
    assert bot.metrics.pos_qty == 0.0
    assert bot.metrics.cash == cash_before
    assert bot.metrics.trades == 0


def test_filled_order_updates_cash_and_position(unpaused):
    bot = TradingBot(
        "t", "BTC_USDT", "1m",
        _ConstStrategy(1.0),
        _StubData(_bars([100.0] * 5)),
        _StubExec({"status": "filled", "filled_qty": 10.0, "avg_price": 100.0, "fee": 0.0}),
        allocation=1000.0,
    )
    bot.step()
    assert bot.metrics.pos_qty == pytest.approx(10.0)
    assert bot.metrics.cash == pytest.approx(0.0)
    assert bot.metrics.trades == 1


# ── app/bots.py: score baseline ─────────────────────────────────────────────
def test_score_uses_fixed_starting_allocation(unpaused):
    """Score must be measured against starting_allocation, not the rebalanced one.

    Here the bot was rebalanced UP to 1500 (cash) but its fixed baseline is 1000.
    A flat-price full-long fill leaves equity at 1500, i.e. +50% vs the 1000
    baseline; with EMA alpha=0.1 the score is 0.05. The old code measured against
    the dynamic 1500 allocation and produced 0.0 — penalising the winner.
    """
    bot = TradingBot(
        "t", "BTC_USDT", "1m",
        _ConstStrategy(1.0),
        _StubData(_bars([100.0] * 5)),
        _StubExec({"status": "filled", "filled_qty": 15.0, "avg_price": 100.0, "fee": 0.0}),
        allocation=1500.0,
        starting_allocation=1000.0,
    )
    bot.step()
    assert bot.metrics.score == pytest.approx(0.05, abs=1e-6)


# ── app/backtest.py: Sharpe annualization ───────────────────────────────────
def test_sharpe_annualizes_by_timeframe():
    """Same data + strategy on 1m vs 1d must differ by sqrt(periods-per-year ratio).

    The old code hardcoded 1m periods regardless of timeframe, so a 1d backtest
    got a Sharpe inflated by ~sqrt(1440).
    """
    prices = [100 + (3 if i % 2 else 0) + i * 0.1 for i in range(60)]
    data = _StubData(_bars(prices))

    bt = Backtester(initial_capital=10_000.0, min_notional=1.0)
    m_1m = bt.run(_ConstStrategy(1.0), data, "BTC_USDT", "1m")
    m_1d = bt.run(_ConstStrategy(1.0), data, "BTC_USDT", "1d")

    assert m_1m.sharpe_ratio > 0
    assert m_1d.sharpe_ratio > 0
    expected_ratio = math.sqrt((365 * 24 * 60) / 365)  # sqrt(1440) ≈ 37.95
    assert (m_1m.sharpe_ratio / m_1d.sharpe_ratio) == pytest.approx(expected_ratio, rel=1e-3)


# ── app/storage.py: today's P&L by close_ts ─────────────────────────────────
def test_todays_pnl_counts_roundtrip_closed_today():
    """A round-trip that closes today must contribute to today's P&L.

    The bug read 'exit_ts' from round-trips that only carry 'close_ts', so the
    key never matched and today's P&L was permanently 0.
    """
    db_path = os.path.join(tempfile.mkdtemp(prefix="tradintel_pnl_"), "pnl.db")
    store = Storage(db_path)

    # trades.bot_name has a FK to bots(name); create the bot first.
    store.upsert_bot(
        name="b1", manager="m", symbol="BTC_USDT", tf="1m", strategy="Const",
        params={}, allocation=1000.0, cash=1000.0, pos_qty=0.0, avg_price=0.0,
        equity=1000.0, score=0.0, trades=0,
    )

    now = int(time.time())
    # Open + close a profitable long today: buy 1 @ 100, sell 1 @ 110 => +10.
    store.record_trade("b1", "BTC_USDT", "buy", 1.0, 100.0, ts=now - 120)
    store.record_trade("b1", "BTC_USDT", "sell", 1.0, 110.0, ts=now - 60)

    roundtrips = store.list_roundtrips()
    assert roundtrips, "expected at least one closed round-trip"
    assert "close_ts" in roundtrips[0]

    assert store.calculate_todays_pnl() == pytest.approx(10.0, abs=1e-6)
