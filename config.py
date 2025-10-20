
import os

# Use env vars; default to testnet public endpoints
TESTNET = os.getenv("TESTNET", "1") == "1"

BASE_URL = "https://api-testnet.gateapi.io/api/v4" if TESTNET else "https://api.gateio.ws/api/v4"

BASE_CURRENCY = os.getenv("BASE_CURRENCY","USDT")
TRADE_SYMBOLS = os.getenv("TRADE_SYMBOLS","BTC_USDT,ETH_USDT").split(",")
