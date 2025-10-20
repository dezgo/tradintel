from __future__ import annotations

from typing import Dict

from app.models import insert_trade


class PaperBroker:
    def __init__(self) -> None:
        pass

    def paper_order(self, bot_name: str, symbol: str, side: str, qty: float, price_hint: float | None = None) -> Dict:
        price = float(price_hint or 0.0)
        insert_trade(bot=bot_name, symbol=symbol, side=side, qty=float(qty), price=price)
        return {"status": "filled", "price": price}
