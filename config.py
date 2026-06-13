"""Configuration reference for tradintel.

NOTE: This module is intentionally NOT imported by the app. Its previous contents
(Gate.io testnet REST endpoints + a TESTNET flag) were dead and misleading — nothing
ever imported them, and they implied the app trades on Gate.io, which it does not.
Real configuration lives in the three places documented below.

────────────────────────────────────────────────────────────────────────────
1. Environment variables (.env, loaded by run.py via python-dotenv)
────────────────────────────────────────────────────────────────────────────
   BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET
                          -> order execution            (app/execution.py)
   AUTH_USERNAME / AUTH_PASSWORD_HASH
                          -> dashboard login            (app/auth.py)
   SECRET_KEY             -> Flask session signing      (app/__init__.py)
   SMTP_SERVER/PORT/USERNAME/PASSWORD/FROM_EMAIL
                          -> price-alert emails         (app/notifications.py)
   BOT_DB                 -> SQLite file path           (app/storage.py)
   DEBUG=1               -> enable Flask debug server   (run.py; OFF by default)

────────────────────────────────────────────────────────────────────────────
2. Database `settings` table (runtime-tunable; see Storage.get_setting / set_setting
   and the /api/config endpoint)
────────────────────────────────────────────────────────────────────────────
   execution_mode        'binance_testnet' (default) | 'paper'
   trading_timeframe     e.g. '1d' — MUST match the timeframe strategies were
                         evolved/optimized on, or performance is invalid
   num_active_strategies  how many evolved strategies to run (default 5)
   min_strategy_score     minimum fitness to include a strategy (default 0.0)
   capital_limit_usdt     cap on capital deployed (default: 90% of balance)
   trading_paused         global kill-switch (defaults to True for safety)
   max_drawdown_pct       risk circuit-breaker threshold (0 = disabled);
                          see app/risk.py

────────────────────────────────────────────────────────────────────────────
3. Hardcoded in code
────────────────────────────────────────────────────────────────────────────
   * Market DATA always comes from Gate.io public candlesticks
     (app/data.py GateAdapter, https://api.gateio.ws) regardless of execution
     mode — note this is the LIVE endpoint, not testnet.
   * EXECUTION (default) goes to Binance testnet
     (app/execution.py BinanceTestnetExec, https://testnet.binance.vision).
     => The data source and the execution venue are DIFFERENT exchanges.
   * Traded symbols: BTC_USDT, ETH_USDT, SOL_USDT (app/portfolio.py SYMBOLS).
"""
