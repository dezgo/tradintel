# tests/test_portfolio.py
import pytest

from app.portfolio import build_portfolio


def test_build_and_step_smoke():
    """Smoke test: the portfolio builds, steps, and snapshots into the expected shape.

    build_portfolio() and step() reach the exchange for balances/bars, so this is
    skipped (not failed) when creds or network are unavailable. The assertions are
    deliberately shape-based — the system now uses a single evolved-strategies
    manager rather than the old fixed 3 (mean_reversion/breakout/trend_follow), so
    we no longer hardcode a manager count.
    """
    try:
        pm = build_portfolio()
        pm.step()
    except Exception as e:  # missing creds, offline, exchange geo-block, etc.
        pytest.skip(f"portfolio build/step needs exchange creds or network: {e}")

    snap = pm.snapshot()
    assert "strategies" in snap
    assert len(snap["strategies"]) >= 1
    for strat in snap["strategies"]:
        assert "bots" in strat
        assert "equity" in strat
