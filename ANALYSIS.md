# Trading Web App - Comprehensive Codebase Analysis

## Executive Summary
This is a hierarchical, multi-strategy trading bot system running on Flask with 3 strategy managers (mean reversion, breakout, trend following), each managing 3x3=9 parameter variants per symbol (3 symbols = 27 bots per strategy, 81 total bots). The system appears designed for paper trading with frequent rebalancing, but has critical flaws causing excessive small trades.

---

## 1. ARCHITECTURE OVERVIEW

### Hierarchical Structure
```
PortfolioManager (root, manages total equity)
├── StrategyManager: mean_reversion
│   ├── TradingBot (mr_btc_usdt_1m_p1, mr_btc_usdt_1m_p2, mr_btc_usdt_1m_p3)
│   ├── TradingBot (mr_eth_usdt_1m_p1, mr_eth_usdt_1m_p2, mr_eth_usdt_1m_p3)
│   └── TradingBot (mr_sol_usdt_1m_p1, mr_sol_usdt_1m_p2, mr_sol_usdt_1m_p3)
├── StrategyManager: breakout
│   └── [9 bots with different lookback parameters]
└── StrategyManager: trend_follow
    └── [9 bots with different fast/slow MA parameters]
```

### Execution Flow
1. **Main Loop** (`app/__init__.py`): Runs every 60 seconds (or per timeframe)
   - Calls `_pm.step()` on PortfolioManager
   - Syncs with bar boundaries (1m timeframe default)
   - Auto-refreshes parameters every 30 minutes

2. **PortfolioManager.step()**:
   - Calls each StrategyManager.step()
   - Rebalances across strategies based on average scores

3. **StrategyManager.step()**:
   - Ensures bots exist in DB
   - Calls each bot's step()
   - **Rebalances allocations within strategy based on bot scores**
   - Updates DB

4. **TradingBot.step()**:
   - Fetches last 200 bars
   - Gets strategy signal (exposure -1.0 to +1.0)
   - Computes target quantity based on equity and signal
   - **Executes if delta >= $10 notional**
   - Updates cash/position metrics
   - Updates performance score (EMA of returns)

### Component Details

**TradingBot** (app/bots.py):
- Single symbol + timeframe + strategy + allocation
- Default allocation: $1,000 per bot (27,000 total per strategy manager)
- Risk per trade: 1% (unused in current logic)
- Metrics: cash, pos_qty, avg_price, equity, score, trades

**StrategyManager** (app/managers.py):
- Manages ~9 bots per strategy
- min_alloc_frac: 0.05 (minimum 5% of strategy AUM per bot)
- max_alloc_frac: 0.70 (maximum 70% per bot)
- Rebalances based on bot score (clamps, normalizes, applies)

**PortfolioManager** (app/managers.py):
- Manages 3 strategy managers
- min_alloc_frac: 0.10
- max_alloc_frac: 0.60
- Rebalances portfolio across strategies

---

## 2. TRADING STRATEGIES IMPLEMENTED

### A. Mean Reversion (MeanReversion class)
**File**: `app/strategies/__init__.py` (lines 35-57)

**Logic**:
- Computes simple moving average (SMA) over lookback period
- Calculates crude standard deviation (mean absolute deviation)
- **Signal**:
  - Returns +1.0 if price < MA - (band × stdev) [oversold → BUY]
  - Returns -1.0 if price > MA + (band × stdev) [overbought → SELL]
  - Returns 0.0 otherwise [neutral]

**Parameter Grid (MR_GRID)**:
```python
[
    {"lookback": 20, "band": 2.0},
    {"lookback": 50, "band": 2.0},
    {"lookback": 100, "band": 2.5},
]
```

**Issues**:
- Very loose bands (2.0-2.5σ) → only triggers on extreme moves
- However, on 1-minute bars, mean reversion can trigger frequently

### B. Breakout (Breakout class)
**File**: `app/strategies/__init__.py` (lines 60-80)

**Logic**:
- Tracks highest high and lowest low over lookback period
- **Signal**:
  - Returns +1.0 if close >= max(highs) [breakout UP]
  - Returns -1.0 if close <= min(lows) [breakout DOWN]
  - Returns 0.0 otherwise

