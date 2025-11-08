# Critical Code Snippets & Issues

## ISSUE #1: Minimum Trade Threshold Too Low

**File**: `app/bots.py` (lines 69-74)

```python
min_notional = 10.0  # <-- PROBLEM: $10 is too permissive!
if abs(delta) * price < min_notional:
    # still update equity mark-to-market, but skip order
    self.metrics.avg_price = price
    self.metrics.equity = self.metrics.cash + self.metrics.pos_qty * price
    return
```

**Why it's a problem**:
- $10 minimum on a $1,000 allocation bot = 1% swing tolerance
- Allocation changes from rebalancing often 1-2%, triggering micro-trades
- At $45,000 BTC, $10 = 0.00022 BTC (barely above dust)
- Creates hundreds of worthless trades per day

**Fix**:
```python
min_notional = 100.0  # Raise to $100-$500 minimum
```

---

## ISSUE #2: Every-Step Rebalancing Creates Constant Adjustments

**File**: `app/managers.py` (lines 59-71) - StrategyManager._rebalance_within_strategy()

```python
def _rebalance_within_strategy(self) -> None:
    scores = [max(0.0, b.metrics.score) for b in self.bots]
    total = sum(scores) or 1.0
    fracs = [s / total for s in scores]
    # clamp
    fracs = [min(self.max_alloc_frac, max(self.min_alloc_frac, f)) for f in fracs]
    # renormalize
    s = sum(fracs)
    fracs = [f / s for f in fracs]
    # apply new target allocations proportionally to current strategy AUM
    strat_equity = sum(b.metrics.equity for b in self.bots)
    for b, f in zip(self.bots, fracs):
        b.allocation = strat_equity * f  # <-- Changes every 60 seconds!
```

**Why it's a problem**:
- Called **every step()** (every 60 seconds)
- Each bot's allocation constantly recalculated
- Even tiny score changes (from minute price moves) change allocations
- Results in different target position size → requires trades to rebalance
- Cascades to PortfolioManager rebalancing too

**Example**:
```
Step 1: Bot A score = +0.05, allocation = $2,500
Step 2: Bot A score = +0.04 (down $50 from 1-minute loss), allocation = $2,400
→ Needs to sell $100 worth to hit new target
→ At $45k BTC = 0.0022 BTC sold (unnecessary!)
```

**Fix**:
```python
# Only rebalance every N steps, e.g., every 5 minutes
def step(self, rebalance_every_n_steps: int = 5) -> None:
    # ... update bots ...
    if self._step_counter % rebalance_every_n_steps == 0:
        self._rebalance_within_strategy()
    self._step_counter += 1
```

---

## ISSUE #3: No Trade Cooldown - Can Flap Multiple Times Per Minute

**File**: `app/bots.py` (lines 47-107) - TradingBot.step()

```python
def step(self) -> None:
    bars: List[Bar] = self.data.history(self.symbol, self.tf, limit=200)
    if not bars:
        return
    last = bars[-1]
    # Only act once per new bar
    if self._last_bar_ts == last.ts:
        return  # <-- Only prevents trades on SAME bar timestamp
    self._last_bar_ts = last.ts
    
    # ... compute delta ...
    
    if abs(delta) * price < min_notional:
        return
    
    if abs(delta) > 1e-9:
        side = "buy" if delta > 0 else "sell"
        # ... execute immediately ...
        self.exec.paper_order(self.symbol, side, trade_qty, price_hint=price)
```

**Why it's a problem**:
- Only guard is `_last_bar_ts` - prevents trades on same bar
- But on 1m bars, each minute has 60 seconds of possible updates
- No cooldown = buy/sell/buy sequence within minutes
- Mean reversion can oscillate around MA, triggering buy-sell-buy
- No frequency limit at all

**Example**:
```
12:34:00 - MR signal: +1.0 (BUY) - executes
12:34:30 - Price dips, still +1.0 (already long)
12:35:00 - New bar! Price above MA now, signal: -1.0 (SELL) - executes
12:36:00 - New bar! Price dips below MA, signal: +1.0 (BUY) - executes
→ 3 trades in 2 minutes!
```

**Fix**:
```python
def __init__(self, ..., min_trade_interval_seconds: int = 300):
    self._last_trade_ts: int | None = None
    self.min_trade_interval = min_trade_interval_seconds

def step(self) -> None:
    # ... existing logic ...
    
    now = int(time.time())
    if self._last_trade_ts and (now - self._last_trade_ts) < self.min_trade_interval:
        return  # Skip trade if cooldown active
    
    # ... only execute trades if cooldown expired ...
    if abs(delta) > 1e-9 and (abs(delta) * price >= min_notional):
        # ... execute ...
        self._last_trade_ts = now
```

---

## ISSUE #4: Signal Flapping - No Confirmation Needed

**File**: `app/strategies/__init__.py` (lines 35-54) - MeanReversion.on_bar()

