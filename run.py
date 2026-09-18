#!/usr/bin/env python3
"""Paper Pit - run the desk for one or more sessions.

    python3 run.py                 # last session, $10,000 paper account
    python3 run.py --days 5        # five sessions in a row
    python3 run.py --offline       # no network: cached bars, or synthetic if there are none
    python3 run.py --grok          # DEVIL and VETO also ask Grok (needs XAI_API_KEY)
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from paperpit import bots, grok
from paperpit.pit import day_ranges, load_desk, new_state, run_day, write_workspace


def load_env(path: Path = Path(__file__).with_name(".env")) -> None:
    """Read a local .env so the xAI key never has to live in shell history."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if value.strip():
            os.environ.setdefault(key.strip(), value.strip())


DEFAULT_SYMBOLS = "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,ADAUSDT,AVAXUSDT,LINKUSDT,TONUSDT,SUIUSDT"


def main() -> None:
    load_env()
    parser = argparse.ArgumentParser(description="Paper Pit - a trading desk that mostly says no")
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument("--equity", type=float, default=10_000.0)
    parser.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    parser.add_argument("--interval", default="5m", choices=["1m", "5m", "15m", "30m", "1h", "4h"])
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--grok", action="store_true", help="let Grok review as a second opinion")
    parser.add_argument("--no-devil", action="store_true", help="replay the session with DEVIL switched off")
    parser.add_argument("--max-fills", type=int, help="trades allowed per session")
    parser.add_argument("--max-open", type=int, help="positions the desk may carry at once")
    parser.add_argument("--cooldown", type=int, help="bars to wait after a trade closes")
    args = parser.parse_args()

    if args.no_devil:
        bots.SETTINGS["devil"] = False
    for key in ("max_fills", "max_open", "cooldown"):
        value = getattr(args, key)
        if value is not None:
            bots.SETTINGS[key] = value

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    series_map, rates_map, source, total_bars = load_desk(symbols, args.days, args.interval, args.offline)
    ranges = day_ranges(total_bars, args.days, args.interval)
    if not ranges:
        raise SystemExit("not enough history for a full session - try fewer days or a larger interval")

    ask_devil = ask_veto = None
    if args.grok:
        if not grok.available():
            print("XAI_API_KEY is not set, running on rules only")
        else:
            ask_devil, ask_veto = grok.reviewer("devil"), grok.reviewer("veto")

    state = new_state(args.equity)
    print(f"data: {source} | {len(symbols)} symbols | {args.interval} bars | paper fills only\n")

    for number, day_bars in enumerate(ranges, start=1):
        ledger, summary = run_day(number, day_bars, series_map, rates_map, state, ask_devil, ask_veto)
        report = write_workspace(number, ledger, summary, source)
        print(f"day {number}:")
        print(f"  {summary['setups']} setups flagged")
        print(f"  {summary['killed']} killed by DEVIL")
        print(f"  {summary['dropped']} dropped by SIZER")
        print(f"  {summary['blocked']} blocked by VETO")
        print(f"  {summary['filled']} filled")
        print(f"  paper P&L {summary['pnl']}, equity {summary['equity']}")
        print(f"  journal: workspace/journal/day-{number}.md\n")


if __name__ == "__main__":
    main()
