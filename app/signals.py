# app/signals.py
from dataclasses import dataclass
from typing import List, Optional, Tuple
from datetime import datetime, timezone

@dataclass
class Candle:
    ts: int    # epoch seconds (bar open time)
    open: float
    high: float
    low: float
    close: float
    vol: float

# --- helpers ---
def ema(values: List[float], span: int) -> List[float]:
    if not values:
        return []
    k = 2 / (span + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out

def _ema(arr: List[float], n: int) -> List[float]:
    k = 2 / (n + 1)
    o = [arr[0]]
    for x in arr[1:]:
        o.append(x * k + o[-1] * (1 - k))
    return o

def rsi(values: List[float], length: int = 14) -> List[float]:
    if len(values) < length + 1:
        return [0.0] * len(values)
    gains, losses = [], []
    for i in range(1, len(values)):
        ch = values[i] - values[i-1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    avg_gain = _ema(gains, length)
    avg_loss = _ema(losses, length)
    rs = []
    for g, l in zip(avg_gain[-(len(values)-1):], avg_loss[-(len(values)-1):]):
        rs.append(999.0 if l == 0 else g / l)
    rsi_vals = [0.0]
    rsi_vals += [100.0 - (100.0 / (1.0 + x)) for x in rs]
    return rsi_vals

def atr(candles: List[Candle], length: int = 14) -> List[float]:
    if not candles:
        return []
    trs: List[float] = []
    prev_close = candles[0].close
    for c in candles:
        tr = max(c.high - c.low, abs(c.high - prev_close), abs(c.low - prev_close))
        trs.append(tr)
        prev_close = c.close
    k = 2 / (length + 1)
    out = [trs[0]]
    for t in trs[1:]:
        out.append(t * k + out[-1] * (1 - k))
    return out

# --------------- signals evaluated on CLOSED bars ---------------
# We evaluate using the last two CLOSED bars:
# - current closed bar index = -2
# - previous closed bar index = -3
# The forming bar (-1) is ignored to avoid repeats/repaints.

def signal_pullback_bounce(candles: List[Candle], ema_len=20, atr_len=14) -> Optional[Tuple[str, int]]:
    if len(candles) < ema_len + 3:
        return None
    closes = [c.close for c in candles]
    lows = [c.low for c in candles]
    _ema = ema(closes, ema_len)
    _atr = atr(candles, atr_len)

    # previous and current CLOSED bars
    c_prev_i, c_cur_i = -3, -2
    c_prev_close, c_cur_close = closes[c_prev_i], closes[c_cur_i]
    c_prev_low = lows[c_prev_i]
    ema_prev, ema_cur = _ema[c_prev_i], _ema[c_cur_i]
    atr_prev = _atr[c_prev_i]

    tagged = (c_prev_low <= ema_prev + 0.05 * atr_prev)
    cross_up = (c_prev_close <= ema_prev) and (c_cur_close > ema_cur)
    if tagged and cross_up:
        return (f"Pullback-&-Bounce: close {c_cur_close:.2f} > EMA{ema_len} {ema_cur:.2f}", candles[c_cur_i].ts)
    return None

def signal_breakout(candles: List[Candle], lookback=200, buffer=0.003) -> Optional[Tuple[str, float, int]]:
    if len(candles) < lookback + 2:
        return None
    closes = [c.close for c in candles]
    # use CLOSED current bar (-2)
    prior_high = max(closes[-(lookback+2):-2])  # exclude the last two (forming and closed current)
    last_closed = closes[-2]
    lvl = prior_high * (1 + buffer)
    if last_closed > lvl:
        return (f"Breakout: {last_closed:.2f} > {lookback}-bar high {prior_high:.2f} (+{buffer*100:.1f}%)",
                prior_high, candles[-2].ts)
    return None

def signal_breakout_retest(candles: List[Candle], breakout_level: float, tol=0.005) -> Optional[Tuple[str, int]]:
    # check CLOSED current bar (-2)
    price = candles[-2].close
    lo, hi = breakout_level * (1 - tol), breakout_level * (1 + tol)
    if lo <= price <= hi:
        return (f"Retest near {breakout_level:.2f} (±{tol*100:.1f}%)", candles[-2].ts)
    return None

def signal_overextended(candles: List[Candle], ema_len=20, atr_len=14, rsi_len=14,
                        rsi_thresh=78.0, atr_mult=1.8) -> Optional[Tuple[str, int]]:
    if len(candles) < max(ema_len, atr_len, rsi_len) + 3:
        return None
    closes = [c.close for c in candles]
    _ema = ema(closes, ema_len)
    _atr = atr(candles, atr_len)
    _rsi = rsi(closes, rsi_len)

    last = closes[-2]
    dist = abs(last - _ema[-2])
    if _rsi[-2] >= rsi_thresh and dist >= atr_mult * _atr[-2]:
        side = "above" if last > _ema[-2] else "below"
        return (f"Overextended: RSI {_rsi[-2]:.1f}, distance {dist:.2f} {side} EMA{ema_len} "
                f"is {dist/_atr[-2]:.1f}× ATR → wait for pullback", candles[-2].ts)
    return None


def _fmt_ratio(numerator: float, denominator: float) -> str:
    return f"{(numerator / denominator) if denominator else 0:.2f}×"


def _fmt_ts(ts: int) -> str:
    # UTC string for easy TV matching
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def signal_wick_reversal(
    candles: List[Candle],
    min_body_pct: float = 0.10,
    wick_mult: float = 1.2,
    context_lookback: int = 8,
    min_wick_pct_of_price: float = 0.0,
) -> Optional[Tuple[str, int]]:
    """
    Detect topping/bottoming tail on the LAST CLOSED bar.
    candles is already trimmed to closed bars by the bot.
    Uses local context (lookback) + wick/body filter.
    """
    if len(candles) < context_lookback + 1:
        return None

    # last CLOSED bar
    c = candles[-1]
    rng = c.high - c.low
    if rng <= 0:
        return None

    body = abs(c.close - c.open)
    if body < min_body_pct * rng:
        return None

    upper = c.high - max(c.close, c.open)
    lower = min(c.close, c.open) - c.low

    if min_wick_pct_of_price > 0:
        need = c.close * min_wick_pct_of_price
        if upper < need and lower < need:
            return None

    prev_slice = candles[-(context_lookback + 1):-1]
    prev_highs = [x.high for x in prev_slice]
    prev_lows = [x.low for x in prev_slice]
    at_local_top = c.high >= max(prev_highs)
    at_local_bottom = c.low <= min(prev_lows)

    stamp = _fmt_ts(c.ts)

    if at_local_bottom and lower >= wick_mult * body:
        msg = (
            f"Bottoming tail @ {stamp} | "
            f"O:{c.open:.2f} H:{c.high:.2f} L:{c.low:.2f} C:{c.close:.2f} | "
            f"lower {lower:.2f} ({_fmt_ratio(lower, body)}) ≥ {wick_mult}× body {body:.2f} "
            f"| local low over {context_lookback} bars"
        )
        return (msg, c.ts)

    if at_local_top and upper >= wick_mult * body:
        msg = (
            f"Topping tail @ {stamp} | "
            f"O:{c.open:.2f} H:{c.high:.2f} L:{c.low:.2f} C:{c.close:.2f} | "
            f"upper {upper:.2f} ({_fmt_ratio(upper, body)}) ≥ {wick_mult}× body {body:.2f} "
            f"| local high over {context_lookback} bars"
        )
        return (msg, c.ts)

    return None