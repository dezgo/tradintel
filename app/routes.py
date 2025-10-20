# app/routes.py
from flask import Blueprint, render_template, request, redirect, url_for, jsonify
from .bot import BOT, _CONTEXT_LOOKBACK
from .gate_api import get_order_book, get_currencies
from .settings_store import save_settings
from app.models import recent_trades

bp = Blueprint("routes", __name__)


@bp.get("/trades")
def trades_json():
    return jsonify(recent_trades(50))


@bp.route("/")
def index():
    status = BOT.get_status()
    return render_template("index.html", status=status)


@bp.route("/start", methods=["POST"])
def start():
    BOT.start()
    return redirect(url_for("routes.index"))


@bp.route("/stop", methods=["POST"])
def stop():
    BOT.stop()
    return redirect(url_for("routes.index"))


@bp.route("/strategy", methods=["POST"], endpoint="strategy_post")
def strategy_post():
    symbols = request.form.get("symbols", "ETH_USDT")
    timeframes = request.form.get("timeframes", "15m,1h,1d")
    poll = int(request.form.get("poll", "60") or "60")
    source = request.form.get("source", "gate").strip().lower()
    if source not in ("gate", "binance"):
        source = "gate"

    BOT.symbols = [s.strip() for s in symbols.split(",") if s.strip()]
    BOT.timeframes = [t.strip() for t in timeframes.split(",") if t.strip()]
    BOT.poll_sec = max(10, poll)
    BOT.data_source = source

    save_settings(BOT.symbols, BOT.timeframes, BOT.poll_sec, BOT.data_source)
    return redirect(url_for("routes.index"))


@bp.route("/signals.json")
def signals_json():
    return jsonify(BOT.get_status())


@bp.route("/balances")
def balances():
    try:
        data = get_currencies()
        return render_template("balances.html", balances=data, error=None)
    except Exception as e:
        return render_template("balances.html", balances=None, error=str(e))


@bp.route("/orderbook")
def orderbook():
    pair = request.args.get("pair", "BTC_USDT")
    try:
        book = get_order_book(pair, limit=15)
        return render_template("orderbook.html", book=book, pair=pair)
    except Exception as e:
        return render_template("orderbook.html", book=None, pair=pair, error=str(e))


@bp.route("/debug_candles")
def debug_candles():
    from .bot import _closed_only
    from .sources import get_candles_gate, get_candles_binance

    fetch = get_candles_binance if BOT.data_source == "binance" else get_candles_gate
    out = {}
    for pair in BOT.symbols:
        out[pair] = {}
        for tf in BOT.timeframes:
            raw = fetch(pair, tf, limit=40)
            closed = _closed_only(raw, tf)
            closed = closed[-(_CONTEXT_LOOKBACK + 5):]  # same windowing
            rows = []
            for c in closed:
                rows.append({
                    "ts_open": c.ts,
                    "open": c.open,
                    "high": c.high,
                    "low": c.low,
                    "close": c.close,
                    "vol": c.vol,
                })
            out[pair][tf] = rows
    return jsonify(out)
