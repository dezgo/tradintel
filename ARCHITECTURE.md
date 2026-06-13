# tradintel — Architecture (current)

> This is the accurate, current description of the system. The older
> `ARCHITECTURE.txt`, `ANALYSIS.md`, and `CODE_ISSUES.md` describe a superseded
> 81-bot / 3-fixed-strategy design and are kept only for history.

## Overview

A single-user Flask web app that runs an auto-evolving, multi-strategy crypto
**paper/testnet** trading system and a price-alert monitor, with a SQLite-backed
dashboard.

```
run.py ──> app.create_app() ──┬─ Flask app + HTTP endpoints (app/__init__.py)
                              ├─ background threads (daemon):
                              │    • _loop          → steps the portfolio
                              │    • _optimizer     → grid optimization
                              │    • _evolver       → genetic evolution
                              │    • _alert         → price-alert monitor
                              └─ _pm = build_portfolio()  (app/portfolio.py)
```

## Trading model

- **Genetic evolution** (`app/genetic_evolution.py`, `app/strategy_genome.py`)
  maintains a population of strategy *genomes* (indicator + entry/exit-rule
  trees), backtests them, and saves the top performers to the DB.
- `build_portfolio()` loads the top *N* evolved strategies (DB setting
  `num_active_strategies`, default 5) into **one** `StrategyManager` named
  `evolved_strategies`, each as a `TradingBot`. (If no evolved strategies exist
  yet, it falls back to the legacy 3-manager grid design.)
- `PortfolioManager.step()` → each `StrategyManager.step()` → each
  `TradingBot.step()`. Bots compute a target exposure from their strategy, size
  a no-leverage position, and place **limit** orders (for maker fees).
- Rebalancing happens every 5 steps, not every step.

## Data vs execution (important)

These are **two different exchanges**:

| Concern   | Source                                                   |
|-----------|----------------------------------------------------------|
| Market data (candles) | **Gate.io** public API (`app/data.py` `GateAdapter`, live endpoint), cached in SQLite (`app/data_cache.py`) |
| Order execution | **Binance testnet** by default (`app/execution.py` `BinanceTestnetExec`), or `paper` |

Note: Binance testnet is geo-blocked from some locations; when the balance fetch
fails the system falls back to $1000/bot.

## Configuration

See `config.py` (a reference doc — not imported). Config lives in:
1. **Env vars** (`.env`): `BINANCE_TESTNET_API_KEY/_SECRET`, `AUTH_USERNAME`,
   `AUTH_PASSWORD_HASH`, `SECRET_KEY`, `SMTP_*`, `BOT_DB`, `DEBUG`.
2. **DB `settings` table** (runtime): `execution_mode`, `trading_timeframe`,
   `num_active_strategies`, `min_strategy_score`, `capital_limit_usdt`,
   `trading_paused`, `max_drawdown_pct`.
3. **Hardcoded**: data source (Gate.io), traded symbols (BTC/ETH/SOL_USDT).

## Persistence

`app/storage.py` — a thread-safe SQLite singleton (`store`), single connection +
lock, WAL mode, versioned migrations. Tables: bots, trades, params, snapshots,
settings, evolved strategies, price alerts, backtests, cached bars. Round-trips
are reconstructed via FIFO lot-matching (`list_roundtrips`); P&L is derived from
those.

## Risk controls

`app/risk.py` — **DrawdownCircuitBreaker**. Opt-in (set DB `max_drawdown_pct` > 0).
Tracks a persisted high-water mark of portfolio equity; if drawdown from the peak
exceeds the threshold it sets `trading_paused = True`. The trading loop calls it
every step. Manage via `GET/POST /api/risk`.

## Auth & security

- Flask-Login session auth; all endpoints require login except `/login`.
- `SECRET_KEY` from env, else a DB-persisted key (stable across restarts).
- Flask debug is off unless `DEBUG=1`.
- A `before_request` hook blocks cross-origin state-changing requests (CSRF).

## Testing

`pytest` (config in `pytest.ini`, collection limited to `tests/`).
- `tests/test_fixes.py` — regression tests for the money/robustness bugs.
- `tests/test_risk.py` — circuit-breaker.
- `tests/test_portfolio.py` — build/step smoke test (skips without creds/network).
- `tests/conftest.py` — points the SQLite singleton at a temp DB.

```
PYTHONIOENCODING=utf-8 python -m pytest -q
```

## Known gaps / next steps

- `app/__init__.py` is ~1900 lines (app factory + loop + every endpoint). Splitting
  into blueprints (`auth`/`portfolio`/`alerts`/`backtest`/`evolution`) is the
  highest-leverage refactor and would make endpoints unit-testable.
- Background threads start on every `create_app()` with no liveness guard →
  duplicate loops possible under the debug reloader or multi-worker gunicorn.
  Run single-worker, or add a guard.
- Dead/misleading fields remain: `risk_per_trade` (unused in sizing),
  `entry_short`/`exit_short` (system is long-only), `cum_pnl` (never written).
- Backtests model 0% commission by default — optimistic; left as-is to avoid
  silently shifting optimizer rankings.
- The legacy auto-rebalance block in the loop maps `mean_reversion`/`breakout`/
  `trend_follow` manager names that don't exist in the evolved single-manager
  design; it's a no-op there (and gated by a setting).
