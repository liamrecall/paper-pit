"""The six seats on the desk.

TAPE   - reads price and volume, flags setups
WIRE   - reads funding, tags why something moved
DEVIL  - argues against every setup, nothing else
SIZER  - turns a survivor into a position size
VETO   - can block a ticket, can never approve one
LEDGER - writes down every trade that did not happen, and why

Every seat returns plain dicts so the whole run can be replayed from the journal.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .data import Bar, funding_at
from .indicators import atr, ema, rolling_max, rolling_min, sma

RISK_PER_TRADE = 0.01          # 1% of equity
STOP_ATR_MULT = 1.5
TARGET_R = 2.0
MAX_STOP_PCT = 0.010           # wider than this and SIZER drops the ticket
MIN_STOP_PCT = 0.0025          # tighter than this and the stop sits inside the noise
MIN_NOTIONAL = 100.0
MAX_NOTIONAL_PCT = 0.20
MAX_OPEN_POSITIONS = 1
MAX_FILLS_PER_SESSION = 3
EXIT_COOLDOWN_BARS = 12        # the desk sits on its hands after a trade closes
DAILY_LOSS_LIMIT = 0.02        # 2% of starting equity
HOT_FUNDING = 0.0005           # 0.05% per 8h
BREAKOUT_LOOKBACK = 20
VOLUME_SPIKE = 1.4


@dataclass
class Series:
    """Precomputed indicators for one symbol."""
    symbol: str
    bars: list[Bar]
    ema_fast: list[float] = field(default_factory=list)
    ema_slow: list[float] = field(default_factory=list)
    atr: list[float] = field(default_factory=list)
    hi: list[float] = field(default_factory=list)
    lo: list[float] = field(default_factory=list)
    vol_avg: list[float] = field(default_factory=list)

    @classmethod
    def build(cls, symbol: str, bars: list[Bar]) -> "Series":
        closes = [b.close for b in bars]
        return cls(
            symbol=symbol,
            bars=bars,
            ema_fast=ema(closes, 20),
            ema_slow=ema(closes, 50),
            atr=atr(bars, 14),
            hi=rolling_max([b.high for b in bars], BREAKOUT_LOOKBACK),
            lo=rolling_min([b.low for b in bars], BREAKOUT_LOOKBACK),
            vol_avg=sma([b.volume for b in bars], BREAKOUT_LOOKBACK),
        )


# --------------------------------------------------------------------------- TAPE
def tape(series: Series, i: int) -> dict | None:
    """Flag a setup on bar i, or nothing."""
    bar = series.bars[i]
    prev_high, prev_low = series.hi[i - 1], series.lo[i - 1]
    trend_up = series.ema_fast[i] > series.ema_slow[i]
    heavy = bar.volume > series.vol_avg[i] * VOLUME_SPIKE

    kind = side = None
    if bar.close > prev_high and heavy and trend_up:
        kind, side = "breakout", "long"
    elif bar.close < prev_low and heavy and not trend_up:
        kind, side = "breakdown", "short"
    elif trend_up and bar.low <= series.ema_fast[i] <= bar.close and series.bars[i - 1].close < series.ema_fast[i - 1]:
        kind, side = "retest", "long"
    if kind is None:
        return None

    return {
        "id": f"{series.symbol}-{bar.ts}",
        "ts": bar.ts,
        "symbol": series.symbol,
        "side": side,
        "kind": kind,
        "price": bar.close,
        "atr": series.atr[i],
        "volume_ratio": round(bar.volume / max(series.vol_avg[i], 1e-9), 2),
        "trend_up": trend_up,
        "ema_fast": series.ema_fast[i],
        "bar": i,
    }


# --------------------------------------------------------------------------- WIRE
def wire(idea: dict, rates: list[tuple[int, float]]) -> dict:
    """Attach the funding context. WIRE never kills anything, it only tags."""
    rate = funding_at(rates, idea["ts"])
    idea["funding"] = rate
    if rate > HOT_FUNDING:
        idea["funding_tag"] = "longs are paying, crowd is long"
    elif rate < -HOT_FUNDING:
        idea["funding_tag"] = "shorts are paying, crowd is short"
    else:
        idea["funding_tag"] = "funding flat"
    return idea


# --------------------------------------------------------------------------- DEVIL
def devil(idea: dict, state: dict, ask_grok=None) -> tuple[bool, str]:
    """Argue against the setup. Returns (killed, reason)."""
    objections: list[str] = []

    if idea["side"] == "long" and not idea["trend_up"]:
        objections.append("no edge: long against the slow trend")
    if idea["side"] == "short" and idea["trend_up"]:
        objections.append("no edge: short against the slow trend")
    if idea["volume_ratio"] < 1.0:
        objections.append("thin: volume below its own average")
    if idea["atr"] / idea["price"] < 0.0015:
        objections.append("no room: range too tight to pay for the stop")
    if idea["side"] == "long" and idea["funding"] > HOT_FUNDING:
        objections.append("crowded: funding says everyone is already long")
    if idea["side"] == "short" and idea["funding"] < -HOT_FUNDING:
        objections.append("crowded: funding says everyone is already short")
    last_loss = state["last_loss_bar"].get(idea["symbol"])
    if last_loss is not None and idea["bar"] - last_loss < 12:
        objections.append("cooldown: this symbol just took money off the desk")
    if abs(idea["price"] - idea["ema_fast"]) > idea["atr"] * 2:
        objections.append("late: price is already two ranges away from the mean")
    if idea["volume_ratio"] < VOLUME_SPIKE:
        objections.append("thin: the move is not carrying enough volume")

    if objections:
        return True, objections[0]

    if ask_grok is not None:
        verdict = ask_grok(idea)
        if verdict and verdict.get("kill"):
            return True, f"grok: {verdict.get('reason', 'no reason given')}"

    return False, ""


# --------------------------------------------------------------------------- SIZER
def sizer(idea: dict, equity: float) -> tuple[dict | None, str]:
    """Turn a survivor into a ticket, or drop it."""
    stop_distance = idea["atr"] * STOP_ATR_MULT
    stop_pct = stop_distance / idea["price"]
    if stop_pct > MAX_STOP_PCT:
        return None, "size too wide: stop would sit further than the account allows"
    if stop_pct < MIN_STOP_PCT:
        return None, "size too tight: the stop would sit inside the noise"

    qty = (equity * RISK_PER_TRADE) / stop_distance
    notional = qty * idea["price"]
    if notional < MIN_NOTIONAL:
        return None, "size too small: the position would not pay for its own fees"
    if notional > equity * MAX_NOTIONAL_PCT:
        qty = (equity * MAX_NOTIONAL_PCT) / idea["price"]
        notional = qty * idea["price"]

    direction = 1 if idea["side"] == "long" else -1
    ticket = dict(idea)
    ticket.update(
        {
            "qty": round(qty, 6),
            "notional": round(notional, 2),
            "stop": round(idea["price"] - direction * stop_distance, 4),
            "target": round(idea["price"] + direction * stop_distance * TARGET_R, 4),
            "risk": round(equity * RISK_PER_TRADE, 2),
        }
    )
    return ticket, ""


# --------------------------------------------------------------------------- VETO
def veto(ticket: dict, state: dict, ask_grok=None) -> tuple[bool, str]:
    """The safety seat. It can only say no. Returns (blocked, reason)."""
    if state["day_pnl"] <= -DAILY_LOSS_LIMIT * state["day_start_equity"]:
        return True, "daily loss limit reached, desk is closed for the day"
    if state["day_fills"] >= MAX_FILLS_PER_SESSION:
        return True, f"session limit: {MAX_FILLS_PER_SESSION} trades is the whole day"
    if len(state["open"]) >= MAX_OPEN_POSITIONS:
        return True, "already carrying a position"
    last_exit = state.get("last_exit_bar")
    if last_exit is not None and ticket["bar"] - last_exit < EXIT_COOLDOWN_BARS:
        return True, "cooldown: the last trade only just closed"
    if any(p["symbol"] == ticket["symbol"] for p in state["open"]):
        return True, "already in this symbol"
    if ticket["side"] == "long" and sum(1 for p in state["open"] if p["side"] == "long"):
        return True, "second correlated long, the book would be one bet"
    if ticket["session_bar"] >= ticket["session_length"] - 6:
        return True, "too late in the session to open anything new"

    if ask_grok is not None:
        verdict = ask_grok(ticket)
        if verdict and verdict.get("kill"):
            return True, f"grok: {verdict.get('reason', 'no reason given')}"

    return False, ""


# --------------------------------------------------------------------------- LEDGER
class Ledger:
    """Writes down every trade that did not happen, and why."""

    def __init__(self) -> None:
        self.ideas: list[dict] = []
        self.tickets: list[dict] = []
        self.journal: list[dict] = []
        self.counters = {"setups": 0, "killed": 0, "dropped": 0, "blocked": 0, "filled": 0}

    def idea(self, idea: dict) -> None:
        self.ideas.append(idea)
        self.counters["setups"] += 1

    def killed(self, idea: dict, reason: str) -> None:
        self.counters["killed"] += 1
        self._log("DEVIL", idea, "killed", reason)

    def dropped(self, idea: dict, reason: str) -> None:
        self.counters["dropped"] += 1
        self._log("SIZER", idea, "dropped", reason)

    def blocked(self, ticket: dict, reason: str) -> None:
        self.counters["blocked"] += 1
        self.tickets.append(ticket)
        self._log("VETO", ticket, "blocked", reason)

    def filled(self, ticket: dict) -> None:
        self.counters["filled"] += 1
        self.tickets.append(ticket)
        self._log("PIT", ticket, "filled", "passed every seat")

    def closed(self, position: dict) -> None:
        self._log("PIT", position, "closed", position["exit_reason"])

    def _log(self, seat: str, item: dict, verdict: str, reason: str) -> None:
        self.journal.append(
            {
                "ts": item["ts"],
                "seat": seat,
                "symbol": item["symbol"],
                "side": item["side"],
                "kind": item.get("kind", ""),
                "price": round(item.get("price", 0.0), 4),
                "verdict": verdict,
                "reason": reason,
                "pnl": round(item.get("pnl", 0.0), 2) if "pnl" in item else None,
            }
        )
