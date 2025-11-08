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

    return app
