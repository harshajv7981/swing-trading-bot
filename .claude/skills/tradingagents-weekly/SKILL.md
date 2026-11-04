---
name: tradingagents-weekly
description: Run the weekly TradingAgents paper test inside Claude Code with no API key. Claude plays TradingAgents' roles (analysts, bull/bear debate, research manager, trader, risk debate, portfolio manager) on 20 fixed mid-caps and logs one rating per stock. Use when asked to "run tradingagents", "do this week's agents run", "weekly agents decisions", or to score the agents trial. Never places an order.
---

# TradingAgents weekly paper test, run by Claude Code

This is [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
(Apache 2.0) with you as the model instead of an API. The data tools are
TradingAgents' own, run by `swingbot-agents brief` with no model calls. The
roles and the rating scale below follow TradingAgents' own prompts. The rules
of the test are fixed in `docs/research/tradingagents-paper-test.md`. Read it
once per session and do not change it.

## Rules

1. **Paper only.** Never place, suggest placing, or size a real order. Never
   touch the momentum trial (`swingbot-momentum`, `data/state/momentum_*`).
2. **Use only the brief.** Decide each stock from its brief file and
   `_market.md`, and nothing else. Don't search the web, don't open other
   files about the stock, and don't use what you remember about prices after
   the decision date. A tool section that says unavailable means the data is
   unavailable: say so and don't fill the gap from memory.
3. **One stock at a time, all roles, then record.** Don't look at other
   stocks' ratings this week while deciding one.
4. **Keep it short.** Each role gets 2–5 sentences. The whole run should fit
   in one session; use as little of the plan as possible.
5. **Never edit `data/state/agents_trial/decisions.jsonl` by hand.** Only
   `swingbot-agents record` writes it. A recorded decision is never changed,
   and `record` refuses a date more than 3 days old.

## Steps

1. **When:** on the decision day after the NSE close (after 15:30 IST),
   normally Friday. TradingAgents withholds fundamentals when a brief is made
   for a past date, so make the brief on the day itself.
2. Make the briefs (no model calls):
   ```bash
   swingbot-agents brief --data data/raw/eod2_data/daily
   ```
   If it refuses because the last decisions were under 7 days ago, stop and
   tell the user the next date it allows.
3. `swingbot-agents pending --date <date>` lists the stocks still to do. If a
   session ends halfway, run this in the next one and carry on.
4. Read `runs/agents-trial/briefs/<date>/_market.md` once. Then, for each
   pending stock, read its brief and write these roles in order, in your
   reply or in a scratch note:
   - **Market analyst:** the trend and momentum from the verified snapshot
     (moving averages, RSI, MACD, Bollinger, ATR, recent closes). Quote exact
     numbers only from the snapshot.
   - **News analyst:** what the company news and the India market news this
     week mean for the stock.
   - **Fundamentals analyst:** what the fundamentals section supports, or that
     it was withheld or unavailable.
   - **Bull researcher:** the strongest case for owning it over the next week,
     answering the bear's likely objections.
   - **Bear researcher:** the strongest case against, answering the bull.
   - **Research manager:** weigh the debate and give one of Buy / Overweight /
     Hold / Underweight / Sell. Conflict alone is not a reason to Hold: commit
     to the stronger side, sized by how decisively it wins. Hold only when the
     evidence is still balanced after weighing it, or too thin to support a
     call.
   - **Trader:** the concrete plan for a 5-session hold that follows from it.
   - **Aggressive, conservative and neutral risk analysts:** one point each on
     the trader's plan.
   - **Portfolio manager:** the final rating, using the same scale and the same
     "commit, don't hedge" rule, plus a one-sentence thesis. Weigh the
     stock's earlier calls in the brief: what worked and what didn't.
5. Record it straight away:
   ```bash
   swingbot-agents record --date <date> --symbol <SYMBOL> --rating <Rating> \
     --model <the model you are running as> --summary "<one-sentence thesis>"
   ```
6. When `pending` says all 20 are done, tell the user how many of each rating
   were given and which stocks are Buy or Overweight this week. Remind them
   it's paper only.

## Scoring

Once eod2_data has the closes 5 sessions after a decision date:
```bash
swingbot-agents score --data data/raw/eod2_data/daily
```
Report the table as printed. Don't judge the test before the 13 weeks the
pre-registration sets, and don't change the rules because of early results.
