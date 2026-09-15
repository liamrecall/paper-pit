"""The floor itself: one session, seat by seat.

A session is one day of bars. On every bar the desk first manages what it is
already carrying, then looks for new work. Nothing here touches a real exchange:
fills are paper fills at the bar close, and there is no signing key anywhere in
this repository.
"""
from __future__ import annotations

import json
from pathlib import Path

from .bots import Ledger, Series, devil, sizer, tape, veto, wire
from .data import bars_per_day, load_bars, load_funding

WORKSPACE = Path(__file__).resolve().parents[1] / "workspace"
WARMUP_BARS = 200


def new_state(equity: float) -> dict:
    return {
        "equity": equity,
        "day_start_equity": equity,
        "day_pnl": 0.0,
        "open": [],
        "last_loss_bar": {},
        "last_exit_bar": None,
        "day_fills": 0,
    }


def _manage_open(state: dict, series_map: dict[str, Series], i: int, ledger: Ledger) -> None:
    """Stop, target or nothing - checked against the current bar."""
    still_open = []
    for pos in state["open"]:
        bar = series_map[pos["symbol"]].bars[i]
        direction = 1 if pos["side"] == "long" else -1
        hit_stop = bar.low <= pos["stop"] if direction == 1 else bar.high >= pos["stop"]
        hit_target = bar.high >= pos["target"] if direction == 1 else bar.low <= pos["target"]

        if hit_stop:
            _close(state, pos, pos["stop"], "stop hit", i, ledger)
        elif hit_target:
            _close(state, pos, pos["target"], "target hit", i, ledger)
        else:
            still_open.append(pos)
    state["open"] = still_open


def _close(state: dict, pos: dict, price: float, reason: str, i: int, ledger: Ledger) -> None:
    direction = 1 if pos["side"] == "long" else -1
    pnl = (price - pos["entry"]) * pos["qty"] * direction
    state["equity"] += pnl
    state["day_pnl"] += pnl
    state["last_exit_bar"] = i
    if pnl < 0:
        state["last_loss_bar"][pos["symbol"]] = i
    closed = dict(pos)
    closed.update({"exit": round(price, 4), "exit_reason": reason, "pnl": pnl, "price": price})
    ledger.closed(closed)


def run_day(
    day_number: int,
    day_bars: range,
    series_map: dict[str, Series],
    rates_map: dict[str, list],
    state: dict,
    ask_devil=None,
    ask_veto=None,
) -> tuple[Ledger, dict]:
    ledger = Ledger()
    state["day_start_equity"] = state["equity"]
    state["day_pnl"] = 0.0
    state["day_fills"] = 0
    state["last_exit_bar"] = None
    session_length = len(day_bars)

    for i in day_bars:
        _manage_open(state, series_map, i, ledger)

        for symbol, series in series_map.items():
            idea = tape(series, i)
            if idea is None:
                continue
            idea["session_bar"] = i - day_bars.start
            idea["session_length"] = session_length
            idea = wire(idea, rates_map.get(symbol, []))
            ledger.idea(idea)

            killed, reason = devil(idea, state, ask_devil)
            if killed:
                ledger.killed(idea, reason)
                continue

            ticket, drop_reason = sizer(idea, state["equity"])
            if ticket is None:
                ledger.dropped(idea, drop_reason)
                continue

            blocked, block_reason = veto(ticket, state, ask_veto)
            if blocked:
                ledger.blocked(ticket, block_reason)
                continue

            ledger.filled(ticket)
            state["day_fills"] += 1
            state["open"].append(
                {
                    "symbol": ticket["symbol"],
                    "side": ticket["side"],
                    "qty": ticket["qty"],
                    "entry": ticket["price"],
                    "stop": ticket["stop"],
                    "target": ticket["target"],
                    "ts": ticket["ts"],
                    "kind": ticket["kind"],
                }
            )

    last = day_bars[-1]
    for pos in list(state["open"]):
        close_price = series_map[pos["symbol"]].bars[last].close
        _close(state, pos, close_price, "end of session", last, ledger)
    state["open"] = []

    summary = {
        "day": day_number,
        **ledger.counters,
        "pnl": round(state["day_pnl"], 2),
        "equity": round(state["equity"], 2),
    }
    return ledger, summary


def write_workspace(day_number: int, ledger: Ledger, summary: dict, source: str) -> Path:
    """/workspace/ideas, /workspace/tickets, /workspace/journal - the folders the seats talk through."""
    for folder in ("ideas", "tickets", "journal"):
        (WORKSPACE / folder).mkdir(parents=True, exist_ok=True)

    _dump(WORKSPACE / "ideas" / f"day-{day_number}.jsonl", ledger.ideas)
    _dump(WORKSPACE / "tickets" / f"day-{day_number}.jsonl", ledger.tickets)
    _dump(WORKSPACE / "journal" / f"day-{day_number}.jsonl", ledger.journal)

    reasons: dict[str, int] = {}
    for row in ledger.journal:
        if row["verdict"] in ("killed", "dropped", "blocked"):
            head = row["reason"].split(":")[0]
            reasons[head] = reasons.get(head, 0) + 1

    lines = [
        f"# Paper Pit - day {day_number}",
        "",
        f"data: {source} | paper fills only, no exchange account is involved",
        "",
        "| stage | count |",
        "| --- | --- |",
        f"| setups flagged | {summary['setups']} |",
        f"| killed by DEVIL | {summary['killed']} |",
        f"| dropped by SIZER | {summary['dropped']} |",
        f"| blocked by VETO | {summary['blocked']} |",
        f"| filled | {summary['filled']} |",
        "",
        f"paper P&L: {summary['pnl']} | paper equity: {summary['equity']}",
        "",
        "## why trades did not happen",
        "",
    ]
    for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
        lines.append(f"- {reason}: {count}")
    lines += ["", "## fills", ""]
    for row in ledger.journal:
        if row["verdict"] in ("filled", "closed"):
            pnl = "" if row["pnl"] is None else f", pnl {row['pnl']}"
            lines.append(f"- {row['symbol']} {row['side']} {row['verdict']} at {row['price']} ({row['reason']}{pnl})")
    report = WORKSPACE / "journal" / f"day-{day_number}.md"
    report.write_text("\n".join(lines) + "\n")
    return report


def _dump(path: Path, rows: list[dict]) -> None:
    with path.open("w") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def load_desk(
    symbols: list[str], days: int, interval: str, offline: bool
) -> tuple[dict[str, Series], dict[str, list], str, int]:
    series_map: dict[str, Series] = {}
    rates_map: dict[str, list] = {}
    sources: set[str] = set()
    for symbol in symbols:
        bars, source = load_bars(symbol, days, interval, offline)
        sources.add(source)
        series_map[symbol] = Series.build(symbol, bars)
        rates_map[symbol] = load_funding(symbol, offline)

    shortest = min(len(s.bars) for s in series_map.values())
    for symbol, series in series_map.items():
        series_map[symbol] = Series.build(symbol, series.bars[-shortest:])
    return series_map, rates_map, "+".join(sorted(sources)), shortest


def day_ranges(total_bars: int, days: int, interval: str) -> list[range]:
    """Latest sessions that still have enough history in front of them."""
    per_day = bars_per_day(interval)
    ranges: list[range] = []
    end = total_bars
    for _ in range(days):
        start = end - per_day
        if start < WARMUP_BARS:
            break
        ranges.append(range(start, end))
        end = start
    return list(reversed(ranges))
