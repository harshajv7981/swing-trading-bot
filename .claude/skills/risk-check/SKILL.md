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
   Harsha to type by hand. If it is latched, say so and stop.
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
swingbot-risk plan TICKER --stop atr:2 --target 2R --out runs/risk/plan.json
swingbot-risk odds runs/risk/plan.json --hold 10
swingbot-risk size runs/risk/plan.json --portfolio portfolio.json --out runs/risk/order.json
swingbot-risk check runs/risk/order.json --portfolio portfolio.json --results-date YYYY-MM-DD
```

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
- **trail** (open positions): `swingbot-risk trail TICKER --entry-date D
  --entry E --initial-stop S0 --stop S`. The stop only ever moves up.

`portfolio.json` holds `equity`, `cash`, `week_open_equity` (Monday's open),
`day_open_equity` (today's open) and `positions` (ticker, qty, entry, stop,
sector), in rupees. If it is missing, ask the user for those numbers.

## What to tell the user

The order in one line (shares, price, rupee amount), the stop and target, the
loss at the stop and what share of equity it is, then PASS or BLOCK. For a
BLOCK, every failing rule as printed, and the fix in plain words. Pass on any
`note` lines too.
