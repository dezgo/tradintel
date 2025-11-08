# ───────────────────────────────────────────────────────────────────────────────
# app/__init__.py
from __future__ import annotations

import os
import threading
import time
from flask import Flask, jsonify, render_template, redirect, url_for
from app.portfolio import build_portfolio
from app.auto_params import AutoParamSelector
from flask import request

_pm = None
_runner_thread: threading.Thread | None = None
_optimizer_thread: threading.Thread | None = None
_evolver_thread: threading.Thread | None = None
_selector = AutoParamSelector()  # default: refresh every 30m
_auto_rebalance_enabled = False  # Global flag for automatic strategy rebalancing


def _initialize_presets():
    """Initialize quick presets as saved strategies if they don't exist."""
    from app.storage import store

    presets = [
        {
            "name": "Mean Reversion • BTC • 5m",
            "strategy": "MeanReversion",
            "symbol": "BTC_USDT",
            "timeframe": "5m",
            "params": {"lookback": 50, "band": 2.0, "confirm_bars": 2},
            "initial_capital": 1000.0,
            "min_notional": 100.0,
        },
        {
            "name": "Breakout • ETH • 5m",
            "strategy": "Breakout",
            "symbol": "ETH_USDT",
            "timeframe": "5m",
            "params": {"lookback": 60, "confirm_bars": 2},
            "initial_capital": 1000.0,
            "min_notional": 100.0,
        },
        {
            "name": "Trend Follow • SOL • 5m",
            "strategy": "TrendFollow",
            "symbol": "SOL_USDT",
            "timeframe": "5m",
            "params": {"fast": 20, "slow": 100, "confirm_bars": 2},
            "initial_capital": 1000.0,
            "min_notional": 100.0,
        },
    ]

    # Get existing saved strategies
    existing = store.list_saved_backtests()
    existing_names = {s["name"] for s in existing}

    # Add presets that don't exist yet
    for preset in presets:
        if preset["name"] not in existing_names:
            try:
                store.save_backtest(**preset)
            except Exception as e:
                print(f"Failed to initialize preset '{preset['name']}': {e}")