**Parameter Grid (BO_GRID)**:
```python
[
    {"lookback": 20},
    {"lookback": 60},
    {"lookback": 120},
]
```

**Issues**:
- On 1-minute bars with lookback=20, triggers very frequently
- Each new 1-minute bar can easily break a 20-bar high/low

### C. Trend Following (TrendFollow class)
**File**: `app/strategies/__init__.py` (lines 83-103)

**Logic**:
- Compares fast SMA vs slow SMA
- **Signal**:
  - Returns +1.0 if fast_ma > slow_ma [uptrend]
  - Returns -1.0 if fast_ma < slow_ma [downtrend]
  - Returns 0.0 if equal

**Parameter Grid (TF_GRID)**:
```python
[
    {"fast": 10, "slow": 50},
    {"fast": 20, "slow": 100},
    {"fast": 50, "slow": 200},
]
```

---

## 3. FUND ALLOCATION SYSTEM

### Initial State
- Each bot starts with $1,000 allocation
- Each StrategyManager manages 9 bots × $1,000 = $9,000
- PortfolioManager total: 3 strategies × $9,000 = $27,000

### Dynamic Rebalancing

**Within Strategy** (StrategyManager._rebalance_within_strategy):
```python
1. Compute score for each bot (clamped [-0.2, 0.2], EMA of returns)
2. Normalize scores → allocation fractions
3. Clamp fractions: min=5%, max=70%
4. Re-normalize to sum=1.0
5. Apply to strategy equity: allocation = strat_equity × fraction
```

**Across Strategies** (PortfolioManager._rebalance_across_strategies):
```python
1. Average bot scores per strategy
2. Allocate portfolio equity based on strategy scores
3. Push targets down to bots proportionally
```

### Critical Issue #1: Frequent Rebalancing
- **Every step()** (every 60 seconds), all allocations are recomputed
- Each bot's allocation changes continuously
- This forces position size recalculations **on every bar**
- Combined with low minimum trade size → many small trades

---

## 4. TRADING EXECUTION LOGIC

### Position Sizing

**Current Logic** (TradingBot.step):
```
1. Get strategy signal (target_exp in [-1, 1])
2. Compute equity: cash + position_qty × current_price
3. Compute target notional: equity × target_exp
4. Compute target quantity: target_notional / price
5. Calculate delta: target_qty - current_qty
6. Trade if abs(delta) × price >= $10 minimum notional
```

**Example**:
- Bot allocation: $1,000
- Current price BTC: $45,000
- Current position: 0 coins
- Strategy signal: +1.0 (fully long)
- Target notional: $1,000 × 1.0 = $1,000
- Target qty: $1,000 / $45,000 = 0.0222 BTC
- Delta: 0.0222 BTC
- Trade notional: 0.0222 × $45,000 = $1,000 ✓ (executes)

### Critical Issue #2: Minimum Trade Threshold is Too Low
- **Minimum notional**: $10 (line 69 in bots.py)
- This is extremely permissive
- Example: If allocation changes by $20 due to rebalancing:
  - $20 / $45,000 = 0.00044 BTC
  - Notional = $20 (barely above minimum)
  - **Executes trade for $20 → unnecessary overhead**

### Critical Issue #3: Allocation Changes Drive Small Trades
- Rebalancing happens **every step()**
- Each bot's allocation changes based on:
  - Other bots' scores in same strategy
  - Strategy's scores relative to others
- Even 1% allocation change on $1,000 = $10 ± a few dollars
- With $10 minimum, this creates borderline trade cases

### Trade Execution
```python
self.exec.paper_order(symbol, side, qty, price_hint=price)
# Updates storage: store.record_trade()
# Updates local metrics: cash, pos_qty, trades counter
```

---

## 5. CONFIGURATION PARAMETERS

### Timeframe & Data
```python
TF = "1m"  # 1-minute bars
SYMBOLS = ["BTC_USDT", "ETH_USDT", "SOL_USDT"]
BASE_CURRENCY = "USDT"
TESTNET = True  # Default
```

### Loop Timing
```python
SEC = 60  # For 1m timeframe, runs every 60 seconds
SLEEP = max(2.0, next_bar - now + 2)  # +2s buffer
```

