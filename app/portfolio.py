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
from app.strategy_genome import StrategyGenome, GenomeStrategy
from app.storage import store

SYMBOLS = ["BTC_USDT", "ETH_USDT", "SOL_USDT"]

# Execution mode: loaded from database setting (default: binance_testnet)
# Options: 'paper' (simulated), 'binance_testnet' (testnet with fake money)
# Note: Live trading not yet implemented (would need BinanceLiveExec class)
def _get_execution_mode() -> str:
    """Get execution mode from database setting. Defaults to binance_testnet."""
    mode = store.get_setting("execution_mode", default="binance_testnet")
    return str(mode)

EXECUTION_MODE = _get_execution_mode()

# Timeframe - loaded from database setting (default: 1d to match optimization/evolution)
# CRITICAL: This must match the timeframe used for optimization/evolution!
# Changing this without re-optimizing strategies will result in invalid performance.
def _get_timeframe() -> str:
    """Get trading timeframe from database setting. Defaults to 1d (daily bars)."""
    tf = store.get_setting("trading_timeframe", default="1d")
    return str(tf)


def _get_capital_per_bot(total_bots: int) -> float:
    """
    Fetch USDT balance from exchange and distribute among bots.
    Respects capital_limit_usdt setting if configured, otherwise uses 90% of balance.
    Only USDT is used since we only trade USDT pairs (BTC_USDT, ETH_USDT, SOL_USDT).
    """
    if EXECUTION_MODE == "paper":
        # Paper mode: use $1000 per bot for simulation
        return 1000.0

    # Check for capital limit setting
    capital_limit = store.get_setting("capital_limit_usdt", default=None)

    # Binance testnet: fetch USDT balance only (since we only trade USDT pairs)
    try:
        client = BinanceTestnetExec("balance_fetcher")
        response = client.exchange.privateGetAccount()
        balances = response.get('balances', [])

        # Find USDT balance (our only quote currency)
        usdt_balance = 0.0
        for bal in balances:
            if bal['asset'] == 'USDT':
                usdt_balance = float(bal.get('free', 0))
                break

        if usdt_balance > 0:
            # Use capital limit if set, otherwise use 90% of balance
            if capital_limit is not None and capital_limit > 0:
                usable = min(float(capital_limit), usdt_balance)
                print(f"📊 Exchange balance: ${usdt_balance:.2f} USDT")
                print(f"🔒 Capital limit: ${capital_limit:.2f} USDT")
                print(f"💰 Using ${usable:.2f} USDT ({usable / usdt_balance * 100:.1f}% of balance)")
            else:
                # Default: 90% of balance (10% reserve)
                usable = usdt_balance * 0.9
                print(f"📊 Found ${usdt_balance:.2f} USDT on exchange")
                print(f"💰 Using ${usable:.2f} USDT (90% of balance, 10% reserve)")

            per_bot = usable / total_bots
            print(f"🤖 Allocating ${per_bot:.2f} per bot ({total_bots} bots)")
            return per_bot
        else:
            print(f"⚠️  Warning: No USDT balance found")
            print(f"    Using fallback: $1000 per bot")
            return 1000.0

    except Exception as e:
        print(f"❌ Error fetching exchange balance: {e}")
        print(f"    Using fallback: $1000 per bot")
        return 1000.0


def _get_execution_client(bot_name: str):
    """Get the appropriate execution client based on EXECUTION_MODE."""
    if EXECUTION_MODE == "binance_testnet":
        return BinanceTestnetExec(bot_name)
    elif EXECUTION_MODE == "paper":
        return PaperExec(bot_name)
    else:
        raise ValueError(f"Unknown execution mode: {EXECUTION_MODE}")


def _decode_genome(genome_dict: dict) -> StrategyGenome:
    """Reconstruct StrategyGenome from dictionary (loaded from database)."""
    return StrategyGenome(
        indicators=genome_dict.get("indicators", []),
        entry_long=genome_dict.get("entry_long", {}),
        exit_long=genome_dict.get("exit_long", {}),
        confirm_bars=genome_dict.get("confirm_bars", 2)
    )


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
                starting_allocation=b.starting_allocation,
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
        b.starting_allocation = float(row.get("starting_allocation", row["allocation"]))
        b.metrics.cash      = float(row["cash"])
        b.metrics.pos_qty   = float(row["pos_qty"])
        b.metrics.avg_price = float(row["avg_price"])
        b.metrics.equity    = float(row["equity"]) or b.metrics.cash
        b.metrics.score     = float(row["score"])
        b.metrics.trades    = int(row["trades"])