def create_app() -> Flask:
    app = Flask(__name__)

    global _pm, _runner_thread, _optimizer_thread, _evolver_thread
    _pm = build_portfolio()

    # Initialize quick presets as saved strategies
    _initialize_presets()

    # Start strategy optimizer in background
    if not os.getenv("APP_DISABLE_OPTIMIZER"):
        def _optimize_loop():
            from app.optimizer import StrategyOptimizer
            optimizer = StrategyOptimizer()
            optimizer.run_continuous(interval_hours=24)

        _optimizer_thread = threading.Thread(target=_optimize_loop, daemon=True)
        _optimizer_thread.start()
        print("[App] Strategy optimizer started in background (24h cycle)")

    # Start genetic evolution in background
    if not os.getenv("APP_DISABLE_EVOLUTION"):
        def _evolution_loop():
            from app.genetic_evolution import GeneticEvolver
            evolver = GeneticEvolver(
                population_size=20,
                survivors=5,
                mutation_rate=0.7,
                crossover_rate=0.3
            )
            evolver.run_continuous(interval_hours=24)

        _evolver_thread = threading.Thread(target=_evolution_loop, daemon=True)
        _evolver_thread.start()
        print("[App] Genetic evolution started in background (24h cycle)")

    if not os.getenv("APP_DISABLE_LOOP"):
        def _loop():
            from app.portfolio import TF
            from app.strategies import MeanReversion, Breakout, TrendFollow, MR_GRID, BO_GRID, TF_GRID
            from app.storage import store
            import re

            SEC = 60 if TF == "1m" else 300 if TF == "5m" else 60  # fallback
            rebalance_counter = 0
            REBALANCE_INTERVAL = 60  # Auto-rebalance every 60 steps (bars)

            while True:
                try:
                    _pm.step()
                    # periodically refresh parameter sets with walk-forward
                    data = getattr(_pm.managers[0].bots[0], "data", None)
                    if data is not None:
                        _selector.maybe_refresh(_pm, data, TF)

                    # Auto-rebalance if enabled
                    global _auto_rebalance_enabled
                    rebalance_counter += 1
                    if _auto_rebalance_enabled and rebalance_counter >= REBALANCE_INTERVAL:
                        rebalance_counter = 0
                        try:
                            # Calculate strategy performance
                            strategy_scores = {}
                            for m in _pm.managers:
                                avg_score = sum(b.metrics.score for b in m.bots) / max(1, len(m.bots))
                                strategy_scores[m.name] = avg_score

                            # Find best performing strategy
                            best_strategy = max(strategy_scores, key=strategy_scores.get)

                            # Map manager names to strategy classes
                            manager_to_strategy = {
                                "mean_reversion": ("MeanReversion", MeanReversion, MR_GRID),
                                "breakout": ("Breakout", Breakout, BO_GRID),
                                "trend_follow": ("TrendFollow", TrendFollow, TF_GRID),
                            }

                            if best_strategy in manager_to_strategy:
                                strategy_name, strategy_class, grid = manager_to_strategy[best_strategy]

                                # Move bottom 20% of worst-performing workers to the best strategy
                                all_bots = [(b, m) for m in _pm.managers for b in m.bots]
                                all_bots.sort(key=lambda x: x[0].metrics.score)

                                num_to_reassign = max(1, len(all_bots) // 5)  # 20%

                                for bot, current_manager in all_bots[:num_to_reassign]:
                                    current_strategy_name = type(bot.strategy).__name__
                                    if current_strategy_name == strategy_name:
                                        continue

                                    match = re.search(r"_p(\d+)$", bot.name)
                                    param_idx = int(match.group(1)) - 1 if match else 0
                                    param_idx = min(param_idx, len(grid) - 1)

                                    new_strategy = strategy_class(**grid[param_idx])
                                    bot.strategy = new_strategy

                                    params = new_strategy.to_params() if hasattr(new_strategy, "to_params") else {}
                                    store.record_params(bot.name, strategy_name, params)
                                    store.upsert_bot(
                                        name=bot.name,
                                        manager=current_manager.name,
                                        symbol=bot.symbol,
                                        tf=bot.tf,
                                        strategy=strategy_name,
                                        params=params,
                                        allocation=bot.allocation,
                                        cash=bot.metrics.cash,
                                        pos_qty=bot.metrics.pos_qty,
                                        avg_price=bot.metrics.avg_price,
                                        equity=bot.metrics.equity,
                                        score=bot.metrics.score,
                                        trades=bot.metrics.trades,
                                    )

                                print(f"Auto-rebalance: moved {num_to_reassign} workers to {best_strategy}")
                        except Exception as exc:
                            print("Auto-rebalance error:", exc)

                    # sleep until a few seconds after the next bar boundary
                    now = time.time()
                    next_bar = (int(now // SEC) + 1) * SEC
                    sleep_s = max(2.0, next_bar - now + 2)  # +2s buffer for data to arrive
                except Exception as exc:  # noqa: BLE001
                    print("manager loop error:", exc)
                    sleep_s = 5
                time.sleep(sleep_s)

        _runner_thread = threading.Thread(target=_loop, daemon=True)
        _runner_thread.start()

    @app.get("/trades.json")
    def trades():
        from app.storage import store
        params = {
            "limit": int(request.args.get("limit", 50)),
            "since_id": int(request.args["since_id"]) if "since_id" in request.args else None,
            "bot_name": request.args.get("bot"),
            "symbol": request.args.get("symbol"),  # ← filter by symbol
            "manager": request.args.get("manager"),
        }
        items = store.list_trades(**params)
        return jsonify({"items": items})

    @app.get("/roundtrips.json")
    def roundtrips():
        from flask import request
        from app.storage import store
        items = store.list_roundtrips(
            limit=int(request.args.get("limit", 50)),
            bot_name=request.args.get("bot"),
            symbol=request.args.get("symbol"),
            manager=request.args.get("manager"),
        )
        return jsonify({"items": items})

    @app.get("/positions.json")
    def positions():
        from flask import request
        from app.storage import store
        items = store.list_open_positions(
            bot_name=request.args.get("bot"),
            symbol=request.args.get("symbol"),
            manager=request.args.get("manager"),
        )
        return jsonify({"items": items})

    @app.get("/portfolio.json")
    def portfolio():
        return jsonify(_pm.snapshot())

    @app.get("/fees.json")
    def fee_statistics():
        """Return fee statistics for the portfolio."""
        stats = store.fee_statistics()
        return jsonify(stats)

    @app.get("/prices.json")
    def prices():
        # get one shared data provider (from any bot)
        first_bot = next((b for m in _pm.managers for b in m.bots), None)
        data = getattr(first_bot, "data", None)

        # unique symbols in portfolio
        symbols = sorted({b.symbol for m in _pm.managers for b in m.bots})

        items = []
        for sym in symbols:
            try:
                # use 1m bars for freshness; ok to call history(limit=1)
                bar = data.history(sym, "1m", limit=1)[-1]
                items.append({"symbol": sym, "ts": int(bar.ts), "price": float(bar.close)})
            except Exception:
                items.append({"symbol": sym, "ts": None, "price": None})

        return jsonify({"items": items})

    @app.post("/api/worker/strategy")
    def change_worker_strategy():
        """Change a worker's strategy dynamically."""
        from flask import request
        data = request.get_json()
        worker_name = data.get("worker")
        new_strategy_name = data.get("strategy")

        if not worker_name or not new_strategy_name:
            return jsonify({"error": "Missing worker or strategy"}), 400

        # Find the bot
        bot = None
        current_manager = None
        for m in _pm.managers:
            for b in m.bots:
                if b.name == worker_name:
                    bot = b
                    current_manager = m
                    break
            if bot:
                break

        if not bot:
            return jsonify({"error": f"Worker {worker_name} not found"}), 404

        # Map strategy names to classes and grids
        from app.strategies import MeanReversion, Breakout, TrendFollow, MR_GRID, BO_GRID, TF_GRID

        strategy_map = {
            "MeanReversion": (MeanReversion, MR_GRID),
            "Breakout": (Breakout, BO_GRID),
            "TrendFollow": (TrendFollow, TF_GRID),
        }

        if new_strategy_name not in strategy_map:
            return jsonify({"error": f"Unknown strategy {new_strategy_name}"}), 400

        strategy_class, grid = strategy_map[new_strategy_name]

        # Determine which parameter set to use based on bot name suffix
        # Extract parameter index from bot name (e.g., mr_btc_usdt_1m_p1 -> p1)
        import re
        match = re.search(r"_p(\d+)$", worker_name)
        param_idx = int(match.group(1)) - 1 if match else 0
        param_idx = min(param_idx, len(grid) - 1)  # clamp to grid size

        # Create new strategy instance
        new_strategy = strategy_class(**grid[param_idx])

        # Replace the bot's strategy
        bot.strategy = new_strategy

        # Update the database
        from app.storage import store
        params = new_strategy.to_params() if hasattr(new_strategy, "to_params") else {}
        store.record_params(bot.name, new_strategy_name, params)
        store.upsert_bot(
            name=bot.name,
            manager=current_manager.name,
            symbol=bot.symbol,
            tf=bot.tf,
            strategy=new_strategy_name,
            params=params,
            allocation=bot.allocation,
            cash=bot.metrics.cash,
            pos_qty=bot.metrics.pos_qty,
            avg_price=bot.metrics.avg_price,
            equity=bot.metrics.equity,
            score=bot.metrics.score,
            trades=bot.metrics.trades,
        )

        return jsonify({"success": True, "worker": worker_name, "new_strategy": new_strategy_name})

    @app.get("/api/auto-rebalance")
    def get_auto_rebalance():
        """Get the current auto-rebalance setting."""
        global _auto_rebalance_enabled
        return jsonify({"enabled": _auto_rebalance_enabled})

    @app.post("/api/auto-rebalance")
    def set_auto_rebalance():
        """Enable or disable automatic strategy rebalancing."""
        global _auto_rebalance_enabled
        data = request.get_json()
        enabled = data.get("enabled", False)
        _auto_rebalance_enabled = bool(enabled)
        return jsonify({"enabled": _auto_rebalance_enabled, "message": f"Auto-rebalance {'enabled' if _auto_rebalance_enabled else 'disabled'}"})

    @app.post("/api/auto-assign-strategies")
    def auto_assign_strategies():
        """Automatically assign workers to strategies based on performance."""
        from app.strategies import MeanReversion, Breakout, TrendFollow, MR_GRID, BO_GRID, TF_GRID
        from app.storage import store

        # Calculate strategy performance
        strategy_scores = {}
        for m in _pm.managers:
            avg_score = sum(b.metrics.score for b in m.bots) / max(1, len(m.bots))
            strategy_scores[m.name] = avg_score

        # Find best performing strategy
        best_strategy = max(strategy_scores, key=strategy_scores.get)

        # Map manager names to strategy classes
        manager_to_strategy = {
            "mean_reversion": ("MeanReversion", MeanReversion, MR_GRID),
            "breakout": ("Breakout", Breakout, BO_GRID),
            "trend_follow": ("TrendFollow", TrendFollow, TF_GRID),
        }

        if best_strategy not in manager_to_strategy:
            return jsonify({"error": "Unknown best strategy"}), 500

        strategy_name, strategy_class, grid = manager_to_strategy[best_strategy]

        # Move bottom 20% of worst-performing workers to the best strategy
        all_bots = [(b, m) for m in _pm.managers for b in m.bots]
        all_bots.sort(key=lambda x: x[0].metrics.score)

        num_to_reassign = max(1, len(all_bots) // 5)  # 20%
        workers_reassigned = []

        for bot, current_manager in all_bots[:num_to_reassign]:
            # Skip if already using the best strategy
            current_strategy_name = type(bot.strategy).__name__
            if current_strategy_name == strategy_name:
                continue

            # Determine parameter index
            import re
            match = re.search(r"_p(\d+)$", bot.name)
            param_idx = int(match.group(1)) - 1 if match else 0
            param_idx = min(param_idx, len(grid) - 1)

            # Create and assign new strategy
            new_strategy = strategy_class(**grid[param_idx])
            bot.strategy = new_strategy

            # Update database
            params = new_strategy.to_params() if hasattr(new_strategy, "to_params") else {}
            store.record_params(bot.name, strategy_name, params)
            store.upsert_bot(
                name=bot.name,
                manager=current_manager.name,
                symbol=bot.symbol,
                tf=bot.tf,
                strategy=strategy_name,
                params=params,
                allocation=bot.allocation,
                cash=bot.metrics.cash,
                pos_qty=bot.metrics.pos_qty,
                avg_price=bot.metrics.avg_price,
                equity=bot.metrics.equity,
                score=bot.metrics.score,
                trades=bot.metrics.trades,
            )

            workers_reassigned.append(bot.name)

        return jsonify({
            "success": True,
            "best_strategy": best_strategy,
            "workers_reassigned": workers_reassigned,
            "count": len(workers_reassigned)
        })

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/")
    def home():
        return redirect(url_for("ui"))

    @app.get("/ui")
    def ui():
        return render_template("portfolio.html")

    @app.get("/backtest-ui")
    def backtest_ui():
        return render_template("backtest.html")

    @app.get("/data-ui")
    def data_ui():
        return render_template("data.html")

    @app.get("/optimizer-ui")
    def optimizer_ui():
        return render_template("optimizer.html")

    @app.get("/evolution-ui")
    def evolution_ui():
        return render_template("evolution.html")

    @app.get("/backtest/strategies")
    def backtest_strategies():
        """List available strategies and their parameter grids."""
        from app.strategies import MR_GRID, BO_GRID, TF_GRID
        return jsonify({
            "strategies": [
                {
                    "name": "MeanReversion",
                    "params": MR_GRID,
                    "description": "Mean reversion strategy using moving average bands"
                },
                {
                    "name": "Breakout",
                    "params": BO_GRID,
                    "description": "Breakout strategy based on new highs/lows"
                },
                {
                    "name": "TrendFollow",
                    "params": TF_GRID,
                    "description": "Trend following strategy using dual moving averages"
                }
            ],
            "symbols": ["BTC_USDT", "ETH_USDT", "SOL_USDT"],
            "timeframes": ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
        })

    @app.get("/backtest/saved")
    def list_saved_backtests():
        """List all saved backtest configurations."""
        from app.storage import store
        saved = store.list_saved_backtests()
        return jsonify({"saved": saved})

    @app.post("/backtest/saved")
    def save_backtest_config():
        """Save a backtest configuration."""
        from app.storage import store

        body = request.get_json()
        if not body or "name" not in body:
            return jsonify({"error": "Request body must include 'name'"}), 400

        try:
            backtest_id = store.save_backtest(
                name=body["name"],
                strategy=body["strategy"],
                symbol=body["symbol"],
                timeframe=body["timeframe"],
                params=body["params"],
                initial_capital=body.get("initial_capital", 1000),
                min_notional=body.get("min_notional", 100),
                days=body.get("days", 365),
            )
            return jsonify({"id": backtest_id, "name": body["name"]})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.delete("/backtest/saved/<int:backtest_id>")
    def delete_saved_backtest(backtest_id: int):
        """Delete a saved backtest configuration."""
        from app.storage import store

        deleted = store.delete_saved_backtest(backtest_id)
        if deleted:
            return jsonify({"deleted": True})
        else:
            return jsonify({"error": "Backtest not found"}), 404

    @app.post("/backtest")
    def run_backtest():
        """
        Run a backtest on historical data.

        Request body:
        {
            "strategy": "MeanReversion" | "Breakout" | "TrendFollow",
            "params": {"lookback": 20, "band": 2.0, ...},
            "symbol": "BTC_USDT",
            "timeframe": "1m",
            "days": 30,  // optional, defaults to 30
            "initial_capital": 1000,  // optional
            "min_notional": 100  // optional
        }

        Returns:
        {
            "metrics": {...},
            "equity_curve": [...],
            "trades": [...]
        }
        """
        from app.backtest import Backtester
        from app.strategies import MeanReversion, Breakout, TrendFollow
        from app.strategy_genome import StrategyGenome, GenomeStrategy
        from app.data import GateAdapter
        import time

        body = request.get_json()
        if not body:
            return jsonify({"error": "Request body required"}), 400

        # Parse request
        strategy_name = body.get("strategy")
        params = body.get("params", {})
        symbol = body.get("symbol", "BTC_USDT")
        timeframe = body.get("timeframe", "1m")
        days = body.get("days", 30)
        initial_capital = body.get("initial_capital", 1000.0)
        min_notional = body.get("min_notional", 100.0)

        # Create strategy instance
        try:
            if strategy_name == "GenomeStrategy":
                # Special handling for evolved strategies
                if "genome" not in params:
                    return jsonify({"error": "GenomeStrategy requires genome in params"}), 400

                genome = StrategyGenome.from_dict(params["genome"])
                strategy = GenomeStrategy(genome)
            else:
                # Validate standard strategy
                strategy_map = {
                    "MeanReversion": MeanReversion,
                    "Breakout": Breakout,
                    "TrendFollow": TrendFollow,
                }

                if strategy_name not in strategy_map:
                    return jsonify({"error": f"Unknown strategy: {strategy_name}"}), 400

                strategy = strategy_map[strategy_name](**params)
        except Exception as e:
            return jsonify({"error": f"Invalid parameters: {str(e)}"}), 400

        # Calculate start timestamp
        end_ts = int(time.time())
        start_ts = end_ts - (days * 86400)

        # Run backtest
        try:
            # Use cached data provider for better performance
            from app.data_cache import CachedDataProvider
            gate = GateAdapter()
            data_provider = CachedDataProvider(gate, source_name="gate")

            backtester = Backtester(
                initial_capital=initial_capital,
                min_notional=min_notional,
            )

            metrics = backtester.run(
                strategy=strategy,
                data_provider=data_provider,
                symbol=symbol,
                timeframe=timeframe,
                start_ts=start_ts,
                end_ts=end_ts,
            )

            return jsonify({
                "metrics": metrics.to_dict(),
                "equity_curve": backtester.get_equity_curve(),
                "trades": backtester.get_trades(),
                "config": {
                    "strategy": strategy_name,
                    "params": params,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "days": days,
                    "initial_capital": initial_capital,
                }
            })

        except Exception as e:
            return jsonify({"error": f"Backtest failed: {str(e)}"}), 500

    @app.get("/data/coverage")
    def data_coverage():
        """
        Get cache coverage for all symbols/timeframes.

        Returns:
        {
            "items": [
                {"symbol": "BTC_USDT", "timeframe": "1d", "start_ts": ..., "end_ts": ..., "count": 365},
                ...
            ]
        }
        """
        from app.storage import store

        # Get all unique symbol/timeframe combinations from cache
        # Note: Multiple sources possible, so we pick the one with most bars
        with store._lock:
            cur = store._conn.execute(
                """
                SELECT symbol, timeframe, source, MIN(ts), MAX(ts), COUNT(*)
                FROM bars
                GROUP BY symbol, timeframe, source
                """
            )
            rows = cur.fetchall()

        # Group by symbol/timeframe and pick source with most bars
        by_key = {}
        for r in rows:
            key = (r[0], r[1])
            if key not in by_key or r[5] > by_key[key][5]:
                by_key[key] = r

        items = [
            {
                "symbol": r[0],
                "timeframe": r[1],
                "source": r[2],
                "start_ts": int(r[3]),
                "end_ts": int(r[4]),
                "count": int(r[5]),
            }
            for r in by_key.values()
        ]

        return jsonify({"items": items})

    @app.post("/data/backfill")
    def backfill_data():
        """
        Backfill historical data from Gate.io or CoinGecko.

        Request body:
        {
            "symbols": ["BTC_USDT", "ETH_USDT", "SOL_USDT"],
            "provider": "gate" | "coingecko",  // defaults to "gate"
            "timeframe": "1d",  // only used for Gate.io
            "bars": 1000  // number of bars to fetch
        }

        Returns:
        {
            "results": {
                "BTC_USDT": "✓ Cached 1000 bars",
                "ETH_USDT": "✓ Cached 1000 bars",
                ...
            }
        }
        """
        from app.data_cache import backfill_daily_data, backfill_gate_data

        body = request.get_json()
        if not body or "symbols" not in body:
            return jsonify({"error": "Request body must include 'symbols' array"}), 400

        symbols = body.get("symbols", [])
        provider = body.get("provider", "gate")
        timeframe = body.get("timeframe", "1d")
        bars = body.get("bars", 1000)

        if not isinstance(symbols, list) or not symbols:
            return jsonify({"error": "'symbols' must be a non-empty array"}), 400

        if provider == "coingecko":
            # CoinGecko: daily data only, max 90 days
            results = backfill_daily_data(symbols, bars)
        elif provider == "gate":
            # Gate.io: any timeframe, max 1000 bars
            results = backfill_gate_data(symbols, timeframe, bars)
        else:
            return jsonify({"error": f"Unknown provider '{provider}'. Use 'gate' or 'coingecko'"}), 400

        return jsonify({"results": results})

    @app.get("/optimizer/results")
    def list_optimizer_results():
        """List optimization results, optionally filtered by strategy/symbol."""
        from app.storage import store

        strategy = request.args.get("strategy")
        symbol = request.args.get("symbol")
        limit = int(request.args.get("limit", 100))

        results = store.list_optimization_results(strategy=strategy, symbol=symbol, limit=limit)
        return jsonify({"results": results})

    @app.post("/optimizer/promote/<int:result_id>")
    def promote_optimizer_result(result_id: int):
        """
        Promote an optimization result to a saved strategy.
        Creates a new saved backtest with a generated name.
        """
        from app.storage import store

        # Get the optimization result
        all_results = store.list_optimization_results(limit=1000)
        result = next((r for r in all_results if r["id"] == result_id), None)

        if not result:
            return jsonify({"error": "Optimization result not found"}), 404

        # Generate name from result
        name = f"{result['strategy']} • {result['symbol'].replace('_USDT', '')} • {result['timeframe']} [Opt {result['score']:.0f}]"

        try:
            # Save as backtest configuration with the days used in optimization
            backtest_id = store.save_backtest(
                name=name,
                strategy=result["strategy"],
                symbol=result["symbol"],
                timeframe=result["timeframe"],
                params=result["params"],
                initial_capital=1000.0,
                min_notional=100.0,
                days=result["days"],
            )

            return jsonify({"id": backtest_id, "name": name})

        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.get("/evolution/results")
    def list_evolution_results():
        """List evolved strategies, optionally filtered by symbol and minimum score."""
        from app.storage import store

        symbol = request.args.get("symbol")
        min_score = request.args.get("min_score", type=float)
        limit = int(request.args.get("limit", 100))

        results = store.list_evolved_strategies(symbol=symbol, min_score=min_score, limit=limit)
        return jsonify({"results": results})

    @app.post("/evolution/promote/<int:strategy_id>")
    def promote_evolved_strategy(strategy_id: int):
        """
        Promote an evolved strategy to a saved backtest configuration.
        The genome will be stored as parameters so it can be used for backtesting.
        """
        from app.storage import store

        # Get the evolved strategy
        result = store.get_evolved_strategy(strategy_id)

        if not result:
            return jsonify({"error": "Evolved strategy not found"}), 404

        # Generate name from result
        name = f"Evolved Gen{result['generation']} • {result['symbol'].replace('_USDT', '')} • {result['timeframe']} [Score {result['score']:.0f}]"

        try:
            # Save as backtest configuration
            # Store the genome as params - it will be interpreted by GenomeStrategy
            backtest_id = store.save_backtest(
                name=name,
                strategy="GenomeStrategy",  # Special strategy type for evolved genomes
                symbol=result["symbol"],
                timeframe=result["timeframe"],
                params={"genome": result["genome"]},  # Store genome as params
                initial_capital=1000.0,
                min_notional=100.0,
                days=result["days"],
            )

            return jsonify({"id": backtest_id, "name": name})

        except Exception as e:
            return jsonify({"error": str(e)}), 500

    return app