### Auto Parameter Refresh
```python
lookback_bars: 1000
top_k: 2  # Keep top 2 performing parameter sets
refresh_seconds: 1800  # 30 minutes
```

### Position Sizing Defaults
```python
allocation_per_bot: 1000.0
risk_per_trade: 0.01  # (UNUSED - not enforced)
min_notional: 10.0  # CRITICAL: Too low!
```

### Allocation Constraints (StrategyManager)
```python
min_alloc_frac: 0.05  # Min 5% per bot
max_alloc_frac: 0.70  # Max 70% per bot
```

### Allocation Constraints (PortfolioManager)
```python
min_alloc_frac: 0.10  # Min 10% per strategy
max_alloc_frac: 0.60  # Max 60% per strategy
```

---

## 6. KEY FINDINGS: ROOT CAUSES OF EXCESSIVE SMALL TRADES

### Issue #1: Too Many Bots with Constant Rebalancing
- **81 total bots** (27 per strategy)
- **Every 60 seconds**, ALL allocations are recomputed
- Allocation changes cascade down from PortfolioManager → StrategyManager → bot level
- Result: **Hundreds of potential trade signals per minute**

### Issue #2: Minimum Trade Threshold ($10) is Too Low
- At $10 minimum, a bot trading at $1,000 allocation with 1-2% swings triggers trades
- Small allocation changes (due to rebalancing) create borderline trade cases
- **Suggestion**: Raise to $50 or $100 minimum

### Issue #3: Allocation Rebalancing Creates Noise
- Scores use EMA with alpha=0.1 (slow moving)
- But allocations change every step anyway
- A bot with score +0.05 gets ~5% more allocation than one with +0.02
- This causes constant position size adjustments

### Issue #4: 1-Minute Timeframe is Too Fast
- Strategy parameters designed for 5m/1h (e.g., lookback=50 means 50 bars)
- On 1m: 50-bar lookback = 50 minutes of history
- Breakout (lookback=20) = 20 minute window → **very frequent breakouts**
- Mean reversion bands might appear loose, but on 1m bars price swings rapidly

### Issue #5: Strategies Have No Hysteresis or Confirmation
- Each strategy signal is binary: +1, 0, or -1 (no gradual exposure)
- No confirmation candles or hysteresis
- Mean reversion can flip +1→0 or 0→-1 every bar if price oscillates near MA
- Each flip causes position rebalancing

### Issue #6: No Trade Frequency Limits
- No cooldown after a trade
- No "do not trade if just traded N seconds ago"
- Same bot can buy and sell multiple times per minute if signals flap

### Issue #7: Score Computation is Noisy on Short Timeframes
- Score = EMA(0.9 × score + 0.1 × return)
- On 1m bars, return = (equity_now - equity_last) / equity_last
- Tiny price moves (0.01%) cause score updates → allocation changes

---

## 7. TRADE FREQUENCY ANALYSIS

### Expected Trade Frequency
With 81 bots running every 60 seconds:
- **Best case**: Only major signal changes → ~10-20 trades/day per bot
- **Actual case**: Allocation rebalancing + signal flapping → **50-100+ trades/day per bot**
- **Total portfolio**: 81 bots × 50-100 trades = **4,000-8,000 trades/day**

### Why So Many?
1. **Allocation rebalancing**: Every bot's position target changes every minute
2. **Signal noise**: On 1m bars, strategies generate spurious signals
3. **Low minimum trade**: $10 threshold allows micro-trades
4. **No confirmation**: Signals can flip without confirmation
5. **Score churn**: EMA score bounces around, causing allocation shifts

---

## 8. CODE FLOW FOR A SINGLE TRADE

