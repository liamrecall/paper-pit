"""Small indicator helpers. No third-party packages on purpose."""
from __future__ import annotations

from .data import Bar


def ema(values: list[float], length: int) -> list[float]:
    out: list[float] = []
    k = 2 / (length + 1)
    avg = values[0]
    for v in values:
        avg = v * k + avg * (1 - k)
        out.append(avg)
    return out


def atr(bars: list[Bar], length: int = 14) -> list[float]:
    trs: list[float] = []
    prev_close = bars[0].close
    for b in bars:
        tr = max(b.high - b.low, abs(b.high - prev_close), abs(b.low - prev_close))
        trs.append(tr)
        prev_close = b.close
    return ema(trs, length)


def rolling_max(values: list[float], length: int) -> list[float]:
    return [max(values[max(0, i - length + 1): i + 1]) for i in range(len(values))]


def rolling_min(values: list[float], length: int) -> list[float]:
    return [min(values[max(0, i - length + 1): i + 1]) for i in range(len(values))]


def sma(values: list[float], length: int) -> list[float]:
    out: list[float] = []
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= length:
            total -= values[i - length]
        out.append(total / min(i + 1, length))
    return out
