# ───────────────────────────────────────────────────────────────────────────────
# app/execution.py
from __future__ import annotations

import os
import random
import time
from typing import Dict, Optional
from app.core import ExecutionClient
from app.storage import store

try:
    import ccxt
    CCXT_AVAILABLE = True
except ImportError:
    CCXT_AVAILABLE = False


class PaperExec(ExecutionClient):
    """
    Paper trading execution client with realistic fee simulation.

    Simulates Binance-like fees:
    - Maker: 0% (assumes using BNB for fees or VIP tier)
    - Taker: 0.1% (standard rate)
    """

    MAKER_FEE_RATE = 0.0000  # 0% maker fee
    TAKER_FEE_RATE = 0.0010  # 0.1% taker fee

    def __init__(self, bot_name: str):
        self.bot_name = bot_name

    def paper_order(
        self, symbol: str, side: str, qty: float, price_hint: Optional[float] = None
    ) -> Dict:
        """Legacy market order - always taker, applies 0.1% fee."""
        price = float(price_hint or 0.0)

        # Market orders are always taker
        notional = qty * price
        fee = notional * self.TAKER_FEE_RATE

        store.record_trade(self.bot_name, symbol, side, float(qty), price, fee=fee, is_maker=False)

        return {
            "status": "filled",
            "symbol": symbol,
            "side": side,
            "qty": float(qty),
            "price": price,
            "is_maker": False,
            "fee": fee,
            "fee_rate": self.TAKER_FEE_RATE
        }

    def limit_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        limit_price: float,
        timeout: float = 60.0
    ) -> Dict:
        """
        Simulated limit order with maker/taker fee logic.

        Simulation logic:
        - 80% chance fills as maker (0% fee) - order sits on book and gets filled
        - 20% chance fills as taker (0.1% fee) - aggressive price, fills immediately

        In reality, this depends on:
        - How far your limit is from current market
        - Market volatility
        - Order book depth
        """
        # Simulate fill probability based on limit order placement
        is_maker = random.random() < 0.80  # 80% maker, 20% taker

        fee_rate = self.MAKER_FEE_RATE if is_maker else self.TAKER_FEE_RATE
        notional = qty * limit_price
        fee = notional * fee_rate

        # Record to storage
        store.record_trade(
            self.bot_name,
            symbol,
            side,
            float(qty),
            limit_price,
            fee=fee,
            is_maker=is_maker
        )

        return {
            "status": "filled",
            "filled_qty": qty,
            "avg_price": limit_price,
            "symbol": symbol,
            "side": side,
            "is_maker": is_maker,
            "fee": fee,
            "fee_rate": fee_rate
        }


class BinanceTestnetExec(ExecutionClient):
    """
    Binance Testnet execution client - uses real Binance testnet APIs.

    Setup:
    1. Create testnet API keys at https://testnet.binance.vision/
    2. Set environment variables:
       - BINANCE_TESTNET_API_KEY
       - BINANCE_TESTNET_API_SECRET
    """

    def __init__(self, bot_name: str):
        if not CCXT_AVAILABLE:
            raise RuntimeError("CCXT library not installed. Run: pip install ccxt")

        self.bot_name = bot_name
        api_key = os.getenv("BINANCE_TESTNET_API_KEY")
        api_secret = os.getenv("BINANCE_TESTNET_API_SECRET")

        if not api_key or not api_secret:
            raise ValueError(
                "Missing Binance testnet credentials. Set BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_API_SECRET"
            )

        self.exchange = ccxt.binance({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'spot',
            }
        })

        # Override with testnet URLs - Binance spot testnet
        self.exchange.urls['api'] = {
            'public': 'https://testnet.binance.vision/api',
            'private': 'https://testnet.binance.vision/api',
        }

    def paper_order(
        self, symbol: str, side: str, qty: float, price_hint: Optional[float] = None
    ) -> Dict:
        """Market order on testnet - always taker fees."""
        try:
            # Convert symbol format: BTC_USDT -> BTC/USDT
            ccxt_symbol = symbol.replace('_', '/')

            # Place market order
            order = self.exchange.create_market_order(ccxt_symbol, side, qty)

            # Extract fill info
            filled_qty = float(order.get('filled', qty))
            avg_price = float(order.get('average', price_hint or 0))
            fee_info = order.get('fee', {})
            fee = float(fee_info.get('cost', 0))

            # Record trade
            store.record_trade(
                self.bot_name,
                symbol,
                side,
                filled_qty,
                avg_price,
                fee=fee,
                is_maker=False
            )

            return {
                "status": order.get('status', 'filled'),
                "symbol": symbol,
                "side": side,
                "qty": filled_qty,
                "price": avg_price,
                "is_maker": False,
                "fee": fee,
                "fee_rate": fee / (filled_qty * avg_price) if filled_qty * avg_price > 0 else 0
            }

        except Exception as e:
            print(f"Binance testnet market order failed: {e}")
            # Fallback to paper simulation
            return PaperExec(self.bot_name).paper_order(symbol, side, qty, price_hint)

    def limit_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        limit_price: float,
        timeout: float = 60.0
    ) -> Dict:
        """Real limit order on Binance testnet with timeout."""
        try:
            # Convert symbol format
            ccxt_symbol = symbol.replace('_', '/')

            # Place limit order
            order = self.exchange.create_limit_order(ccxt_symbol, side, qty, limit_price)
            order_id = order['id']

            # Wait for fill with timeout
            start_time = time.time()
            while time.time() - start_time < timeout:
                order = self.exchange.fetch_order(order_id, ccxt_symbol)
                status = order.get('status', '')

                if status == 'closed':  # Filled
                    filled_qty = float(order.get('filled', qty))
                    avg_price = float(order.get('average', limit_price))
                    fee_info = order.get('fee', {})
                    fee = float(fee_info.get('cost', 0))

                    # Determine if maker or taker
                    # On Binance, if order was on the book before fill, it's maker
                    is_maker = order.get('maker', True)  # Default assume maker

                    # Record trade
                    store.record_trade(
                        self.bot_name,
                        symbol,
                        side,
                        filled_qty,
                        avg_price,
                        fee=fee,
                        is_maker=is_maker
                    )

                    return {
                        "status": "filled",
                        "filled_qty": filled_qty,
                        "avg_price": avg_price,
                        "symbol": symbol,
                        "side": side,
                        "is_maker": is_maker,
                        "fee": fee,
                        "fee_rate": fee / (filled_qty * avg_price) if filled_qty * avg_price > 0 else 0
                    }

                elif status in ['canceled', 'expired']:
                    return {"status": "cancelled", "filled_qty": 0}

                time.sleep(2)  # Poll every 2 seconds

            # Timeout - cancel order
            try:
                self.exchange.cancel_order(order_id, ccxt_symbol)
            except:
                pass  # Already filled or cancelled

            return {"status": "timeout", "filled_qty": 0}

        except Exception as e:
            print(f"Binance testnet limit order failed: {e}")
            # Fallback to paper simulation
            return PaperExec(self.bot_name).limit_order(symbol, side, qty, limit_price, timeout)


