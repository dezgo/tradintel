# ───────────────────────────────────────────────────────────────────────────────
# app/auto_params.py
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Dict, List, Tuple, Callable, Any

from app.core import Bar, DataProvider
from app.managers import StrategyManager, PortfolioManager
from app.strategies import MeanReversion, Breakout, TrendFollow, MR_GRID, BO_GRID, TF_GRID

ScoreFn = Callable[[List[Bar], Dict[str, Any]], float]


def _series_returns(bars: List[Bar]) -> List[float]:
    r = []
    for i in range(1, len(bars)):
        prev = bars[i - 1].close
        cur = bars[i].close
        r.append((cur - prev) / prev)
    return r


def _backtest_exposure(bars: List[Bar], exposures: List[float], fee_bps: float = 5.0) -> float:
    """Toy backtest: equity path with exposure per step, linear fee on exposure change."""
    eq = 1.0
    rets = _series_returns(bars)
    last_exp = 0.0
    for e, r in zip(exposures, rets):
        # trading cost on change in exposure
        cost = abs(e - last_exp) * (fee_bps / 10000.0)
        eq *= (1.0 + e * r - cost)
        last_exp = e
    return eq - 1.0  # total return


def _exposures_for_strategy(strategy_name: str, params: Dict[str, Any], bars: List[Bar]) -> List[float]:
    if strategy_name == "mean_reversion":
        s = MeanReversion(**params)
    elif strategy_name == "breakout":
        s = Breakout(**params)
    else:
        s = TrendFollow(**params)
    exps: List[float] = []
    # feed cumulatively like live run
    for i in range(1, len(bars) + 1):
        exps.append(float(s.on_bar(bars[:i])))
    return exps[1:]  # align to returns length


@dataclass
class AutoParamSelector:
    """Periodically reselects/refreshes parameter variants per StrategyManager.

    - Looks back over last lookback_bars per symbol.
    - Scores param candidates with a tiny walk-forward sim.
    - Keeps top_k per symbol, replaces worst with mutations.
    """

    lookback_bars: int = 1000
    top_k: int = 2
    refresh_seconds: int = 1800  # 30 min
    last_run: float = 0.0

    def maybe_refresh(self, pm: PortfolioManager, data: DataProvider, tf: str) -> None:
        now = time.time()
        if now - self.last_run < self.refresh_seconds:
            return
        self.last_run = now
        for m in pm.managers:
            self._refresh_manager(m, data, tf)

    def _refresh_manager(self, m: StrategyManager, data: DataProvider, tf: str) -> None:
        # Group existing bots by symbol
        by_sym: Dict[str, List] = {}
        for b in m.bots:
            by_sym.setdefault(b.symbol, []).append(b)

        strat = m.name  # "mean_reversion" | "breakout" | "trend_follow"
        seed_grid = MR_GRID if strat == "mean_reversion" else BO_GRID if strat == "breakout" else TF_GRID

        new_bots: List = []
        for sym, bots in by_sym.items():
            bars = data.history(sym, tf, limit=self.lookback_bars)
            if len(bars) < 100:
                new_bots.extend(bots)
                continue

            # Build candidate pool: current params + seed grid + small mutations of top
            candidates: List[Dict[str, Any]] = []
            for b in bots:
                params = self._params_from_name(b.name) or {}
                if params:
                    candidates.append(params)
            candidates.extend(seed_grid)

            # Score candidates
            scored: List[Tuple[float, Dict[str, Any]]] = []
            for p in candidates:
                exps = _exposures_for_strategy(strat, p, bars)
                score = _backtest_exposure(bars[-len(exps)-1:], exps)
                scored.append((score, p))
            scored.sort(key=lambda x: x[0], reverse=True)

            # Keep top_k and mutate a couple
            keep = [p for _, p in scored[: self.top_k]]
            mutants = [self._mutate_params(strat, keep[i % len(keep)]) for i in range(max(0, len(bots) - len(keep)))]
            selected = keep + mutants

            # Rebuild bot objects with selected params (preserve allocation & state where possible)
            idx = 1
            for p in selected[: len(bots)]:
                b = bots[idx - 1]
                new_bots.append(self._rebuild_bot(m.name, b, p, idx))
                idx += 1

        m.bots = new_bots

    # Helpers
    def _params_from_name(self, name: str) -> Dict[str, Any] | None:
        # expects suffix like _p3 or encodes no params; return None to skip
        return None

    def _mutate_params(self, strat: str, base: Dict[str, Any]) -> Dict[str, Any]:
        p = dict(base)
        if strat == "mean_reversion":
            p["lookback"] = max(5, int(round(p["lookback"] * random.uniform(0.8, 1.2))))
            p["band"] = max(1.0, round(p["band"] * random.uniform(0.9, 1.1), 2))
        elif strat == "breakout":
            p["lookback"] = max(10, int(round(p["lookback"] * random.uniform(0.8, 1.2))))
        else:
            # trend follow
            p["fast"] = max(3, int(round(p["fast"] * random.uniform(0.8, 1.2))))
            p["slow"] = max(p["fast"] + 5, int(round(p["slow"] * random.uniform(0.8, 1.2))))
        return p

    def _rebuild_bot(self, strat_name: str, old_bot, params: Dict[str, Any], idx: int):
        from app.bots import TradingBot
        from app.execution import PaperExec
        from app.strategies import MeanReversion, Breakout, TrendFollow
        from app.storage import store

        if strat_name == "mean_reversion":
            strat = MeanReversion(**params)
            prefix = "mr"
        elif strat_name == "breakout":
            strat = Breakout(**params)
            prefix = "bo"
        else:
            strat = TrendFollow(**params)
            prefix = "tf"

        name = f"{prefix}_{old_bot.symbol.lower()}_{old_bot.tf}_p{idx}"
        store.record_params(name, type(strat).__name__, params)

        return TradingBot(
            name=name,
            symbol=old_bot.symbol,
            tf=old_bot.tf,
            strategy=strat,
            data=old_bot.data,
            exec_client=PaperExec(name),
            allocation=old_bot.allocation,
        )
