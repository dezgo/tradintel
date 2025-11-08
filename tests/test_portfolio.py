# tests/test_portfolio.py
from app.portfolio import build_portfolio


def test_build_and_step():
    pm = build_portfolio()
    pm.step()
    snap = pm.snapshot()
    assert "strategies" in snap and len(snap["strategies"]) == 3
