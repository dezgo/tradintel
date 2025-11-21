# ───────────────────────────────────────────────────────────────────────────────
# app/__init__.py
from __future__ import annotations

import os
import threading
import time
from flask import Flask, jsonify, render_template, redirect, url_for, request
from flask_login import LoginManager, login_required, current_user
from app.portfolio import build_portfolio
from app.auto_params import AutoParamSelector
from app.auth import User

_pm = None
_runner_thread: threading.Thread | None = None
_optimizer_thread: threading.Thread | None = None
_evolver_thread: threading.Thread | None = None
_selector = AutoParamSelector()  # default: refresh every 30m


def _get_trading_paused() -> bool:
    """Get trading paused state from database (works across multiple workers)."""
    from app.storage import store
    return store.get_setting("trading_paused", default=True)  # Default to paused for safety


def _set_trading_paused(paused: bool) -> None:
    """Set trading paused state in database (works across multiple workers)."""
    from app.storage import store
    store.set_setting("trading_paused", paused)


def _get_auto_rebalance_enabled() -> bool:
    """Get auto-rebalance enabled state from database (works across multiple workers)."""
    from app.storage import store
    return store.get_setting("auto_rebalance_enabled", default=False)


def _set_auto_rebalance_enabled(enabled: bool) -> None:
    """Set auto-rebalance enabled state in database (works across multiple workers)."""
    from app.storage import store
    store.set_setting("auto_rebalance_enabled", enabled)