class GateTestnetExec(ExecutionClient):
    """
    Gate.io Testnet execution client - uses real Gate.io testnet APIs.

    Setup:
    1. Create testnet API keys at https://www.gate.io/testnet
    2. Set environment variables:
       - GATE_TESTNET_API_KEY
       - GATE_TESTNET_API_SECRET
    """

    def __init__(self, bot_name: str):
        if not CCXT_AVAILABLE:
            raise RuntimeError("CCXT library not installed. Run: pip install ccxt")

        self.bot_name = bot_name
        api_key = os.getenv("GATE_TESTNET_API_KEY")
        api_secret = os.getenv("GATE_TESTNET_API_SECRET")

        if not api_key or not api_secret:
            raise ValueError(
                "Missing Gate.io testnet credentials. Set GATE_TESTNET_API_KEY and GATE_TESTNET_API_SECRET"
            )

        self.exchange = ccxt.gate({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'spot',
            }
        })
        # Gate.io testnet URL
        self.exchange.urls['api'] = 'https://fx-api-testnet.gateio.ws'

    def paper_order(
        self, symbol: str, side: str, qty: float, price_hint: Optional[float] = None
    ) -> Dict:
        """Market order on Gate.io testnet."""
        try:
            ccxt_symbol = symbol.replace('_', '/')
            order = self.exchange.create_market_order(ccxt_symbol, side, qty)

            filled_qty = float(order.get('filled', qty))
            avg_price = float(order.get('average', price_hint or 0))
            fee_info = order.get('fee', {})
            fee = float(fee_info.get('cost', 0))

            store.record_trade(
                self.bot_name,
                symbol,
                side,
                filled_qty,
                avg_price,
                fee=fee,
                is_maker=False
            )

            return {
                "status": order.get('status', 'filled'),
                "symbol": symbol,
                "side": side,
                "qty": filled_qty,
                "price": avg_price,
                "is_maker": False,
                "fee": fee,
                "fee_rate": fee / (filled_qty * avg_price) if filled_qty * avg_price > 0 else 0
            }

        except Exception as e:
            print(f"Gate.io testnet market order failed: {e}")
            return PaperExec(self.bot_name).paper_order(symbol, side, qty, price_hint)

    def limit_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        limit_price: float,
        timeout: float = 60.0
    ) -> Dict:
        """Real limit order on Gate.io testnet with timeout."""
        try:
            ccxt_symbol = symbol.replace('_', '/')
            order = self.exchange.create_limit_order(ccxt_symbol, side, qty, limit_price)
            order_id = order['id']

            start_time = time.time()
            while time.time() - start_time < timeout:
                order = self.exchange.fetch_order(order_id, ccxt_symbol)
                status = order.get('status', '')

                if status == 'closed':
                    filled_qty = float(order.get('filled', qty))
                    avg_price = float(order.get('average', limit_price))
                    fee_info = order.get('fee', {})
                    fee = float(fee_info.get('cost', 0))
                    is_maker = order.get('maker', True)

                    store.record_trade(
                        self.bot_name,
                        symbol,
                        side,
                        filled_qty,
                        avg_price,
                        fee=fee,
                        is_maker=is_maker
                    )

                    return {
                        "status": "filled",
                        "filled_qty": filled_qty,
                        "avg_price": avg_price,
                        "symbol": symbol,
                        "side": side,
                        "is_maker": is_maker,
                        "fee": fee,
                        "fee_rate": fee / (filled_qty * avg_price) if filled_qty * avg_price > 0 else 0
                    }

                elif status in ['canceled', 'expired']:
                    return {"status": "cancelled", "filled_qty": 0}

                time.sleep(2)

            try:
                self.exchange.cancel_order(order_id, ccxt_symbol)
            except:
                pass

            return {"status": "timeout", "filled_qty": 0}

        except Exception as e:
            print(f"Gate.io testnet limit order failed: {e}")
            return PaperExec(self.bot_name).limit_order(symbol, side, qty, limit_price, timeout)