### Example: BTC Mean Reversion Bot (1-minute update)
```
Time: 12:34:00
├─ PortfolioManager.step()
│  ├─ StrategyManager(mean_reversion).step()
│  │  ├─ TradingBot(mr_btc_usdt_1m_p1).step()
│  │  │  ├─ Fetch 200 1m bars
│  │  │  ├─ Last bar close = $45,050
│  │  │  ├─ MeanReversion signal = -1.0 (overbought)
│  │  │  ├─ Current allocation = $950 (down from $1,000 last step)
│  │  │  ├─ Equity = $950 + (0.02 coins × $45,050) = $1,850
│  │  │  ├─ Target notional = $1,850 × (-1.0) = -$1,850 (fully short)
│  │  │  ├─ Target qty = -$1,850 / $45,050 = -0.041 BTC
│  │  │  ├─ Current qty = +0.02 BTC (from previous buy)
│  │  │  ├─ Delta = -0.041 - 0.02 = -0.061 BTC (SELL)
│  │  │  ├─ Trade notional = 0.061 × $45,050 = $2,748 ✓ Exceeds $10 minimum
│  │  │  ├─ Execute: SELL 0.061 BTC @ $45,050
│  │  │  ├─ Update: cash += $2,748, pos_qty = -0.041 BTC
│  │  │  ├─ metrics.trades += 1
│  │  │  └─ Compute score (equity vs allocation)
│  │  │
│  │  ├─ [Repeat for 8 other MR bots]
│  │  └─ _rebalance_within_strategy()
│  │     └─ Update each bot's allocation based on scores
│  │
│  ├─ [Repeat for breakout and trend_follow strategies]
│  └─ _rebalance_across_strategies()
│     └─ Update strategy allocations based on strategy scores
│
└─ Next step in 60 seconds
```

---

## 9. DATA FLOW & STORAGE

### Trade Recording
- Every executed trade → stored in SQLite `trades` table
- Columns: id, ts, bot_name, symbol, side, qty, price
- No fee deduction in simulation
- Paper trading only (no actual orders)

### Bot State Persistence
- Every step(), bot state upserted to `bots` table
- Columns: name, manager, symbol, tf, strategy, params_json, allocation, cash, pos_qty, avg_price, equity, score, trades
- DB is single source of truth for state recovery
- Survives app restarts

### Round-Trip Calculation
- Computes closed positions via FIFO lot matching
- Tracks entry/exit price, PnL, duration
- Useful for performance analysis

---

## 10. PERFORMANCE METRICS & SCORING

### Bot Metrics (BotMetrics)
```python
equity = cash + (pos_qty × current_price)  # Mark-to-market
score = (1-alpha) × prev_score + alpha × return
  where return = (equity - allocation) / allocation
  clamped to [-0.2, 0.2]
  alpha = 0.1 (EMA weight on new return)
```

### Issues with Scoring
- Returns computed every bar (even if no trade)
- Small 1-minute price moves cause score swings
- Score clamped to ±20% (caps positive feedback)
- Low alpha (0.1) means slow smoothing
  - Takes ~23 bars to reach 90% of new return value
  - On 1m bars = ~23 minutes
  - But allocations change every minute anyway

---

## 11. AUTO PARAMETER REFRESH (auto_params.py)

### How It Works
- Every 30 minutes, system evaluates parameter candidates
- Scores using backtest_exposure() on last 1,000 bars
- Keeps top 2 performing parameter sets
- Mutates remaining slots (±10-20% parameter changes)
- Rebuilds bots with new strategies

### Impact on Trading
- Parameter changes don't immediately affect live signals
- New strategy objects restart their internal state (deques)
- May cause signal delays for 1-2 bars until enough history

---

## 12. API & UI ENDPOINTS

### Data Endpoints
- `/trades.json`: Recent trades (filterable by bot/symbol/manager)
- `/roundtrips.json`: Closed position cycles
- `/positions.json`: Open positions with unrealized PnL
- `/portfolio.json`: Snapshot of strategy equity and bot performance
- `/prices.json`: Current prices for all symbols

### UI
- Single-page HTML dashboard (Bootstrap 5)
- Auto-refreshes every 5 seconds
- Shows: portfolio equity, trades, positions, roundtrips per strategy

---

## 13. CRITICAL VULNERABILITIES & ISSUES SUMMARY

