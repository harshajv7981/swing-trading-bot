---
name: swarm-backtest
description: Run the persona swarm backtest with Claude Code itself playing the market personas, no Anthropic API key needed. Use when asked to run the swarm, backtest news with Claude, or score news events in this repo.
---

# Swarm backtest, played by Claude Code

You are the swarm. Instead of calling the Anthropic API, you read a news-only
brief, play each market persona yourself, write their views to a file, and then
run the scorer. Nothing here talks to a broker or places orders.

## Inputs

- An events file (CSV or JSONL). Use `data/raw/news/events.csv` if it exists
  (built by the fetchers in `scripts/`, see the README), otherwise
  `data/sample/events.csv`. Briefs and the scorer drop news from before the
  training cutoff (1 July 2026 by default); tell the user how many were dropped.
- A prices directory for scoring: `data/raw/prices` for real news (fill it with
  `scripts/fetch_prices.py --events <events>`), `data/sample/prices` for the
  sample. Use `--benchmark NIFTY50` with either.
- A views file to write: `runs/views.jsonl` unless the user names another.

If `swingbot-briefs` is not on the PATH, run `pip install -e .` first.

## Rules that keep the test honest

These matter more than anything else in this skill. Breaking them makes the
result worthless, and nobody will be able to tell.

1. **Do not look at prices or results before every view is written.** Do not open
   anything under the prices directory, `runs/*/results.csv` or `summary.json`,
   and do not run `swingbot-backtest` until the views file is complete.
2. **Do not look anything up.** No web search, no web fetch, no reading other
   files about these companies. Judge only from the brief.
3. **Do not use what you remember about how the stock actually moved** after the
   news date. If you recognise the event and know the outcome, say so to the
   user and mark that event's rationale with `[recalled]` so it can be excluded.
4. Write each persona's view from that persona's point of view, not from what you
   think is the "right" answer.

## Steps

1. **Make a brief** for the next batch of events that have no views yet:

   ```bash
   swingbot-briefs --events <events> --views runs/views.jsonl --limit 10 --out runs/brief.md
   ```

   Read `runs/brief.md`. It holds the persona descriptions and the events.

   If the user wants the swarm to see charts as well as news, add
   `--with-technicals <prices dir>`. Each event then carries a technical
   snapshot (trend, RSI, MACD, Bollinger, ATR, volume) built only from sessions
   that had closed when the news broke. Reading that snapshot in the brief is
   fine; opening the prices directory yourself is still not. Keep news-only and
   with-technicals runs in separate views files, e.g. `runs/views-tech.jsonl`,
   so they can be compared.

   `--with-context` also adds each stock's earlier news and filings from the
   past week and its latest results filing, all taken from the events file and
   cut to the moment the story broke. Never run `swingbot-data` during a
   backtest: the brief is the only data you may use, because it is the part
   that is cut to the right time.

1b. **Analyst reports** (only when the brief has data sections). For each event,
   follow the analyst section of the brief and append one line per report:

   ```json
   {"event_id": "e01", "role": "analyst", "analyst": "market", "report": "Downtrend into the news: close 4.8% under EMA10, RSI 15, near the lower Bollinger band."}
   ```

   `analyst` is `market`, `news` or `fundamentals`. In rounds 1 and 2 the
   personas read these reports, not the raw data sections.

2. **Round 1.** For every event in the brief, write one view per persona, each
   formed on its own without considering the other personas. Append one JSON
   object per line to `runs/views.jsonl`:

   ```json
   {"event_id": "e01", "persona": "Retail Bull", "round": 1, "stance": 0.7, "conviction": 0.6, "rationale": "Record order, momentum crowd will pile in."}
   ```

   - `persona` must match the brief's names exactly: `Retail Bull`,
     `Institutional Bear`, `Foreign Institutional Investor`, `Financial Media`.
   - `stance` is -1.0 (expects a fall) to 1.0 (expects a rise) over 48 to 72 hours.
   - `conviction` is 0.0 to 1.0.
   - `rationale` is one or two sentences in the persona's voice.

3. **Round 2.** For each event, re-read the four round 1 views and write a round 2
   view per persona, letting each one react to the others the way that persona
   would. A retail bull is easily swayed; an institutional bear barely moves.
   Append these as `"round": 2`. The scorer uses the last round per event.

3b. **Bull/Bear debate and judge.** For each event, follow the debate section at
   the end of the brief: write a Bull researcher argument, a Bear researcher
   argument that rebuts it, then the judge's verdict. Append:

   ```json
   {"event_id": "e01", "role": "bull", "argument": "Record order adds a year of revenue; retail and media are both leaning in."}
   {"event_id": "e01", "role": "bear", "argument": "Highway contracts are low margin and the stock is already stretched."}
   {"event_id": "e01", "role": "judge", "stance": 0.4, "conviction": 0.55, "rationale": "The order size outweighs margin worries over three days."}
   ```

   The judge's call becomes the signal for that event. Base every argument on
   the brief and the persona views; never invent figures.

4. **Repeat** steps 1 to 3b until `swingbot-briefs` reports 0 events left.
   Write views in batches so a long file doesn't have to fit in one go.

5. **Score it**, only now:

   ```bash
   swingbot-backtest --events <events> --prices <prices dir> --benchmark NIFTY50 \
       --engine recorded --views runs/views.jsonl
   ```

   Then score the same file without the debate, so the judge can be compared
   with the plain persona crowd: add `--crowd-only` to the same command.

6. **Report** to the user: number of events scored, hit rate and its p-value,
   average return after costs, Brier score, for the judge and for the crowd
   (`--crowd-only`), and how both compare with the `lexicon` baseline on the same
   events
   (`swingbot-backtest ... --engine lexicon`). Say plainly when the sample is too
   small to mean anything, or when it is the synthetic sample. Mention any events
   you marked `[recalled]`.

## Limits to tell the user about

- One model plays all four personas in one context, so their views are less
  independent than four separate calls. For more independence, write round 1
  for one persona across all events before moving to the next persona.
- It runs only while someone has a Claude Code session open. It cannot run on a
  schedule unattended the way an API-backed engine could.
- It uses the user's Claude plan limits. Around 10 events per batch keeps each
  batch manageable.