```python
class MeanReversion(Strategy):
    def on_bar(self, bars: Iterable[Bar]) -> float:
        for b in bars:
            self._closes.append(b.close)
        if len(self._closes) < self.lookback:
            return 0.0
        ma = _sma(self._closes, self.lookback)
        dev = (_sma([abs(c - ma) for c in self._closes], self.lookback) or 1.0)
        last = self._closes[-1]
        if last < ma - self.band * dev:
            return +1.0  # <-- Immediate signal, no confirmation
        if last > ma + self.band * dev:
            return -1.0  # <-- Immediate signal, no confirmation
        return 0.0
```

**Why it's a problem**:
- Signal returns instantly on first bar crossing threshold
- No confirmation candles required
- Price oscillates near MA on 1m bars → constant flipping
- Example with lookback=20, band=2.0:
  ```
  12:34:00 bar close: 45,000 (MA=45,020, dev=10)
            threshold = 45,020 - 2.0*10 = 45,000
            SIGNAL: +1.0 BUY
  12:35:00 bar close: 45,015 (crosses above threshold)
            SIGNAL: 0.0 NEUTRAL (position unwind)
  12:36:00 bar close: 45,005 (crosses below threshold)
            SIGNAL: +1.0 BUY (re-entry!)
  → 3 signals = 2-3 trades in 2 minutes
  ```

**Fix**:
```python
class MeanReversionWithConfirmation(Strategy):
    def __init__(self, lookback: int = 20, band: float = 2.0, 
                 confirm_bars: int = 2):
        self.lookback = lookback
        self.band = band
        self.confirm_bars = confirm_bars
        self._closes: Deque[float] = deque(maxlen=max(lookback, 50))
        self._signal_bars = 0  # bars at current signal

    def on_bar(self, bars: Iterable[Bar]) -> float:
        for b in bars:
            self._closes.append(b.close)
        if len(self._closes) < self.lookback:
            return 0.0
        
        ma = _sma(self._closes, self.lookback)
        dev = (_sma([abs(c - ma) for c in self._closes], self.lookback) or 1.0)
        last = self._closes[-1]
        
        current_signal = 0.0
        if last < ma - self.band * dev:
            current_signal = +1.0
        elif last > ma + self.band * dev:
            current_signal = -1.0
        
        # Track consecutive bars at signal
        if current_signal == self._last_signal:
            self._signal_bars += 1
        else:
            self._signal_bars = 1
            self._last_signal = current_signal
        
        # Only return signal if confirmed for N bars
        if self._signal_bars >= self.confirm_bars:
            return current_signal
        return 0.0
```

---

## ISSUE #5: Score Computation Noisy on 1m Bars

**File**: `app/bots.py` (lines 102-106)

```python
# 5) Score (EMA of return) with clamp for UI readability
ret = (self.metrics.equity - self.allocation) / max(1e-9, self.allocation)
alpha = 0.1
self.metrics.score = (1 - alpha) * self.metrics.score + alpha * ret
self.metrics.score = max(-0.2, min(0.2, self.metrics.score))
```

**Why it's a problem**:
- `ret` = (current equity - allocation) / allocation
- On 1m bars, any small price move changes equity
- Example: $1,000 allocation, BTC up $10 = +1% return
- Score jumps from 0.0 → 0.10 (after clamping)
- This changes allocation fractions in rebalancing
- Results in position adjustments every minute

**Example with 3 bots**:
```
Time 12:34:00:
  Bot A: equity=$1,050, score = +0.10 (1% gain)
  Bot B: equity=$1,010, score = +0.01 (tiny gain)
  Bot C: equity=$1,000, score = 0.00 (flat)
  Total: $3,060 equity
  Fracs after clamp/norm: 0.35, 0.35, 0.30
  Allocations: $1,071, $1,071, $918

Time 12:35:00: (prices moved 0.1%)
  Bot A: equity=$1,051, score = 0.10×0.9 + 0.01×0.1 = 0.091
  Bot B: equity=$1,011, score = 0.01×0.9 + 0.001×0.1 = 0.009
  Bot C: equity=$1,000, score = 0.0
  Allocations now computed differently!
  → All 3 bots need position adjustments
```

**Fix**:
```python
# Increase alpha to dampen score noise
alpha = 0.05  # Changed from 0.1
# Or smooth the return signal first
from collections import deque
self._recent_rets = deque(maxlen=10)  # Keep 10 bars of returns
self._recent_rets.append(ret)
smoothed_ret = sum(self._recent_rets) / len(self._recent_rets)
self.metrics.score = (1 - alpha) * self.metrics.score + alpha * smoothed_ret
```

---

## ISSUE #6: Breakout Strategy Too Sensitive on 1m Bars

**File**: `app/strategies/__init__.py` (lines 60-80)

