# app/settings_store.py
import json
from pathlib import Path
from typing import Dict, List

DEFAULTS = {
    "symbols": ["ETH_USDT"],
    "timeframes": ["15m", "1h", "1d"],
    "poll_sec": 60,
    "source": "gate",  # 'gate' or 'binance'
}


def _settings_path() -> Path:
    p = Path(__file__).resolve().parent / "data"
    p.mkdir(parents=True, exist_ok=True)
    return p / "settings.json"


def load_settings() -> Dict:
    path = _settings_path()
    if not path.exists():
        return DEFAULTS.copy()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data.get("symbols"), list):
            data["symbols"] = DEFAULTS["symbols"]
        if not isinstance(data.get("timeframes"), list):
            data["timeframes"] = DEFAULTS["timeframes"]
        if not isinstance(data.get("poll_sec"), int):
            data["poll_sec"] = DEFAULTS["poll_sec"]
        if data.get("source") not in ("gate", "binance"):
            data["source"] = DEFAULTS["source"]
        return data
    except Exception:
        return DEFAULTS.copy()


def save_settings(symbols: List[str], timeframes: List[str], poll_sec: int, source: str) -> None:
    payload = {
        "symbols": symbols,
        "timeframes": timeframes,
        "poll_sec": int(poll_sec),
        "source": source,
    }
    _settings_path().write_text(json.dumps(payload, indent=2), encoding="utf-8")
