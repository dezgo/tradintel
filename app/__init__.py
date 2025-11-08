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


def create_app() -> Flask:
    app = Flask(__name__)

    global _pm, _runner_thread
    _pm = build_portfolio()

    if not os.getenv("APP_DISABLE_LOOP"):
        def _loop():
            from app.portfolio import TF
            SEC = 60 if TF == "1m" else 300 if TF == "5m" else 60  # fallback
            while True:
                try:
                    _pm.step()
                    # periodically refresh parameter sets with walk-forward
                    data = getattr(_pm.managers[0].bots[0], "data", None)
                    if data is not None:
                        _selector.maybe_refresh(_pm, data, TF)

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

        # Validate strategy
        strategy_map = {
            "MeanReversion": MeanReversion,
            "Breakout": Breakout,
            "TrendFollow": TrendFollow,
        }

        if strategy_name not in strategy_map:
            return jsonify({"error": f"Unknown strategy: {strategy_name}"}), 400

        # Create strategy instance
        try:
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
        with store._lock:
            cur = store._conn.execute(
                "SELECT symbol, timeframe, MIN(ts), MAX(ts), COUNT(*) FROM bars GROUP BY symbol, timeframe"
            )
            rows = cur.fetchall()

        items = [
            {
                "symbol": r[0],
                "timeframe": r[1],
                "start_ts": int(r[2]),
                "end_ts": int(r[3]),
                "count": int(r[4]),
            }
            for r in rows
        ]

        return jsonify({"items": items})

    @app.post("/data/backfill")
    def backfill_data():
        """
        Backfill daily data from CoinGecko.

        Request body:
        {
            "symbols": ["BTC_USDT", "ETH_USDT", "SOL_USDT"],
            "days": 365  // optional, defaults to 365
        }

        Returns:
        {
            "results": {
                "BTC_USDT": "✓ Cached 365 daily bars",
                "ETH_USDT": "✓ Cached 365 daily bars",
                ...
            }
        }
        """
        from app.data_cache import backfill_daily_data

        body = request.get_json()
        if not body or "symbols" not in body:
            return jsonify({"error": "Request body must include 'symbols' array"}), 400

        symbols = body.get("symbols", [])
        days = body.get("days", 365)

        if not isinstance(symbols, list) or not symbols:
            return jsonify({"error": "'symbols' must be a non-empty array"}), 400

        results = backfill_daily_data(symbols, days)

        return jsonify({"results": results})

    return app