```python
class Breakout(Strategy):
    def __init__(self, lookback: int = 50):
        self.lookback = lookback
        self._highs: Deque[float] = deque(maxlen=lookback)
        self._lows: Deque[float] = deque(maxlen=lookback)

    def on_bar(self, bars: Iterable[Bar]) -> float:
        for b in bars:
            self._highs.append(b.high)
            self._lows.append(b.low)
        if len(self._highs) < self.lookback:
            return 0.0
        last = bars[-1].close if hasattr(bars, "__getitem__") else list(bars)[-1].close
        if last >= max(self._highs):  # <-- Triggers on ANY new 1m high
            return +1.0
        if last <= min(self._lows):   # <-- Triggers on ANY new 1m low
            return -1.0
        return 0.0
```

**Why it's a problem**:
- lookback=20 on 1m bars = 20 minutes of history
- On volatile assets like crypto, 1m bars VERY frequently hit new highs/lows
- Example with BTC at 45,000:
  - $1 move per minute = $60/hour = 0.13% movement
  - Over 20 bars (20 min), very likely to hit new high/low
  - Could signal breakout 5+ times per hour!

**Example**:
```
Time: 12:30-12:34 (5 bars)
- Previous 20-bar high: 45,050

12:34:00 bar: high=45,051 → NEW HIGH! Signal: +1.0
12:35:00 bar: high=45,052 → NEW HIGH! Signal: +1.0 (already bought)
12:36:00 bar: high=45,050, close=45,025 → BELOW HIGH, signal: 0.0
→ Sell signal → trade
12:37:00 bar: high=45,055 → NEW HIGH! Signal: +1.0
→ Buy signal → trade
→ 3 trades in 4 minutes!
```

**Fix**:
```python
class BreakoutWithConfirmation(Strategy):
    def __init__(self, lookback: int = 50, confirm_bars: int = 2):
        self.lookback = lookback
        self.confirm_bars = confirm_bars
        self._highs: Deque[float] = deque(maxlen=lookback)
        self._lows: Deque[float] = deque(maxlen=lookback)
        self._signal_bars = 0
        self._current_signal = 0.0

    def on_bar(self, bars: Iterable[Bar]) -> float:
        for b in bars:
            self._highs.append(b.high)
            self._lows.append(b.low)
        if len(self._highs) < self.lookback:
            return 0.0
        last = ... # (get last close)
        
        current = 0.0
        if last >= max(self._highs):
            current = +1.0
        elif last <= min(self._lows):
            current = -1.0
        
        if current == self._current_signal:
            self._signal_bars += 1
        else:
            self._signal_bars = 1
            self._current_signal = current
        
        if self._signal_bars >= self.confirm_bars:
            return current
        return 0.0
```

---

## ISSUE #7: No Position Size Limits

**File**: `app/bots.py` (lines 61-64)

```python
# 2) Target *notional* position and qty with NO leverage
equity_now = self.metrics.cash + self.metrics.pos_qty * price
target_notional = equity_now * target_exp
target_qty = target_notional / max(1e-9, price)
```

**Why it's a problem**:
- If target_exp = 1.0 (fully long), allocates 100% of equity to position
- No leverage cap, no position size limits
- One bad trade can wipe out entire allocation
- No risk-per-trade enforcement (risk_per_trade=0.01 is ignored!)

**Example**:
```
Allocation: $1,000
Current price: $45,000
Signal: +1.0 (fully long)
Target position size: $1,000 / $45,000 = 0.0222 BTC
→ All-in position, 100% of capital exposed
→ If price drops 10%, equity = $900 (10% drawdown)
→ If price drops 50%, equity = $500 (50% drawdown!)
```

**Fix**:
```python
def step(self) -> None:
    # ... existing logic ...
    
    # NEW: Cap position size
    max_position_pct = 0.50  # Max 50% of allocation per position
    capped_notional = equity_now * target_exp
    max_notional = self.allocation * max_position_pct
    if abs(capped_notional) > max_notional:
        capped_notional = max_notional if capped_notional > 0 else -max_notional
    
    capped_qty = capped_notional / max(1e-9, price)
    delta = capped_qty - self.metrics.pos_qty
```

---

## Summary: Root Cause Chain

```
81 bots × Every 60 seconds → Step all managers
        ↓
Every manager rebalances → Allocation changes
        ↓
Allocation changes → New target position size
        ↓
New target size → Trade delta computed
        ↓
$10 minimum too low → 1% allocation change = $10 trade (executes!)
        ↓
Strategies have no confirmation → Signal flaps → More trades
        ↓
No cooldown → Buy/sell/buy rapid cycles
        ↓
Result: Thousands of small, pointless trades per day
```

---

## Implementation Priority

1. **IMMEDIATE** (1-hour fix):
   - Raise min_notional from $10 → $100
   - Add trade cooldown (5 minute minimum)

2. **URGENT** (Today):
   - Reduce rebalancing frequency (every 5 min instead of every 60s)
   - Add signal confirmation (2-3 consecutive bars)

3. **IMPORTANT** (This week):
   - Switch to 5m timeframe
   - Cap position sizes

4. **NICE-TO-HAVE** (Longer term):
   - Reduce bot count (27 instead of 81)
   - Implement proper risk management
