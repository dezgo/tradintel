# app/__init__.py
from __future__ import annotations

from flask import Flask, jsonify

from .routes import bp as routes_bp
from .bot import BOT
from .settings_store import load_settings
from .data_adapter import GateAdapter
from .portfolio import Portfolio


portfolio: Portfolio | None = None


def create_app() -> Flask:
    app = Flask(__name__)
    app.register_blueprint(routes_bp)

    # Keep your existing alert-scanner config alive
    cfg = load_settings()
    BOT.symbols = cfg["symbols"]
    BOT.timeframes = cfg["timeframes"]
    BOT.poll_sec = cfg["poll_sec"]
    BOT.data_source = cfg.get("source", "gate")

    # Portfolio MVP using Gate public candles via the adapter
    adapter = GateAdapter()
    global portfolio
    portfolio = Portfolio(adapter)
    portfolio.start(interval_sec=30)

    @app.get("/portfolio")
    def portfolio_summary():
        return jsonify(portfolio.summary())

    return app
