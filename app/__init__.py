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
_selector = AutoParamSelector()  # default: refresh every 30m
_auto_rebalance_enabled = False  # Global flag for automatic strategy rebalancing


def create_app() -> Flask:
    app = Flask(__name__)

    global _pm, _runner_thread
    _pm = build_portfolio()

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

    return app
