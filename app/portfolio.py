# ───────────────────────────────────────────────────────────────────────────────
# app/portfolio.py (hydrate from DB on startup)
from __future__ import annotations

from typing import List
from app.core import DataProvider
from app.data import GateAdapter
from app.execution import PaperExec, BinanceTestnetExec
from app.bots import TradingBot
from app.managers import StrategyManager, PortfolioManager
from app.strategies import MeanReversion, Breakout, TrendFollow, MR_GRID, BO_GRID, TF_GRID
from app.storage import store

SYMBOLS = ["BTC_USDT", "ETH_USDT", "SOL_USDT"]
TF = "1m"

# Execution mode: 'paper' or 'binance_testnet'
EXECUTION_MODE = "binance_testnet"  # Change this to switch between paper and testnet

# Capital allocation mode:
# - 'fixed': Use CAPITAL_PER_BOT for each bot
# - 'exchange_balance': Fetch and distribute exchange USDT balance evenly
CAPITAL_MODE = "fixed"  # Change to 'exchange_balance' to use real testnet balances
CAPITAL_PER_BOT = 1000.0  # Used when CAPITAL_MODE = 'fixed'


def _get_capital_per_bot(total_bots: int) -> float:
    """Get capital allocation per bot based on CAPITAL_MODE."""
    if CAPITAL_MODE == "fixed":
        return CAPITAL_PER_BOT

    elif CAPITAL_MODE == "exchange_balance":
        # Fetch USDT balance from exchange
        try:
            if EXECUTION_MODE == "binance_testnet":
                client = BinanceTestnetExec("balance_fetcher")
                response = client.exchange.privateGetAccount()
                balances = response.get('balances', [])

                # Find USDT balance
                usdt_balance = 0.0
                for bal in balances:
                    if bal['asset'] == 'USDT':
                        usdt_balance = float(bal.get('free', 0))
                        break

                if usdt_balance > 0:
                    # Distribute evenly among all bots, leaving 10% as reserve
                    usable = usdt_balance * 0.9
                    per_bot = usable / total_bots
                    print(f"Exchange balance mode: Found {usdt_balance:.2f} USDT, allocating {per_bot:.2f} per bot")
                    return per_bot
                else:
                    print(f"Warning: No USDT balance found, falling back to {CAPITAL_PER_BOT}")
                    return CAPITAL_PER_BOT

            else:
                # Paper mode, use fixed
                return CAPITAL_PER_BOT

        except Exception as e:
            print(f"Error fetching exchange balance: {e}, falling back to {CAPITAL_PER_BOT}")
            return CAPITAL_PER_BOT

    else:
        raise ValueError(f"Unknown CAPITAL_MODE: {CAPITAL_MODE}")


def _get_execution_client(bot_name: str):
    """Get the appropriate execution client based on EXECUTION_MODE."""
    if EXECUTION_MODE == "binance_testnet":
        return BinanceTestnetExec(bot_name)
    elif EXECUTION_MODE == "paper":
        return PaperExec(bot_name)
    else:
        raise ValueError(f"Unknown execution mode: {EXECUTION_MODE}")


def _apply_saved_state(bots: list) -> None:
    saved = store.load_bots()
    for b in bots:
        row = saved.get(b.name)
        if not row:
            # brand-new bot: record its params and seed the bots table
            params = b.strategy.to_params() if hasattr(b.strategy, "to_params") else {}
            store.record_params(b.name, type(b.strategy).__name__, params)
            store.upsert_bot(
                name=b.name,
                manager=None,  # will be filled by StrategyManager on first step
                symbol=b.symbol,
                tf=b.tf,
                strategy=type(b.strategy).__name__,
                params=params,
                allocation=b.allocation,
                cash=b.metrics.cash,
                pos_qty=b.metrics.pos_qty,
                avg_price=b.metrics.avg_price,
                equity=b.metrics.equity,
                score=b.metrics.score,
                trades=b.metrics.trades,
            )
            continue

        # hydrate existing bot state
        b.allocation        = float(row["allocation"])
        b.metrics.cash      = float(row["cash"])
        b.metrics.pos_qty   = float(row["pos_qty"])
        b.metrics.avg_price = float(row["avg_price"])
        b.metrics.equity    = float(row["equity"]) or b.metrics.cash
        b.metrics.score     = float(row["score"])
        b.metrics.trades    = int(row["trades"])


def build_portfolio(data_provider: DataProvider | None = None) -> PortfolioManager:
    data = data_provider or GateAdapter()

    # Calculate total number of bots to determine capital allocation
    total_bots = len(SYMBOLS) * (len(MR_GRID) + len(BO_GRID) + len(TF_GRID))
    capital_per_bot = _get_capital_per_bot(total_bots)

    bots_mr: List[TradingBot] = []
    bots_bo: List[TradingBot] = []
    bots_tf: List[TradingBot] = []

    for sym in SYMBOLS:
        for idx, p in enumerate(MR_GRID, start=1):
            name = f"mr_{sym.lower()}_{TF}_p{idx}"
            bots_mr.append(TradingBot(name, sym, TF, MeanReversion(**p), data, _get_execution_client(name), capital_per_bot))
    for sym in SYMBOLS:
        for idx, p in enumerate(BO_GRID, start=1):
            name = f"bo_{sym.lower()}_{TF}_p{idx}"
            bots_bo.append(TradingBot(name, sym, TF, Breakout(**p), data, _get_execution_client(name), capital_per_bot))
    for sym in SYMBOLS:
        for idx, p in enumerate(TF_GRID, start=1):
            name = f"tf_{sym.lower()}_{TF}_p{idx}"
            bots_tf.append(TradingBot(name, sym, TF, TrendFollow(**p), data, _get_execution_client(name), capital_per_bot))

    # hydrate from DB (allocations, cash/positions, scores)
    _apply_saved_state([*bots_mr, *bots_bo, *bots_tf])

    m1 = StrategyManager(name="mean_reversion", bots=bots_mr, min_alloc_frac=0.05, max_alloc_frac=0.70)
    m2 = StrategyManager(name="breakout", bots=bots_bo, min_alloc_frac=0.05, max_alloc_frac=0.70)
    m3 = StrategyManager(name="trend_follow", bots=bots_tf, min_alloc_frac=0.05, max_alloc_frac=0.70)

    return PortfolioManager(managers=[m1, m2, m3], min_alloc_frac=0.10, max_alloc_frac=0.60)

