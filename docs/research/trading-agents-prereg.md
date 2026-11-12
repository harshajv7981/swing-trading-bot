# TradingAgents as a Claude Code skill: forward paper test, fixed before the first call

**Change on 2026-11-13, before any call was scored:** the calls move from the
`trading-agents` skill to the Anthropic API (`swingbot-agent-calls run`,
`claude-haiku-5-5`), and the test restarts on that engine. The first scored
call is the first Friday the API makes, planned for Friday 2026-11-20.

- **The pass bar counts API calls only.** The 10 skill calls from 6 Nov stay in the
  log as a separate record; `swingbot-agent-calls score` lists them apart, and they
  are not counted towards the 200 settled calls or the three conditions below.
  Nothing had settled when this was written: their 5-session windows had not finished.
- **Judgement rule unchanged:** the first Friday on or after 31 May 2027, or after
  200 settled directional API calls, whichever comes first. The watchlist, rating
  scale, score and cost are unchanged.
- **The runner enforces the schedule.** It refuses a day other than Friday unless
  run with `--any-day` (the week's last session when Friday is a market holiday),
  and skips a stock whose last call is under 5 days old, so two calls' windows
  can't overlap. A cost check is therefore made on the first Friday itself, with
  the first stock or two, never on another day.
- **A missed call is a missed call.** A stock the spend limit (`--max-usd`, 3.0
  a run) or missing prices leaves without a call that Friday counts against
  condition 2, not as a Hold.
- **Known differences below:** the first bullet no longer applies to the API
  engine, which makes each role its own request (12 per stock). The stock's memory
  is its settled calls from either engine.

The text below is the plan as first written for the skill.

Written 2026-11-05, before any call is logged. Harsha has no API budget, so
TradingAgents' agents are played by Claude Code inside his Claude plan (the
`trading-agents` skill), with this repo's data tools in place of its own. This
test asks one question: do its calls beat the Nifty 50 over the next week, by
more than the cost of trading them, on dates nobody has seen?

## Why forward only

A language model may know what happened after a past date. Any backtest of an
LLM over dates before its training cut-off can look good for that reason
alone. The repo's earlier LLM news swarm showed no edge in the Jul to Oct 2026
walk-forward and was removed (commit `c1eef8d` has it). So every call here is
made on the day, and `swingbot-agent-calls record` refuses a past date.

## The test

- **Watchlist (fixed, in this order):** RELIANCE, HDFCBANK, ICICIBANK, INFY,
  TCS, ITC, LT, BHARTIARTL, SUNPHARMA, MARUTI. Ten liquid Nifty 50 stocks
  across energy, banks, IT, consumer, industrials, telecom, pharma and autos,
  picked for liquidity and spread, not for any view. Changing the list later
  starts a new test.
- **When:** every Friday session, after the close (16:00 IST or later), the
  whole list. If Friday is a holiday, the last session of that week. Weekly,
  not daily, so each call's 5-session window ends before the next call on the
  same stock starts: daily calls would overlap and make the t-statistic look
  stronger than it is. It also keeps the Claude usage to 10 runs a week.
- **The call:** the Portfolio Manager's rating on TradingAgents' 5-tier scale.
  Buy and Overweight count as long (+1), Sell and Underweight as short (-1),
  Hold as no position. A run with no readable rating is REVIEW: logged, not
  scored, and counted.
- **Score (TradingAgents' own):** the stock's close-to-close return from the
  first close after the call to 5 sessions later, minus the Nifty 50's over the
  same days, times the call's sign. `swingbot-agent-calls score` computes it from
  the repo's price files (Yahoo-adjusted closes).
- **Cost:** 0.40% a round trip, the figure the momentum study used, taken off
  each directional call's score at the judgement.

## How it is judged

- **When:** at the first Friday on or after 31 May 2027, or after 200 settled
  directional calls, whichever comes first. About 30 Fridays.
- **It passes only if all hold:**
  1. The mean scored return per directional call, after the 0.40% cost, is
     above zero with a t-statistic of at least 2.
  2. Calls were made on at least 90% of the Fridays, for the whole list each
     time, and no call was rerun or dropped.
  3. REVIEW runs are under 10% of all runs.
- **No early stop on returns.** It stops early only if the process breaks:
  missing data the skill can't report as a gap, or Fridays missed.
- **If it passes,** the next step is a separate plan for paper trading its
  calls through the risk engine (`risk-check`), not real money. **If it
  fails,** the skill stays as a research tool and nothing trades on it.

## Known differences from TradingAgents

- One model plays every role, so the debate is less independent than separate
  LLM calls. Each role is written separately, from only what TradingAgents
  would give it, to limit that.
- No Reddit or StockTwits data: the sentiment analyst works from news tone
  only and reports social sentiment as a gap.
- No FRED or prediction-market data: the macro backdrop is the repo's market
  mood (Nifty trend, FMCG, gold, USD/INR, India VIX).
- Its memory is the stock's own settled calls from `swingbot-agent-calls score`, read
  by the Portfolio Manager, as TradingAgents does with its memory log.