def _ensure_manual_trade_bot():
    """Ensure 'manual_trade' bot exists in database for manual trading."""
    from app.storage import store

    # Check if manual_trade bot already exists
    existing_bots = store.load_bots()
    if "manual_trade" not in existing_bots:
        # Create a dummy bot entry for manual trades
        store.upsert_bot(
            name="manual_trade",
            manager="manual",
            symbol="MULTI",  # Can trade multiple symbols
            tf="manual",
            strategy="Manual",
            params={},
            allocation=0.0,  # Manual trades don't have bot allocation
            starting_allocation=0.0,
            cash=0.0,
            pos_qty=0.0,
            avg_price=0.0,
            equity=0.0,
            score=0.0,
            trades=0,
        )
        print("[App] Created 'manual_trade' bot entry for manual trading")


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

    # Configure Flask-Login
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', os.urandom(24).hex())

    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'login'
    login_manager.login_message = 'Please log in to access this page.'

    @login_manager.user_loader
    def load_user(user_id):
        """Load user for Flask-Login session management."""
        return User.get_configured_user()

    global _pm, _runner_thread, _optimizer_thread, _evolver_thread
    _pm = build_portfolio()

    # Initialize quick presets as saved strategies
    _initialize_presets()
    _ensure_manual_trade_bot()  # Ensure manual_trade bot exists for manual trading

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

    # Start price alert monitoring in background (independent of trading timeframe)
    if not os.getenv("APP_DISABLE_ALERTS"):
        def _alert_loop():
            import time
            from app.alert_monitor import PriceAlertMonitor
            from app.data import GateAdapter

            gate = GateAdapter()
            monitor = PriceAlertMonitor(gate)

            while True:
                try:
                    results = monitor.run_check_if_ready()
                    if results and results["triggered"] > 0:
                        print(f"Price alerts: {results['triggered']} triggered, {results['checked']} checked")
                except Exception as exc:
                    print("Price alert check error:", exc)

                # Sleep for 10 seconds before next check (monitor.should_check() handles 60s interval)
                time.sleep(10)

        _alert_thread = threading.Thread(target=_alert_loop, daemon=True)
        _alert_thread.start()
        print("[App] Price alert monitoring started (checks every 60 seconds)")

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
                    rebalance_counter += 1
                    if _get_auto_rebalance_enabled() and rebalance_counter >= REBALANCE_INTERVAL:
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
                                        starting_allocation=bot.starting_allocation,
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

    # ── Authentication routes ────────────────────────────────────────────────────
    from flask_login import login_user, logout_user

    @app.route("/login", methods=["GET", "POST"])
    def login():
        """Login page and handler."""
        if current_user.is_authenticated:
            return redirect(url_for('ui'))

        if request.method == "POST":
            username = request.form.get("username")
            password = request.form.get("password")
            remember = request.form.get("remember") == "on"

            user = User.verify_credentials(username, password)

            if user:
                login_user(user, remember=remember)
                next_page = request.args.get('next')
                # Prevent open redirect vulnerability
                if next_page and next_page.startswith('/'):
                    return redirect(next_page)
                return redirect(url_for('ui'))
            else:
                return render_template("login.html", error="Invalid username or password")

        return render_template("login.html")

    @app.route("/logout")
    @login_required
    def logout():
        """Logout handler."""
        logout_user()
        return redirect(url_for('login'))

    # ── API routes ───────────────────────────────────────────────────────────────

    @app.get("/trades.json")
    @login_required
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
    @login_required
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
    @login_required
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
    @login_required
    def portfolio():
        from app.portfolio import EXECUTION_MODE
        snapshot = _pm.snapshot()
        snapshot['execution_mode'] = EXECUTION_MODE
        return jsonify(snapshot)

    @app.get("/fees.json")
    @login_required
    def fee_statistics():
        """Return fee statistics for the portfolio."""
        from app.storage import store
        stats = store.fee_statistics()
        return jsonify(stats)

    @app.get("/decisions.json")
    @login_required
    def trading_decisions():
        """Return recent trading decisions for monitoring."""
        from app.bots import get_decision_log
        decisions = get_decision_log()
        # Return most recent first
        return jsonify({"items": list(reversed(decisions))})

    @app.get("/api/recent-trades")
    @login_required
    def recent_trades():
        """Return recent executed trades from database (persists across restarts)."""
        from app.storage import store
        trades = store.list_trades(limit=50)  # Get last 50 trades

        # Format trades for the UI
        items = []
        for trade in trades:
            items.append({
                "timestamp": trade["ts"],
                "symbol": trade["symbol"],
                "side": trade["side"],
                "qty": trade["qty"],
                "price": trade["price"],
                "fee": trade["fee"],
                "is_maker": trade["is_maker"]
            })

        return jsonify({"items": items})

    @app.get("/exchange-balance.json")
    @login_required
    def exchange_balance():
        """Return actual exchange account balance (testnet or live)."""
        from app.portfolio import EXECUTION_MODE
        from app.execution import BinanceTestnetExec

        if EXECUTION_MODE == "paper":
            return jsonify({
                "mode": "paper",
                "balances": [],
                "message": "Paper trading mode - no exchange connection"
            })

        try:
            # Get a test client to fetch balances
            client = BinanceTestnetExec("balance_check")
            response = client.exchange.privateGetAccount()
            balances = response.get('balances', [])

            # Filter to only show non-zero balances
            non_zero = []
            for bal in balances:
                free = float(bal.get('free', 0))
                locked = float(bal.get('locked', 0))
                if free > 0 or locked > 0:
                    non_zero.append({
                        'asset': bal['asset'],
                        'free': free,
                        'locked': locked,
                        'total': free + locked
                    })

            return jsonify({
                "mode": EXECUTION_MODE,
                "balances": non_zero,
                "message": f"Connected to {EXECUTION_MODE}"
            })

        except Exception as e:
            return jsonify({
                "mode": EXECUTION_MODE,
                "balances": [],
                "error": str(e),
                "message": f"Failed to fetch {EXECUTION_MODE} balance"
            })

    @app.get("/prices.json")
    @login_required
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
    @login_required
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

        # Map strategy names to classes and grids (old hardcoded strategies)
        from app.strategies import MeanReversion, Breakout, TrendFollow, MR_GRID, BO_GRID, TF_GRID
        from app.strategy_genome import GenomeStrategy

        strategy_map = {
            "MeanReversion": (MeanReversion, MR_GRID),
            "Breakout": (Breakout, BO_GRID),
            "TrendFollow": (TrendFollow, TF_GRID),
        }

        new_strategy = None
        strategy_type_name = new_strategy_name

        # Check if it's a saved or evolved strategy (format: "saved:123" or "evolved:456")
        if ":" in new_strategy_name:
            strategy_prefix, strategy_id_str = new_strategy_name.split(":", 1)
            strategy_id = int(strategy_id_str)

            if strategy_prefix == "saved":
                # Load saved strategy from database
                from app.storage import store
                saved_strat = store.get_saved_strategy(strategy_id)
                if not saved_strat:
                    return jsonify({"error": f"Saved strategy {strategy_id} not found"}), 404

                # Reconstruct strategy based on saved config
                strat_name = saved_strat["strategy"]
                params = saved_strat["params"]

                if strat_name == "GenomeStrategy":
                    # It's a genome strategy - reconstruct from params
                    from app.portfolio import _decode_genome
                    genome = _decode_genome(params)
                    new_strategy = GenomeStrategy(genome)
                elif strat_name in strategy_map:
                    # It's a legacy strategy with custom params
                    strategy_class = strategy_map[strat_name][0]
                    new_strategy = strategy_class(**params)
                else:
                    return jsonify({"error": f"Unknown saved strategy type: {strat_name}"}), 400

                strategy_type_name = f"SavedStrategy({strategy_id})"

            elif strategy_prefix == "evolved":
                # Load evolved strategy from database
                from app.storage import store
                evolved_strat = store.get_evolved_strategy(strategy_id)
                if not evolved_strat:
                    return jsonify({"error": f"Evolved strategy {strategy_id} not found"}), 404

                # Reconstruct genome
                from app.portfolio import _decode_genome
                genome = _decode_genome(evolved_strat["genome"])
                new_strategy = GenomeStrategy(genome)
                strategy_type_name = f"EvolvedStrategy({strategy_id})"
            else:
                return jsonify({"error": f"Unknown strategy prefix: {strategy_prefix}"}), 400

        elif new_strategy_name in strategy_map:
            # Old hardcoded strategy
            strategy_class, grid = strategy_map[new_strategy_name]

            # Determine which parameter set to use based on bot name suffix
            # Extract parameter index from bot name (e.g., mr_btc_usdt_1m_p1 -> p1)
            import re
            match = re.search(r"_p(\d+)$", worker_name)
            param_idx = int(match.group(1)) - 1 if match else 0
            param_idx = min(param_idx, len(grid) - 1)  # clamp to grid size

            # Create new strategy instance
            new_strategy = strategy_class(**grid[param_idx])
            strategy_type_name = new_strategy_name
        else:
            return jsonify({"error": f"Unknown strategy {new_strategy_name}"}), 400

        # Replace the bot's strategy
        bot.strategy = new_strategy

        # Update the database
        from app.storage import store
        params = new_strategy.to_params() if hasattr(new_strategy, "to_params") else {}
        store.record_params(bot.name, strategy_type_name, params)
        store.upsert_bot(
            name=bot.name,
            manager=current_manager.name,
            symbol=bot.symbol,
            tf=bot.tf,
            strategy=strategy_type_name,
            params=params,
            allocation=bot.allocation,
            cash=bot.metrics.cash,
            pos_qty=bot.metrics.pos_qty,
            avg_price=bot.metrics.avg_price,
            equity=bot.metrics.equity,
            score=bot.metrics.score,
            trades=bot.metrics.trades,
        )

        return jsonify({"success": True, "worker": worker_name, "new_strategy": strategy_type_name})

    @app.get("/api/available-strategies")
    @login_required
    def get_available_strategies():
        """Get all available strategies for worker dropdown (evolved + hardcoded)."""
        from app.storage import store

        strategies = []

        # Add old hardcoded strategies (for backwards compatibility)
        strategies.append({"id": "MeanReversion", "name": "Mean Reversion (Legacy)", "type": "hardcoded"})
        strategies.append({"id": "Breakout", "name": "Breakout (Legacy)", "type": "hardcoded"})
        strategies.append({"id": "TrendFollow", "name": "Trend Follow (Legacy)", "type": "hardcoded"})

        # Add saved strategies (from strategy builder / backtest clones)
        try:
            saved_strategies = store.list_saved_strategies()
            for s in saved_strategies:
                strategies.append({
                    "id": f"saved:{s['id']}",
                    "name": f"📋 {s['name']}",
                    "type": "saved"
                })
        except Exception as ex:
            print(f"Warning: Could not load saved strategies: {ex}")

        # Add evolved strategies (top 20, only profitable ones)
        try:
            evolved_strategies = store.list_evolved_strategies(symbol=None, min_score=0.0, limit=20)
            for e in evolved_strategies:
                # Create a short preview of the genome
                genome = e["genome"]
                indicators = genome.get("indicators", [])
                indicator_preview = indicators[0] if indicators else "custom"

                strategies.append({
                    "id": f"evolved:{e['id']}",
                    "name": f"🧬 G{e['generation']} {e['symbol']} {indicator_preview.upper()} (score: {e['score']:.1f})",
                    "type": "evolved"
                })
        except Exception as ex:
            print(f"Warning: Could not load evolved strategies: {ex}")

        return jsonify({"strategies": strategies})

    @app.get("/api/auto-rebalance")
    @login_required
    def get_auto_rebalance():
        """Get the current auto-rebalance setting."""
        return jsonify({"enabled": _get_auto_rebalance_enabled()})

    @app.post("/api/auto-rebalance")
    @login_required
    def set_auto_rebalance():
        """Enable or disable automatic strategy rebalancing."""
        data = request.get_json()
        enabled = data.get("enabled", False)
        _set_auto_rebalance_enabled(bool(enabled))
        return jsonify({"enabled": enabled, "message": f"Auto-rebalance {'enabled' if enabled else 'disabled'}"})

    @app.post("/api/reset-for-testing")
    @login_required
    def reset_for_testing():
        """
        DANGER: Reset all trading state for testing purposes.
        Clears all trades, positions, and resets bots to initial state.
        Only use this in testnet/paper trading!

        SAFETY: Requires trading to be paused or all positions liquidated before reset.
        """
        from app.storage import store
        from app.portfolio import EXECUTION_MODE, SYMBOLS
        from app.strategies import MR_GRID, BO_GRID, TF_GRID
        from app.portfolio import _get_capital_per_bot

        if EXECUTION_MODE not in ["paper", "binance_testnet"]:
            return jsonify({"error": "Reset only allowed in paper/testnet mode"}), 403

        # Safety check: Require trading to be paused first
        if not _get_trading_paused():
            return jsonify({
                "error": "Trading must be paused before reset",
                "message": "For safety, pause trading or liquidate all positions before resetting.",
                "action_required": "pause_or_liquidate"
            }), 400

        try:
            # Clear all trades and decision log
            with store._lock:
                store._conn.execute("DELETE FROM trades")
                store._conn.execute("DELETE FROM equity_history")
                store._conn.commit()

            # Clear decision log
            from app.bots import clear_decision_log
            clear_decision_log()

            # Delete orphaned bot records (bots in DB but not in current portfolio)
            current_bot_names = {bot.name for manager in _pm.managers for bot in manager.bots}
            all_bot_records = store.load_bots()
            orphaned_bots = [name for name in all_bot_records.keys() if name not in current_bot_names and name != "manual_trade"]

            if orphaned_bots:
                print(f"\n🧹 Cleaning up {len(orphaned_bots)} orphaned bot records from database...")
                with store._lock:
                    for bot_name in orphaned_bots:
                        store._conn.execute("DELETE FROM bots WHERE name = ?", (bot_name,))
                    store._conn.commit()
                print(f"✓ Deleted orphaned bots: {', '.join(orphaned_bots[:5])}{' ...' if len(orphaned_bots) > 5 else ''}\n")

            # Count ACTUAL bots currently running (not hardcoded grid logic)
            total_bots = sum(len(manager.bots) for manager in _pm.managers)
            initial_capital = _get_capital_per_bot(total_bots)

            print(f"\n{'='*60}")
            print(f"RESET: Recalculating capital allocation")
            print(f"  Total bots: {total_bots}")
            print(f"  Capital per bot: ${initial_capital:.2f}")
            print(f"  Total starting capital: ${total_bots * initial_capital:.2f}")
            print(f"{'='*60}\n")

            # Reset all bots to initial state
            reset_count = 0
            for manager in _pm.managers:
                for bot in manager.bots:
                    # Reset both allocation AND starting_allocation to fresh initial capital
                    bot.allocation = initial_capital
                    bot.starting_allocation = initial_capital  # CRITICAL: Reset P&L baseline
                    bot.metrics.cash = initial_capital
                    bot.metrics.pos_qty = 0.0
                    bot.metrics.avg_price = 0.0
                    bot.metrics.equity = initial_capital
                    bot.metrics.cum_pnl = 0.0
                    bot.metrics.trades = 0
                    bot.metrics.score = 0.0
                    reset_count += 1

                    # Update DB
                    params = bot.strategy.to_params() if hasattr(bot.strategy, "to_params") else {}
                    store.upsert_bot(
                        name=bot.name,
                        manager=manager.name,
                        symbol=bot.symbol,
                        tf=bot.tf,
                        strategy=type(bot.strategy).__name__,
                        params=params,
                        allocation=bot.allocation,
                        starting_allocation=bot.starting_allocation,
                        cash=bot.metrics.cash,
                        pos_qty=bot.metrics.pos_qty,
                        avg_price=bot.metrics.avg_price,
                        equity=bot.metrics.equity,
                        score=bot.metrics.score,
                        trades=bot.metrics.trades,
                    )

            print(f"✓ Reset {reset_count} bots to ${initial_capital:.2f} each\n")

            total_equity = total_bots * initial_capital

            return jsonify({
                "success": True,
                "message": "All trading state has been reset",
                "trades_cleared": True,
                "bots_reset": total_bots,
                "capital_per_bot": initial_capital,
                "total_equity": total_equity
            })

        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.post("/api/pause-trading")
    @login_required
    def pause_trading():
        """Pause all trading. Bots will stop executing trades but keep positions."""
        _set_trading_paused(True)
        print("\n🛑 TRADING PAUSED - No new trades will be executed\n")
        return jsonify({
            "success": True,
            "message": "Trading paused. No new trades will be executed.",
            "trading_paused": True
        })

    @app.post("/api/resume-trading")
    @login_required
    def resume_trading():
        """Resume trading after pause."""
        _set_trading_paused(False)
        print("\n▶️  TRADING RESUMED - Bots will execute trades normally\n")
        return jsonify({
            "success": True,
            "message": "Trading resumed. Bots will execute trades normally.",
            "trading_paused": False
        })

    @app.get("/api/trading-status")
    @login_required
    def trading_status():
        """Get current trading pause status, capital limit, timeframe, and portfolio config."""
        from app.storage import store
        capital_limit = store.get_setting("capital_limit_usdt", default=None)
        timeframe = store.get_setting("trading_timeframe", default="1d")
        num_strategies = store.get_setting("num_active_strategies", default=5)
        execution_mode = store.get_setting("execution_mode", default="binance_testnet")

        return jsonify({
            "trading_paused": _get_trading_paused(),
            "capital_limit_usdt": capital_limit,
            "trading_timeframe": timeframe,
            "num_active_strategies": int(num_strategies),
            "execution_mode": execution_mode
        })

    @app.post("/api/set-capital-limit")
    @login_required
    def set_capital_limit():
        """Set the maximum USDT capital to use for trading."""
        from app.storage import store
        data = request.json

        if not data or "capital_limit_usdt" not in data:
            return jsonify({"error": "capital_limit_usdt required"}), 400

        limit = float(data["capital_limit_usdt"])
        if limit <= 0:
            return jsonify({"error": "capital_limit_usdt must be positive"}), 400

        store.set_setting("capital_limit_usdt", limit)

        return jsonify({
            "success": True,
            "capital_limit_usdt": limit,
            "message": f"Capital limit set to ${limit:,.2f} USDT. Restart required to apply."
        })

    @app.delete("/api/set-capital-limit")
    @login_required
    def clear_capital_limit():
        """Clear capital limit (use full balance)."""
        from app.storage import store

        # Delete the setting by setting it to None
        store.set_setting("capital_limit_usdt", None)

        return jsonify({
            "success": True,
            "capital_limit_usdt": None,
            "message": "Capital limit removed. Will use 90% of available balance. Restart required to apply."
        })

    @app.post("/api/set-timeframe")
    @login_required
    def set_timeframe():
        """Set the trading timeframe. CRITICAL: Must match optimization/evolution timeframe!"""
        from app.storage import store
        data = request.json

        if not data or "timeframe" not in data:
            return jsonify({"error": "timeframe required"}), 400

        timeframe = str(data["timeframe"])
        valid_timeframes = ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"]

        if timeframe not in valid_timeframes:
            return jsonify({"error": f"timeframe must be one of: {', '.join(valid_timeframes)}"}), 400

        store.set_setting("trading_timeframe", timeframe)

        return jsonify({
            "success": True,
            "timeframe": timeframe,
            "message": f"⚠️ Timeframe set to {timeframe}. RESTART REQUIRED. Ensure your strategies were optimized on {timeframe}!",
            "warning": "Using a different timeframe than optimization will result in poor performance!"
        })

    @app.post("/api/set-num-strategies")
    @login_required
    def set_num_strategies():
        """Set number of active strategies to run in portfolio."""
        from app.storage import store
        data = request.json

        if not data or "num_strategies" not in data:
            return jsonify({"error": "num_strategies required"}), 400

        num_strategies = int(data["num_strategies"])
        if num_strategies < 1 or num_strategies > 20:
            return jsonify({"error": "num_strategies must be between 1 and 20"}), 400

        store.set_setting("num_active_strategies", num_strategies)

        return jsonify({
            "success": True,
            "num_strategies": num_strategies,
            "message": f"Portfolio will run top {num_strategies} evolved strategies. Restart required to apply."
        })

    @app.post("/api/set-execution-mode")
    @login_required
    def set_execution_mode():
        """Set the execution mode (paper, binance_testnet, or live)."""
        from app.storage import store
        data = request.json

        if not data or "execution_mode" not in data:
            return jsonify({"error": "execution_mode required"}), 400

        mode = str(data["execution_mode"])
        valid_modes = ["paper", "binance_testnet"]  # Live not yet implemented

        if mode not in valid_modes:
            return jsonify({"error": f"execution_mode must be one of: {', '.join(valid_modes)}"}), 400

        store.set_setting("execution_mode", mode)

        mode_labels = {
            "paper": "📝 Paper Trading (simulated)",
            "binance_testnet": "🧪 Binance Testnet (fake money)"
        }

        return jsonify({
            "success": True,
            "execution_mode": mode,
            "message": f"Execution mode set to {mode_labels.get(mode, mode)}. Restart required to apply."
        })

    @app.post("/api/liquidate-all")
    @login_required
    def liquidate_all():
        """
        EMERGENCY: Close all open positions immediately and pause trading.
        This will sell all crypto positions and convert to USDT.
        """
        from app.portfolio import EXECUTION_MODE

        if EXECUTION_MODE not in ["paper", "binance_testnet"]:
            return jsonify({"error": "Liquidation only allowed in paper/testnet mode"}), 403

        try:
            # Pause trading first
            _set_trading_paused(True)
            print("\n🚨 EMERGENCY LIQUIDATION INITIATED 🚨")
            print("   Trading paused - closing all positions\n")

            liquidated_positions = []
            total_liquidated_value = 0.0

            # Loop through all bots and close any open positions
            for manager in _pm.managers:
                for bot in manager.bots:
                    # Check if bot has an open position
                    if bot.metrics.pos_qty != 0:
                        qty = abs(bot.metrics.pos_qty)
                        side = "sell" if bot.metrics.pos_qty > 0 else "buy"  # Close position

                        # Get current price
                        from app.data import GateAdapter
                        data = GateAdapter()
                        bars = data.history(bot.symbol, bot.tf, limit=1)
                        if not bars:
                            continue
                        current_price = bars[-1].close

                        # Execute market order to close position
                        try:
                            result = bot.exec.paper_order(bot.symbol, side, qty, price_hint=current_price)

                            if result.get("status") == "filled":
                                filled_qty = result.get("filled_qty", qty)
                                avg_price = result.get("avg_price", current_price)
                                fee = result.get("fee", 0.0)

                                # Update bot metrics
                                if side == "sell":
                                    proceeds = filled_qty * avg_price - fee
                                    bot.metrics.cash += proceeds
                                    bot.metrics.pos_qty = 0
                                else:  # buy to close short
                                    cost = filled_qty * avg_price + fee
                                    bot.metrics.cash -= cost
                                    bot.metrics.pos_qty = 0

                                bot.metrics.equity = bot.metrics.cash
                                bot.metrics.avg_price = 0.0

                                liquidated_positions.append({
                                    "bot": bot.name,
                                    "symbol": bot.symbol,
                                    "side": side,
                                    "quantity": filled_qty,
                                    "price": avg_price,
                                    "value": filled_qty * avg_price
                                })

                                total_liquidated_value += filled_qty * avg_price

                                print(f"   ✓ Liquidated {bot.name}: {side} {filled_qty} {bot.symbol} @ ${avg_price:.2f}")

                        except Exception as e:
                            print(f"   ✗ Failed to liquidate {bot.name}: {e}")

            print(f"\n✓ Liquidation complete")
            print(f"   Positions closed: {len(liquidated_positions)}")
            print(f"   Total value liquidated: ${total_liquidated_value:.2f}")
            print(f"   Trading remains PAUSED\n")

            return jsonify({
                "success": True,
                "message": f"Liquidated {len(liquidated_positions)} positions. Trading paused.",
                "positions_closed": len(liquidated_positions),
                "total_value": total_liquidated_value,
                "liquidated_positions": liquidated_positions,
                "trading_paused": True
            })

        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.post("/api/manual-trade")
    @login_required
    def manual_trade():
        """
        Execute a manual trade on the exchange.

        Request body:
        {
            "symbol": "BTC_USDT",
            "side": "buy" or "sell",
            "quantity": 0.001,  // amount in crypto
            "order_type": "market" or "limit",
            "limit_price": 42000.50  // required if order_type is "limit"
        }
        """
        from app.portfolio import EXECUTION_MODE
        from app.execution import BinanceTestnetExec, PaperExec

        if EXECUTION_MODE not in ["paper", "binance_testnet"]:
            return jsonify({"error": "Manual trading only allowed in paper/testnet mode"}), 403

        data = request.get_json()

        # Validate required fields
        symbol = data.get("symbol")
        side = data.get("side", "").lower()
        quantity = data.get("quantity")
        order_type = data.get("order_type", "market").lower()

        if not symbol or symbol not in ["BTC_USDT", "ETH_USDT", "SOL_USDT", "USDC_USDT"]:
            return jsonify({"error": "Invalid symbol. Must be BTC_USDT, ETH_USDT, SOL_USDT, or USDC_USDT"}), 400

        if side not in ["buy", "sell"]:
            return jsonify({"error": "Invalid side. Must be 'buy' or 'sell'"}), 400

        try:
            quantity = float(quantity)
            if quantity <= 0:
                return jsonify({"error": "Quantity must be positive"}), 400
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid quantity"}), 400

        try:
            # Get execution client
            if EXECUTION_MODE == "binance_testnet":
                client = BinanceTestnetExec("manual_trade")
            else:
                client = PaperExec("manual_trade")

            # Execute trade based on order type
            if order_type == "market":
                # For market orders, we need a price hint
                # Fetch current price first
                binance_symbol = symbol.replace('_', '')
                price_data = client.exchange.publicGetTickerPrice({'symbol': binance_symbol})
                current_price = float(price_data['price'])

                result = client.paper_order(symbol, side, quantity, price_hint=current_price)

            elif order_type == "limit":
                limit_price = data.get("limit_price")
                if limit_price is None:
                    return jsonify({"error": "limit_price required for limit orders"}), 400

                try:
                    limit_price = float(limit_price)
                    if limit_price <= 0:
                        return jsonify({"error": "limit_price must be positive"}), 400
                except (TypeError, ValueError):
                    return jsonify({"error": "Invalid limit_price"}), 400

                result = client.limit_order(symbol, side, quantity, limit_price, timeout=60.0)

            else:
                return jsonify({"error": "Invalid order_type. Must be 'market' or 'limit'"}), 400

            # Return trade result
            return jsonify({
                "success": True,
                "trade": result,
                "message": f"Successfully executed {side} {quantity} {symbol}"
            })

        except Exception as e:
            return jsonify({"error": f"Trade execution failed: {str(e)}"}), 500

    @app.post("/api/auto-assign-strategies")
    @login_required
    def auto_assign_strategies():
        """Automatically assign workers to strategies based on performance."""
        from app.strategies import MeanReversion, Breakout, TrendFollow, MR_GRID, BO_GRID, TF_GRID
        from app.storage import store

        # Check if portfolio has managers
        if not _pm.managers:
            return jsonify({"error": "No strategy managers available"}), 400

        # Calculate strategy performance
        strategy_scores = {}
        for m in _pm.managers:
            if not m.bots:
                continue
            avg_score = sum(b.metrics.score for b in m.bots) / len(m.bots)
            strategy_scores[m.name] = avg_score

        # Check if we have any strategies with bots
        if not strategy_scores:
            return jsonify({"error": "No active bots found"}), 400

        # Find best performing strategy
        best_strategy = max(strategy_scores, key=strategy_scores.get)

        # Map manager names to strategy classes
        manager_to_strategy = {
            "mean_reversion": ("MeanReversion", MeanReversion, MR_GRID),
            "breakout": ("Breakout", Breakout, BO_GRID),
            "trend_follow": ("TrendFollow", TrendFollow, TF_GRID),
        }

        # Handle evolved strategies portfolio (doesn't support auto-reassignment)
        if best_strategy not in manager_to_strategy:
            return jsonify({
                "error": "Auto-assignment is only supported for fallback strategies (mean_reversion, breakout, trend_follow). "
                        "Current portfolio uses evolved strategies which are already optimized. "
                        "Use the built-in rebalancing instead."
            }), 400

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

    # ── Price Alerts ───────────────────────────────────────────────────────────
    @app.get("/api/price-alerts")
    @login_required
    def get_price_alerts():
        """List all price alerts, optionally filtered by status."""
        from app.storage import store

        status = request.args.get("status")
        email = request.args.get("email")

        try:
            alerts = store.list_price_alerts(status=status, email=email)
            return jsonify({
                "success": True,
                "alerts": alerts,
                "count": len(alerts)
            })
        except Exception as e:
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    @app.post("/api/price-alerts")
    @login_required
    def create_price_alert():
        """Create a new price alert."""
        from app.storage import store

        data = request.get_json()
        if not data:
            return jsonify({
                "success": False,
                "error": "No data provided"
            }), 400

        symbol = data.get("symbol")
        target_price = data.get("target_price")
        condition = data.get("condition")
        email = data.get("email")

        # Validation
        if not all([symbol, target_price, condition, email]):
            return jsonify({
                "success": False,
                "error": "Missing required fields: symbol, target_price, condition, email"
            }), 400

        if condition not in ["above", "below"]:
            return jsonify({
                "success": False,
                "error": "Invalid condition. Must be 'above' or 'below'"
            }), 400

        try:
            target_price = float(target_price)
            if target_price <= 0:
                return jsonify({
                    "success": False,
                    "error": "Target price must be positive"
                }), 400
        except (ValueError, TypeError):
            return jsonify({
                "success": False,
                "error": "Invalid target price"
            }), 400

        # Basic email validation
        if "@" not in email or "." not in email:
            return jsonify({
                "success": False,
                "error": "Invalid email address"
            }), 400

        try:
            alert_id = store.create_price_alert(
                symbol=symbol,
                target_price=target_price,
                condition=condition,
                email=email
            )

            return jsonify({
                "success": True,
                "alert_id": alert_id,
                "message": f"Price alert created: {symbol} {condition} {target_price}"
            })
        except Exception as e:
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    @app.delete("/api/price-alerts/<int:alert_id>")
    @login_required
    def delete_price_alert(alert_id: int):
        """Delete a price alert."""
        from app.storage import store

        try:
            deleted = store.delete_price_alert(alert_id)
            if deleted:
                return jsonify({
                    "success": True,
                    "message": f"Alert {alert_id} deleted"
                })
            else:
                return jsonify({
                    "success": False,
                    "error": f"Alert {alert_id} not found"
                }), 404
        except Exception as e:
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    @app.post("/api/price-alerts/<int:alert_id>/cancel")
    @login_required
    def cancel_price_alert(alert_id: int):
        """Cancel a price alert (set status to 'cancelled')."""
        from app.storage import store

        try:
            updated = store.update_alert_status(alert_id, "cancelled")
            if updated:
                return jsonify({
                    "success": True,
                    "message": f"Alert {alert_id} cancelled"
                })
            else:
                return jsonify({
                    "success": False,
                    "error": f"Alert {alert_id} not found"
                }), 404
        except Exception as e:
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    @app.post("/api/price-alerts/check")
    @login_required
    def check_price_alerts_manually():
        """Manually trigger price alert check (for testing)."""
        from app.alert_monitor import check_price_alerts
        from app.data import GateAdapter

        try:
            gate = GateAdapter()
            results = check_price_alerts(gate)
            return jsonify({
                "success": True,
                "results": results
            })
        except Exception as e:
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    @app.post("/api/price-alerts/test-email")
    @login_required
    def send_test_email():
        """Send a test email to verify SMTP configuration."""
        from app.notifications import email_notifier

        data = request.get_json()
        if not data or "email" not in data:
            return jsonify({
                "success": False,
                "error": "Email address required"
            }), 400

        email = data["email"]

        # Basic email validation
        if "@" not in email or "." not in email:
            return jsonify({
                "success": False,
                "error": "Invalid email address"
            }), 400

        try:
            sent = email_notifier.send_test_email(email)
            if sent:
                return jsonify({
                    "success": True,
                    "message": f"Test email sent to {email}"
                })
            else:
                return jsonify({
                    "success": False,
                    "error": "Failed to send test email. Check SMTP configuration."
                }), 500
        except Exception as e:
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    @app.get("/health")
    @login_required
    def health():
        return {"ok": True}

    @app.get("/")
    @login_required
    def home():
        return redirect(url_for("ui"))

    @app.get("/ui")
    @login_required
    def ui():
        return render_template("portfolio.html")

    @app.get("/backtest-ui")
    @login_required
    def backtest_ui():
        return render_template("backtest.html")

    @app.get("/data-ui")
    @login_required
    def data_ui():
        return render_template("data.html")

    @app.get("/optimizer-ui")
    @login_required
    def optimizer_ui():
        return render_template("optimizer.html")

    @app.get("/evolution-ui")
    @login_required
    def evolution_ui():
        return render_template("evolution.html")

    @app.get("/backtest/strategies")
    @login_required
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
    @login_required
    def list_saved_backtests():
        """List all saved backtest configurations."""
        from app.storage import store
        saved = store.list_saved_backtests()
        return jsonify({"saved": saved})

    @app.post("/backtest/saved")
    @login_required
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
    @login_required
    def delete_saved_backtest(backtest_id: int):
        """Delete a saved backtest configuration."""
        from app.storage import store

        deleted = store.delete_saved_backtest(backtest_id)
        if deleted:
            return jsonify({"deleted": True})
        else:
            return jsonify({"error": "Backtest not found"}), 404

    @app.post("/backtest")
    @login_required
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
    @login_required
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
    @login_required
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
    @login_required
    def list_optimizer_results():
        """List optimization results, optionally filtered by strategy/symbol."""
        from app.storage import store

        strategy = request.args.get("strategy")
        symbol = request.args.get("symbol")
        limit = int(request.args.get("limit", 100))

        results = store.list_optimization_results(strategy=strategy, symbol=symbol, limit=limit)
        return jsonify({"results": results})

    @app.post("/optimizer/promote/<int:result_id>")
    @login_required
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
    @login_required
    def list_evolution_results():
        """List evolved strategies, optionally filtered by symbol and minimum score."""
        from app.storage import store

        symbol = request.args.get("symbol")
        min_score = request.args.get("min_score", type=float)
        limit = int(request.args.get("limit", 100))

        results = store.list_evolved_strategies(symbol=symbol, min_score=min_score, limit=limit)
        return jsonify({"results": results})

    @app.post("/evolution/promote/<int:strategy_id>")
    @login_required
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