| Issue | Severity | Impact | Root Cause |
|-------|----------|--------|-----------|
| **Excessive Small Trades** | CRITICAL | Thousands/day | Low minimum ($10), frequent rebalancing |
| **Allocation Churn** | HIGH | Constant position adjustments | Every-step rebalancing with loose constraints |
| **Signal Noise (1m TF)** | HIGH | Spurious entries/exits | Strategies too fast, no confirmation |
| **No Cooldown** | MEDIUM | Multiple trades same bar | No trade frequency limit |
| **Loose Band Parameters** | MEDIUM | Frequent MR signals | Parameters optimized for different TF |
| **Score-Driven Rebalancing** | MEDIUM | Cascading effects | Small score changes → allocation changes |
| **No Risk Management** | MEDIUM | Potential for large drawdowns | risk_per_trade unused, no stops |
| **Noisy Score Computation** | LOW | Allocation instability | EMA alpha too high for 1m bars |

---

## 14. RECOMMENDED FIXES

### Immediate (High Priority)
1. **Raise minimum trade notional** from $10 to $100-$500
   - File: `app/bots.py`, line 69
   - Filters out micro-trades from rebalancing noise

2. **Reduce rebalancing frequency** from every step to every N steps
   - File: `app/managers.py`, modify step() to skip rebalance some times
   - Only rebalance every 5-10 minutes, not 60 seconds

3. **Add trade cooldown** per bot
   - Track last_trade_ts per bot
   - Skip trade if < 300 seconds (5 minutes) since last trade
   - File: `app/bots.py`

4. **Add hysteresis/confirmation** to strategies
   - Don't switch to new signal immediately
   - Require N consecutive bars (N=2-3) at new signal
   - File: `app/strategies/__init__.py`

### Medium Priority
5. **Switch to longer timeframe** (5m or 15m instead of 1m)
   - Reduces signal noise by ~5x
   - Parameters already tuned for 5m/1h

6. **Reduce bot count** from 81 to 27 (1 per symbol/strategy)
   - Fewer rebalancing targets
   - Simpler system to debug

7. **Increase min/max allocation constraints**
   - Prevent extreme concentration
   - Reduce allocation swings

8. **Implement position size limits**
   - Cap max position as % of equity
   - Prevent all-in scenarios

---

## 15. FILE STRUCTURE SUMMARY

```
/home/user/tradintel/
├── app/
│   ├── __init__.py          # Flask app, main loop, endpoints
│   ├── bots.py              # TradingBot class (CRITICAL: min_notional=$10)
│   ├── managers.py          # StrategyManager, PortfolioManager (allocation logic)
│   ├── core.py              # Protocol definitions (Bar, Strategy, etc)
│   ├── strategies/
│   │   ├── __init__.py      # MeanReversion, Breakout, TrendFollow classes + grids
│   │   ├── mean_reversion.py # (empty)
│   │   ├── breakout.py       # (empty)
│   │   └── trend_follow.py   # (empty)
│   ├── execution.py         # PaperExec (paper trading simulation)
│   ├── data.py              # GateAdapter (Gate.io API client)
│   ├── portfolio.py         # build_portfolio() function (initializes all bots)
│   ├── storage.py           # Storage class (SQLite persistence)
│   ├── auto_params.py       # AutoParamSelector (parameter optimization)
│   └── templates/
│       └── portfolio.html   # UI dashboard
├── config.py                # Environment variables (TESTNET, SYMBOLS)
├── run.py                   # Flask entry point
├── requirements.txt         # Flask, requests
├── tests/
│   └── test_portfolio.py    # Basic build & step test
└── trading.db               # SQLite database (trades, bots, state)
```

---

## 16. CONCLUSION

This trading system is experiencing excessive small trades primarily due to:

1. **Too-low minimum trade threshold** ($10 is negligible)
2. **Excessive rebalancing frequency** (every 60 seconds)
3. **Too many bots** (81 total = 27 trade signals × 3 strategies)
4. **1-minute timeframe with tight bands** (strategies oscillate)
5. **No signal confirmation or hysteresis** (buy/sell every bar)
6. **No trade cooldown/frequency limits**

The fixes are straightforward:
- Raise minimum notional to $100-$500
- Rebalance every 5-10 minutes, not every 60 seconds
- Add N-bar confirmation to strategies
- Add per-bot cooldown after trades
- Consider switching to 5m timeframe

Combined, these changes could reduce trade frequency by **80-90%** while maintaining strategy diversity.

