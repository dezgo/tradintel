# ───────────────────────────────────────────────────────────────────────────────
# app/risk.py
"""Portfolio risk controls.

DrawdownCircuitBreaker auto-pauses trading when portfolio equity falls more than
a configured percentage below its running peak — the "stop-loss for the whole
system" discipline layer. It is OPT-IN: with max_drawdown_pct <= 0 (the default)
it does nothing but report status, so it can never disrupt an existing setup.

State is persisted in the Storage settings table so the peak survives restarts:
  - max_drawdown_pct        threshold in percent (0 = disabled)
  - risk_peak_equity        running high-water mark of portfolio equity
  - risk_breaker_tripped_ts  epoch seconds when the breaker last fired
The breaker pauses trading by setting the same `trading_paused` flag the rest of
the app already honours.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from app.storage import store as _default_store

THRESHOLD_KEY = "max_drawdown_pct"
PEAK_KEY = "risk_peak_equity"
TRIPPED_TS_KEY = "risk_breaker_tripped_ts"
PAUSED_KEY = "trading_paused"


@dataclass
class RiskStatus:
    enabled: bool          # is the breaker armed (threshold > 0)?
    threshold_pct: float   # configured max drawdown, percent
    peak_equity: float     # running high-water mark
    current_equity: float
    drawdown_pct: float    # current drawdown from peak, percent (>= 0)
    tripped: bool          # did the breaker fire on THIS check?
    paused: bool           # is trading currently paused?
    tripped_ts: Optional[int] = None


def _to_float(value, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


class DrawdownCircuitBreaker:
    """Pauses trading when drawdown from peak equity exceeds max_drawdown_pct."""

    def __init__(self, store=None):
        self.store = store if store is not None else _default_store

    # ── settings helpers ──────────────────────────────────────────────────
    def threshold_pct(self) -> float:
        return max(0.0, _to_float(self.store.get_setting(THRESHOLD_KEY, default=0.0)))

    def peak_equity(self) -> float:
        return _to_float(self.store.get_setting(PEAK_KEY, default=0.0))

    def is_paused(self) -> bool:
        return bool(self.store.get_setting(PAUSED_KEY, default=True))

    # ── core ──────────────────────────────────────────────────────────────
    def check(self, current_equity: float, now_ts: Optional[int] = None) -> RiskStatus:
        """Update the peak, compute drawdown, and trip (pause) if over threshold.

        Returns a RiskStatus describing the current state. `tripped` is True only
        on the check that actually fires the breaker (i.e. not on subsequent
        checks while already paused).
        """
        current_equity = _to_float(current_equity)
        threshold = self.threshold_pct()

        # Ratchet the high-water mark upward and persist any change.
        prev_peak = self.peak_equity()
        peak = max(prev_peak, current_equity)
        if peak != prev_peak:
            self.store.set_setting(PEAK_KEY, peak)

        drawdown_pct = 0.0
        if peak > 0 and current_equity < peak:
            drawdown_pct = (peak - current_equity) / peak * 100.0

        already_paused = self.is_paused()

        # Disabled: report only.
        if threshold <= 0:
            return RiskStatus(
                enabled=False, threshold_pct=0.0, peak_equity=peak,
                current_equity=current_equity, drawdown_pct=drawdown_pct,
                tripped=False, paused=already_paused,
            )

        should_trip = drawdown_pct >= threshold
        if should_trip and not already_paused:
            ts = int(now_ts if now_ts is not None else time.time())
            self.store.set_setting(PAUSED_KEY, True)
            self.store.set_setting(TRIPPED_TS_KEY, ts)
            return RiskStatus(
                enabled=True, threshold_pct=threshold, peak_equity=peak,
                current_equity=current_equity, drawdown_pct=drawdown_pct,
                tripped=True, paused=True, tripped_ts=ts,
            )

        return RiskStatus(
            enabled=True, threshold_pct=threshold, peak_equity=peak,
            current_equity=current_equity, drawdown_pct=drawdown_pct,
            tripped=False, paused=already_paused,
            tripped_ts=self.store.get_setting(TRIPPED_TS_KEY, default=None),
        )

    def status(self, current_equity: float) -> RiskStatus:
        """Read-only view: like check() but never trips or pauses."""
        current_equity = _to_float(current_equity)
        threshold = self.threshold_pct()
        peak = max(self.peak_equity(), current_equity)
        drawdown_pct = 0.0
        if peak > 0 and current_equity < peak:
            drawdown_pct = (peak - current_equity) / peak * 100.0
        return RiskStatus(
            enabled=threshold > 0, threshold_pct=threshold, peak_equity=peak,
            current_equity=current_equity, drawdown_pct=drawdown_pct,
            tripped=False, paused=self.is_paused(),
            tripped_ts=self.store.get_setting(TRIPPED_TS_KEY, default=None),
        )

    def set_threshold(self, pct: float) -> None:
        """Set the max-drawdown threshold (percent). 0 disables the breaker."""
        self.store.set_setting(THRESHOLD_KEY, max(0.0, _to_float(pct)))

    def reset_peak(self, equity: Optional[float] = None) -> None:
        """Reset the high-water mark (e.g. to re-arm after a trip)."""
        self.store.set_setting(PEAK_KEY, _to_float(equity) if equity is not None else 0.0)
        self.store.set_setting(TRIPPED_TS_KEY, None)
