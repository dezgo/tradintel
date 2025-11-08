# ───────────────────────────────────────────────────────────────────────────────
# app/execution.py
from __future__ import annotations

from typing import Dict, Optional
from app.core import ExecutionClient
from app.storage import store


class PaperExec(ExecutionClient):
    def __init__(self, bot_name: str):
        self.bot_name = bot_name

    def paper_order(
        self, symbol: str, side: str, qty: float, price_hint: Optional[float] = None
    ) -> Dict:
        price = float(price_hint or 0.0)
        # TODO: persist to DB; for now return a fill-like dict
        store.record_trade(self.bot_name, symbol, side, float(qty), price)
        return {"status": "filled", "symbol": symbol, "side": side, "qty": float(qty), "price": price}