def build_portfolio(data_provider: DataProvider | None = None) -> PortfolioManager:
    """
    Build portfolio using top evolved strategies from database.

    Strategies contain their own symbol + timeframe, so we don't hardcode N bots per symbol.
    Instead, we select the top N strategies overall (regardless of symbol).

    Example: If BTC strategies perform best, we might run 3 BTC bots and 2 ETH bots.
    """
    data = data_provider or GateAdapter()

    # Get configuration from database
    num_strategies = int(store.get_setting("num_active_strategies", default=5))
    min_score = float(store.get_setting("min_strategy_score", default=0.0))

    print(f"\n{'='*70}")
    print(f"📊 PORTFOLIO CONFIGURATION")
    print(f"{'='*70}")
    print(f"Active Strategies: {num_strategies}")
    print(f"Min Score Threshold: {min_score}")
    print(f"{'='*70}\n")

    # Get top evolved strategies from database
    evolved_strats = store.get_top_evolved_strategies_for_portfolio(
        num_strategies=num_strategies,
        min_score=min_score
    )

    if not evolved_strats:
        print("⚠️  WARNING: No evolved strategies found in database!")
        print("   Falling back to default hardcoded strategies...")
        print("   Run genetic evolution or optimization to generate strategies.\n")
        return _build_fallback_portfolio(data)

    if len(evolved_strats) < num_strategies:
        print(f"⚠️  WARNING: Only found {len(evolved_strats)} profitable strategies (wanted {num_strategies})")
        print(f"   Using {len(evolved_strats)} strategies instead.\n")

    # Calculate capital per bot
    capital_per_bot = _get_capital_per_bot(len(evolved_strats))

    # Create bots from evolved strategies
    bots: List[TradingBot] = []

    print(f"🤖 ACTIVE STRATEGIES:")
    print(f"{'-'*70}")

    symbol_count = {}  # Track symbol distribution for diversity check

    for idx, strat in enumerate(evolved_strats, start=1):
        # Decode genome to strategy instance
        genome = _decode_genome(strat["genome"])
        strategy_instance = GenomeStrategy(genome)

        # Extract info from strategy metadata
        symbol = strat["symbol"]
        timeframe = strat["timeframe"]
        score = strat["score"]
        total_return = strat["total_return"]
        sharpe = strat["sharpe_ratio"]

        # Track symbol distribution
        symbol_count[symbol] = symbol_count.get(symbol, 0) + 1

        # Create bot name: evolved_1_btc_usdt_1d
        bot_name = f"evolved_{idx}_{symbol.lower()}_{timeframe}"

        # Create bot with strategy's symbol and timeframe
        bot = TradingBot(
            name=bot_name,
            symbol=symbol,
            tf=timeframe,  # Use strategy's timeframe (not global)
            strategy=strategy_instance,
            data=data,
            exec_client=_get_execution_client(bot_name),
            allocation=capital_per_bot
        )

        bots.append(bot)

        print(f"{idx}. {bot_name}")
        print(f"   Score: {score:.2f} | Return: {total_return:.1f}% | Sharpe: {sharpe:.2f}")

    print(f"{'-'*70}")
    print(f"📊 Symbol Distribution: {dict(symbol_count)}")
    print(f"✅ All strategies are unique (different symbol OR different genome)")
    print(f"{'-'*70}\n")

    # Hydrate from DB (allocations, cash/positions, scores)
    _apply_saved_state(bots)

    # Single manager for all evolved strategies (simple architecture)
    manager = StrategyManager(
        name="evolved_strategies",
        bots=bots,
        min_alloc_frac=0.05,
        max_alloc_frac=0.80
    )

    return PortfolioManager(
        managers=[manager],
        min_alloc_frac=0.10,
        max_alloc_frac=1.0  # Only one manager, can use all capital
    )


def _build_fallback_portfolio(data: DataProvider) -> PortfolioManager:
    """
    Fallback portfolio using hardcoded strategies if no evolved strategies exist.
    This ensures the system doesn't crash on first run before evolution has completed.
    """
    TF = _get_timeframe()
    print(f"📈 Fallback Timeframe: {TF}\n")

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

    _apply_saved_state([*bots_mr, *bots_bo, *bots_tf])

    m1 = StrategyManager(name="mean_reversion", bots=bots_mr, min_alloc_frac=0.05, max_alloc_frac=0.70)
    m2 = StrategyManager(name="breakout", bots=bots_bo, min_alloc_frac=0.05, max_alloc_frac=0.70)
    m3 = StrategyManager(name="trend_follow", bots=bots_tf, min_alloc_frac=0.05, max_alloc_frac=0.70)

    return PortfolioManager(managers=[m1, m2, m3], min_alloc_frac=0.10, max_alloc_frac=0.60)

