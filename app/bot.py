# app/bot.py
import threading
import time
from typing import Dict, List, Tuple

from .sources import get_candles_gate, get_candles_binance
from .signals import (
    signal_pullback_bounce,
    signal_breakout,
    signal_breakout_retest,
    signal_overextended,
    signal_wick_reversal,
    Candle,
)

_TF_SEC = {"15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800}
_CONTEXT_LOOKBACK = 8
_SIGNAL_WINDOW = _CONTEXT_LOOKBACK + 2  # last closed + prior context


def _closed_only(candles: List[Candle], tf: str) -> List[Candle]:
    """
    Keep only CLOSED bars. Works whether ts is OPEN or CLOSE time.
    """
    if not candles:
        return candles

    dur = _TF_SEC.get(tf)
    if not dur or len(candles) < 2:
        return candles[:-1] if len(candles) >= 1 else candles

    now = int(time.time())
    prev_ts = candles[-2].ts
    last_ts = candles[-1].ts
    step = last_ts - prev_ts

    # If step ~ dur, ts is OPEN; bar closes at ts + dur
    if abs(step - dur) <= dur * 0.2:
        return candles[:-1] if now < last_ts + dur else candles

    # If last ts is in the future, it's forming (CLOSE ts ahead)
    if last_ts > now:
        return candles[:-1]

    # Otherwise assume CLOSE ts; still drop within a tiny delay
    return candles[:-1] if now <= last_ts + 2 else candles


DEFAULT_SYMBOLS = ["ETH_USDT"]
DEFAULT_TFS = ["15m", "1h", "1d"]
POLL_SEC = 60


class AlertBot:
    def __init__(self):
        self.symbols = list(DEFAULT_SYMBOLS)
        self.timeframes = list(DEFAULT_TFS)
        self.poll_sec = POLL_SEC
        self.data_source = "gate"  # 'gate' or 'binance'

        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.running = False

        # last_alert[(pair, tf, signal_type)] = bar_ts
        self.last_alert: Dict[Tuple[str, str, str], int] = {}
        self.breakout_levels: Dict[Tuple[str, str], float] = {}
        self.latest_signals: List[Dict] = []

    def start(self):
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.running = True

    def stop(self):
        self._stop.set()
        self.running = False

    def _fetch(self, pair: str, tf: str, limit: int = 400) -> List[Candle]:
        if self.data_source == "binance":
            return get_candles_binance(pair, tf, limit=limit)
        return get_candles_gate(pair, tf, limit=limit)

    def _emit_once(self, pair: str, tf: str, sig_type: str, bar_ts: int, text: str):
        key = (pair, tf, sig_type)
        if self.last_alert.get(key) == bar_ts:
            return
        self.last_alert[key] = bar_ts
        with self._lock:
            self.latest_signals.append(
                {"pair": pair, "tf": tf, "msg": text, "ts": int(time.time())}
            )
            if len(self.latest_signals) > 200:
                self.latest_signals = self.latest_signals[-200:]

    def _loop(self):
        while not self._stop.is_set():
            try:
                for pair in self.symbols:
                    for tf in self.timeframes:
                        candles: List[Candle] = self._fetch(pair, tf, limit=400)
                        candles = _closed_only(candles, tf)

                        # keep only the last window so signals can't see older bars
                        if len(candles) > _SIGNAL_WINDOW:
                            candles = candles[-_SIGNAL_WINDOW:]

                        if len(candles) < max(12, _SIGNAL_WINDOW):
                            continue

                        pb = signal_pullback_bounce(candles)
                        if pb:
                            msg, bar_ts = pb
                            self._emit_once(pair, tf, "pullback_bounce", bar_ts, f"✅ {msg}")

                        bo = signal_breakout(candles)
                        if bo:
                            msg, lvl, bar_ts = bo
                            self.breakout_levels[(pair, tf)] = lvl
                            self._emit_once(pair, tf, "breakout", bar_ts, f"🚀 {msg}")

                        key = (pair, tf)
                        if key in self.breakout_levels:
                            rt = signal_breakout_retest(candles, self.breakout_levels[key])
                            if rt:
                                msg, bar_ts = rt
                                self._emit_once(pair, tf, "retest", bar_ts, f"🔁 {msg}")

                        ox = signal_overextended(candles)
                        if ox:
                            msg, bar_ts = ox
                            self._emit_once(pair, tf, "overextended", bar_ts, f"⚠️ {msg}")

                        wr = signal_wick_reversal(
                            candles,
                            min_body_pct=0.10,
                            wick_mult=1.2,
                            context_lookback=_CONTEXT_LOOKBACK,
                            min_wick_pct_of_price=0.001 if tf in ("1m", "3m", "5m", "15m") else 0.0,
                        )
                        if wr:
                            msg, bar_ts = wr
                            self._emit_once(pair, tf, "wick", bar_ts, f"🔻/🔺 {msg}")

            except Exception as e:
                with self._lock:
                    self.latest_signals.append(
                        {"pair": "system", "tf": "error", "msg": f"[loop error] {e}", "ts": int(time.time())}
                    )

            for _ in range(self.poll_sec):
                if self._stop.is_set():
                    break
                time.sleep(1)

    def get_status(self):
        with self._lock:
            return {
                "running": self.running,
                "symbols": list(self.symbols),
                "timeframes": list(self.timeframes),
                "poll_sec": self.poll_sec,
                "source": self.data_source,
                "signals": list(self.latest_signals)[-50:][::-1],
            }


BOT = AlertBot()
