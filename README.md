# Paper Pit

A six-seat trading desk that is built to talk itself out of trades.

Paper trading only. There is no exchange account, no signing key and no order
router anywhere in this repository. The desk reads public market data, argues
with itself, and writes down every trade that did not happen.

```
TAPE ─┐
      ├─► DEVIL ─► SIZER ─► VETO ─► FILLED
WIRE ─┘                │
                       └─► LEDGER (every rejection, with the reason)
```

## The six seats

| seat | job |
| --- | --- |
| TAPE | reads price and volume, flags setups |
| WIRE | reads funding, tags why something moved |
| DEVIL | argues against every setup. nothing else |
| SIZER | turns a survivor into a position size |
| VETO | can block a ticket. can never approve one |
| LEDGER | writes down every trade that did not happen, and why |

## Rules that do not change

1. No seat approves its own idea.
2. The safety seat only says no.
3. Paper fills until the journal earns real ones.

## Run it

Python 3.10+, no dependencies.

```bash
python3 run.py                 # last session on a $10,000 paper account
python3 run.py --days 5        # five sessions in a row
python3 run.py --offline       # no network: cached data, or synthetic if there is none
```

Output:

```
day 1:
  230 setups flagged
  213 killed by DEVIL
  1 dropped by SIZER
  13 blocked by VETO
  3 filled
  paper P&L -16.63, equity 9983.37
```

(One real session on live data. Your run pulls different bars, so your numbers
will differ - the shape is the point: almost everything gets thrown away.)

Everything the desk did lands in the workspace, in the same folders the seats
talk through:

```
workspace/ideas/day-1.jsonl      every setup TAPE flagged
workspace/tickets/day-1.jsonl    the ones that reached VETO
workspace/journal/day-1.jsonl    every verdict, with a reason
workspace/journal/day-1.md       the session report
```

## Letting Grok argue

DEVIL and VETO can take a second opinion from Grok through the xAI API. Without
a key the desk runs on its own rules and nothing changes.

```bash
cp .env.example .env     # put your key in it
export XAI_API_KEY=...   # or export it directly
python3 run.py --grok
```

The reviewer can only answer `kill` or `pass` with one line of reasoning, and a
failed API call never moves the desk - it falls straight back to the rules.

## Risk settings

Defaults live at the top of `paperpit/bots.py`:

- 1% of equity risked per ticket, stop at 1.5 ATR, target at 2R
- maximum 2 open positions, one per symbol, no second correlated long
- 2% daily loss limit, after which VETO closes the desk for the session

## What this is not

It is not a signal service, not financial advice, and not a live trading bot.
It is a way to watch an agent desk make decisions where every rejection is
written down and can be argued with afterwards.

MIT licensed.
