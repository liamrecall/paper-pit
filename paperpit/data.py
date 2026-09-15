"""Market data for Paper Pit.

Public endpoints only - no API key, no account, nothing to sign.
Falls back to cached bars, and then to a deterministic synthetic series, so the
desk always runs. The run report always says which source it used.
"""
from __future__ import annotations

import json
import math
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

SPOT_KLINES = "https://api.binance.com/api/v3/klines"
FUNDING = "https://fapi.binance.com/fapi/v1/fundingRate"
CACHE_DIR = Path(__file__).resolve().parents[1] / "data"
MAX_PER_CALL = 1000

INTERVAL_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}


def bars_per_day(interval: str) -> int:
    return (24 * 60) // INTERVAL_MINUTES[interval]


@dataclass
class Bar:
    ts: int  # open time, ms
    open: float
    high: float
    low: float
    close: float
    volume: float


def _get_json(url: str, params: dict, timeout: float = 20.0):
    req = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(params)}",
        headers={"User-Agent": "paper-pit/0.1"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _cache_path(name: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / name


def _fetch_klines(symbol: str, interval: str, needed: int) -> list[list]:
    """Walk backwards in pages of 1000 until we have enough bars."""
    rows: list[list] = []
    end_time = None
    while len(rows) < needed:
        params = {"symbol": symbol, "interval": interval, "limit": MAX_PER_CALL}
        if end_time is not None:
            params["endTime"] = end_time
        page = _get_json(SPOT_KLINES, params)
        if not page:
            break
        rows = page + rows
        end_time = int(page[0][0]) - 1
        if len(page) < MAX_PER_CALL:
            break
        time.sleep(0.2)  # be polite to a public endpoint
    return rows[-needed:] if len(rows) > needed else rows


def load_bars(symbol: str, days: int, interval: str = "5m", offline: bool = False) -> tuple[list[Bar], str]:
    """Return (bars, source). Source is 'live', 'cache' or 'synthetic'."""
    needed = (days + 1) * bars_per_day(interval) + 200  # sessions plus warmup
    cache = _cache_path(f"{symbol}_{interval}.json")

    if not offline:
        try:
            raw = _fetch_klines(symbol, interval, needed)
            if len(raw) >= needed // 2:
                cache.write_text(json.dumps(raw))
                return [_to_bar(r) for r in raw], "live"
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            pass

    if cache.exists():
        raw = json.loads(cache.read_text())
        if len(raw) >= needed // 2:
            return [_to_bar(r) for r in raw], "cache"

    return _synthetic(symbol, needed, interval), "synthetic"


def _to_bar(row: list) -> Bar:
    return Bar(int(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5]))


def load_funding(symbol: str, offline: bool = False) -> list[tuple[int, float]]:
    """Recent funding rates as (timestamp_ms, rate). Empty list if unavailable."""
    cache = _cache_path(f"{symbol}_funding.json")
    if not offline:
        try:
            raw = _get_json(FUNDING, {"symbol": symbol, "limit": 500})
            cache.write_text(json.dumps(raw))
            return [(int(r["fundingTime"]), float(r["fundingRate"])) for r in raw]
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError):
            pass
    if cache.exists():
        try:
            raw = json.loads(cache.read_text())
            return [(int(r["fundingTime"]), float(r["fundingRate"])) for r in raw]
        except (ValueError, KeyError):
            return []
    return []


def funding_at(rates: list[tuple[int, float]], ts: int) -> float:
    """Funding rate in effect at ts. 0.0 when unknown."""
    rate = 0.0
    for t, r in rates:
        if t <= ts:
            rate = r
        else:
            break
    return rate


def _synthetic(symbol: str, count: int, interval: str) -> list[Bar]:
    """Deterministic random walk. Only used when there is no network and no cache."""
    rng = random.Random(sum(ord(c) for c in symbol))
    price = {"BTCUSDT": 64000.0, "ETHUSDT": 3200.0, "SOLUSDT": 150.0}.get(symbol, 10.0 + len(symbol))
    step_ms = INTERVAL_MINUTES[interval] * 60_000
    ts = 1_700_000_000_000
    bars: list[Bar] = []
    for i in range(count):
        drift = math.sin(i / 90.0) * 0.0004
        step = rng.gauss(drift, 0.0016)
        open_ = price
        close = max(0.01, price * (1 + step))
        high = max(open_, close) * (1 + abs(rng.gauss(0, 0.0007)))
        low = min(open_, close) * (1 - abs(rng.gauss(0, 0.0007)))
        volume = abs(rng.gauss(1000, 300)) * (1.9 if abs(step) > 0.0026 else 1.0)
        bars.append(Bar(ts + i * step_ms, open_, high, low, close, volume))
        price = close
    return bars
