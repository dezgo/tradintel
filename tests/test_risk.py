"""Tests for the drawdown circuit-breaker (app/risk.py).

Deterministic and offline: each test uses a fresh temp-DB Storage so settings
don't leak between tests.
"""
import os
import tempfile

import pytest

from app.storage import Storage
from app.risk import DrawdownCircuitBreaker, PAUSED_KEY


def _breaker():
    db_path = os.path.join(tempfile.mkdtemp(prefix="tradintel_risk_"), "risk.db")
    store = Storage(db_path)
    return DrawdownCircuitBreaker(store=store), store


def test_disabled_by_default_never_trips():
    breaker, store = _breaker()
    store.set_setting(PAUSED_KEY, False)
    breaker.check(1000.0)
    # Huge drop, but threshold is 0 (disabled) -> no trip, no pause.
    res = breaker.check(100.0)
    assert res.enabled is False
    assert res.tripped is False
    assert store.get_setting(PAUSED_KEY) is False


def test_peak_ratchets_up():
    breaker, store = _breaker()
    breaker.check(1000.0)
    breaker.check(1200.0)
    res = breaker.check(1100.0)  # below the new peak
    assert res.peak_equity == pytest.approx(1200.0)
    assert res.drawdown_pct == pytest.approx((1200 - 1100) / 1200 * 100)


def test_trips_when_drawdown_exceeds_threshold():
    breaker, store = _breaker()
    store.set_setting(PAUSED_KEY, False)
    breaker.set_threshold(20.0)
    breaker.check(1000.0)            # establish peak
    res = breaker.check(750.0)       # -25% from peak, over the 20% limit
    assert res.tripped is True
    assert res.paused is True
    assert store.get_setting(PAUSED_KEY) is True
    assert res.tripped_ts is not None


def test_does_not_trip_within_threshold():
    breaker, store = _breaker()
    store.set_setting(PAUSED_KEY, False)
    breaker.set_threshold(20.0)
    breaker.check(1000.0)
    res = breaker.check(850.0)       # -15%, within limit
    assert res.tripped is False
    assert store.get_setting(PAUSED_KEY) is False


def test_trips_only_once():
    breaker, store = _breaker()
    store.set_setting(PAUSED_KEY, False)
    breaker.set_threshold(20.0)
    breaker.check(1000.0)
    first = breaker.check(700.0)
    second = breaker.check(680.0)
    assert first.tripped is True
    assert second.tripped is False   # already paused -> not a fresh trip
    assert second.paused is True


def test_reset_peak_rearms():
    breaker, store = _breaker()
    store.set_setting(PAUSED_KEY, False)
    breaker.set_threshold(20.0)
    breaker.check(1000.0)
    breaker.check(700.0)             # trips, pauses
    # Operator resumes and re-arms at the current level.
    store.set_setting(PAUSED_KEY, False)
    breaker.reset_peak(700.0)
    res = breaker.check(700.0)       # flat vs new peak -> no trip
    assert res.tripped is False
    assert res.peak_equity == pytest.approx(700.0)


def test_status_is_read_only():
    breaker, store = _breaker()
    store.set_setting(PAUSED_KEY, False)
    breaker.set_threshold(20.0)
    breaker.check(1000.0)
    s = breaker.status(500.0)        # -50%, well over threshold
    assert s.drawdown_pct == pytest.approx(50.0)
    assert s.tripped is False        # status() must never trip
    assert store.get_setting(PAUSED_KEY) is False
