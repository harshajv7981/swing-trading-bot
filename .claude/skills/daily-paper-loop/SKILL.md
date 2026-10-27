---
name: daily-paper-loop
description: Run one paper trading day end to end with swingbot-daily (data refresh, paper broker settle, account, kill switch, mood, swarm on today's news, orders, risk gate, tickets, ledger report, Verifier). Use when asked to "run my day", "run the daily loop", "do today's paper trading", "morning run" or "/daily-paper-loop". Paper only; never places a real order.
---

# The daily paper trading loop, run by Claude Code

One run per NSE session day, best started between 08:00 and 09:00 IST so the
DAY entries are in before the 09:15 open. You run two commands and play the
swarm (and later the Verifier) in between. Everything a day produces is in
`runs/daily/<date>/`, and `summary.md` there is the day's report.

If `swingbot-daily` is not on the PATH, run `pip install -e ".[data]"` first.

## Rules

1. **Never reset the kill switch, never edit `config/risk.toml`,
   `config/nse_holidays.toml` or `config/daily.toml`, and never edit the
   ledger, the verdict log or a ticket by hand.** If something blocks, report
   it as printed and stop that part of the day.
2. **The gate decides.** A BLOCK is final for that order. Don't change an
   order and check it again to get a PASS.
3. **Fills are what the paper broker did, not what was planned.** If the loop
   says the paper broker is a stub, tell Harsha that nothing filled by itself,
   and record a fill with `swingbot-paper fill` only when Harsha gives you
   the price.
4. **Play the swarm from the brief only**, under the `swarm-backtest` skill's
   rules: no prices beyond what the brief shows, no web lookups for this
   news, each persona in its own voice. This is a live call, so there is no
   future to leak, but a brief-only view keeps paper trades comparable with
   the backtest.
5. Don't run `swingbot-daily start` twice in one day without saying so: the
   second run rewrites `day.json` and the brief.

## Steps

1. **Start the day.**

   ```bash
   swingbot-daily start
   ```

   Read every line it prints. It checks, in order: the NSE calendar and the
   time, the data refresh, the paper broker's fills and exits, the paper
   account, the weekly kill switch (it latches it on a 5% weekly loss, never
   resets it), and the market mood. Then it writes the swarm brief for news
   published since the previous session opened.

   If it says **no new entries today** (a weekend or holiday, after the
   close, no paper account, or a latched kill switch), skip to step 4: exits
   and the report still matter.

2. **Play the swarm** on `runs/daily/<date>/brief.md`, appending to
   `runs/daily/<date>/views.jsonl` exactly as in the `swarm-backtest` skill:
   analyst reports when the brief has data sections, two persona rounds, then
   the Bull/Bear debate and the judge. Then

   ```bash
   swingbot-daily brief
   ```

   for the next batch, until it reports 0 events. Watch the clock: if it is
   past 09:00, tell Harsha the entries will go in after the open.

3. **Finish the day.**

   ```bash
   swingbot-daily finish
   ```

   It turns the views into signals, hands them to the signal-to-order step,
   runs `swingbot-risk check` on each order (each one sees the entries already
   PASSed today) and `swingbot-risk ticket` on each PASS, gives the tickets to
   the paper broker, writes the ledger report, and puts trades that closed
   into the learning memory.

4. If you skipped step 2 and 3 because there were no entries, still run
   `swingbot-daily finish` for the report and the Verifier.

5. **The Verifier.** If finish says trades need a lesson, follow the
   `trade-verifier` skill on `runs/daily/<date>/verify.md`: one lesson line
   per trade in `runs/lessons.jsonl`, then
   `swingbot-memory learn --lessons runs/lessons.jsonl`.

## What to tell Harsha

Lead with the one thing that matters most today: a latched kill switch, a
rule break in the report, tickets to place, or "no trades today, because ...".
Then, briefly:

- the account line (equity, return against the Nifty, drawdown) and the paper
  gate progress, as printed;
- each signal (stock, call, confidence) and what happened to it: no order,
  BLOCK with the failing rules as printed, or PASS with the ticket line;
- every STUB or FAILED step, in one line each, so a stub never reads as done;
- any `note` lines, and any stop the report says was traded through.

Link `runs/daily/<date>/summary.md` for the rest.

## Timing on the Mac

- The loop has no scheduler: Claude Code plays the swarm, so someone has to
  open a session. Start it from the repo folder in the morning.
- The data refresh (`swingbot-refresh`) is meant to run after 16:00 IST for
  the day's closing bars; running it in the morning still fills in every
  session that has closed. Bars for a session still in progress are never
  saved, so a morning run trades on yesterday's close.
