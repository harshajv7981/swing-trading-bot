---
name: risk-check
description: Turn a swarm view into a sized, rule-checked paper order with swingbot-risk (exit plan, Time Machine odds, position size, PASS/BLOCK gate), or move the trailing stop on an open position. Use after market-research gives a bullish view, or when asked "how many shares", "where's my stop", "can I take this trade", "is this safe", "what are the odds", "run the time machine", "trail my stop". Never places an order.
---

# Risk check, run by Claude Code

The risk engine is the hard boundary in the build plan: the swarm proposes,
and only an order that PASSes here may go on to paper trading. You run the
tool and report what it says. You never argue with it, work around it, or
change its limits.

## Rules

1. **Never edit `config/risk.toml` or `config/nse_holidays.toml`.** If the user
   wants a different limit, tell them to change it by hand in its own commit.
2. **Never reset the kill switch.** `swingbot-risk kill-switch --reset` is for
   Harsha to type by hand. It refuses to run inside Claude Code or without a
   terminal, and `.claude/settings.json` denies it and edits to
   `data/state/kill_switch.json`; do not look for a way round either. If it is
   latched, say so and stop.
3. **A BLOCK is final for that order.** Do not shrink the quantity, move the
   stop or drop a flag to get a PASS. You may suggest a fix (a closer stop from
   a new plan, wait for results, skip the trade) and run the steps again only
   if the user asks.
4. **Nothing here places an order.** There is no broker step yet. The output
   is a paper order for the journal.
5. **Same `--as-of` for every step** of one decision, and the same one the
   market-research run used.

## Steps

```bash
swingbot-paper portfolio --out portfolio.json      # once the paper account exists
swingbot-risk mood --out runs/risk/mood.json
swingbot-risk plan TICKER --stop atr:2 --target 2R --out runs/risk/plan.json
swingbot-risk odds runs/risk/plan.json --hold 10
swingbot-risk size runs/risk/plan.json --portfolio portfolio.json --mood runs/risk/mood.json --out runs/risk/order.json
swingbot-risk check runs/risk/order.json --portfolio portfolio.json --results-date YYYY-MM-DD
swingbot-risk ticket runs/risk/order.json --out runs/risk/ticket.json   # only after a PASS
```

- **mood** (R11): bullish, neutral, defensive or unknown, from the Nifty's
  trend, Nifty vs FMCG, gold and USD/INR, and India VIX. When it is defensive,
  `size` and `check` cut the risk per trade to `defensive_risk_scale` of the
  usual 1% (half, by default). Give the verdict and every check line, and say
  which checks were unavailable. Unknown means too few series: say so, never
  call it neutral.

- **plan**: stop rules are `atr:K`, `pct:X`, `price:X`, `low:N`; targets `2R` or
  `price:X`. Leave out `--entry` to use the last close known at `--as-of`. A
  refusal is one sentence: tell the user it as printed.
- **odds** (the Time Machine): replays the hold 10,000 times from the stock's
  own past sessions (seed 19), then runs the same stop and target on every
  past day as a base rate. Tell the user, in this order: the replay odds
  (target first, stop first, neither, and how often it gapped through the
  stop), the 5 to 95% range of the price at the end, the base rate line, then
  the verdict and any line under it exactly as printed. Say the seed. If the
  verdict is a coin flip or close to break-even, say so plainly; if the
  replays and the base rate disagree, say that too. It is built from the past
  and is not a prediction, and it leaves costs out. It never blocks a trade:
  the user decides.
- **size**: prints the shares, which limit decided them, and the loss at the
  stop. A refusal means even one share breaks a limit.
- **check**: PASS or BLOCK with every rule's line. Ask the user for the next
  results date (from the company's board meeting notice) and pass it with
  `--results-date`; without it R9 cannot be checked and the tool says so.
- **ticket** (after a PASS only): the paper broker orders, a DAY limit buy
  and, once it fills, an OCO target and stop. Show the user its printed lines
  and say it is paper only and was not sent. A refusal (no PASS today, a later
  BLOCK, the kill switch) is final: report it as printed, never edit the
  order or the verdict log to get a ticket.
- **trail** (open positions): `swingbot-risk trail TICKER --entry-date D
  --entry E --initial-stop S0 --stop S`. The stop only ever moves up.

**Several calls at once** (a day's swarm run): `swingbot-risk orders
runs/signals.jsonl --portfolio portfolio.json --mood runs/risk/mood.json
--results-dates data/results_dates.csv` runs every step above for each call
and writes `runs/orders/<date>/`. Report the summary line, each ticket line,
and every BLOCK and refusal as printed; skipped calls can be one line each.
The same rules apply: never edit `config/signals.toml` to let a call through.

After a paper fill, record it with `swingbot-paper fill buy --order
runs/risk/order.json --qty N --price P --at "YYYY-MM-DD HH:MM"`, using what the
paper broker actually filled. `swingbot-paper report` shows the account, the
drawdown, the return against the Nifty, progress to the paper gate and every
rule a trade broke.

`portfolio.json` holds `equity`, `cash`, `week_open_equity` (Monday's open),
`day_open_equity` (today's open) and `positions` (ticker, qty, entry, stop,
sector), in rupees. If it is missing, ask the user for those numbers.

## What to tell the user

The order in one line (shares, price, rupee amount), the stop and target, the
loss at the stop and what share of equity it is, then PASS or BLOCK. For a
BLOCK, every failing rule as printed, and the fix in plain words. Pass on any
`note` lines too.
