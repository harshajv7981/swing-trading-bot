---
name: swarm-backtest
description: Run the persona swarm backtest with Claude Code itself playing the market personas, no Anthropic API key needed. Use when asked to run the swarm, backtest news with Claude, or score news events in this repo.
---

# Swarm backtest, played by Claude Code

You are the swarm. Instead of calling the Anthropic API, you read a news-only
brief, play each market persona yourself, write their views to a file, and then
run the scorer. Nothing here talks to a broker or places orders.

## Inputs

- An events file (CSV or JSONL). Default to `data/sample/events.csv` if the user
  names none.
- A prices directory for scoring. Default `data/sample/prices` with
  `--benchmark NIFTY50`.
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

4. **Repeat** steps 1 to 3 until `swingbot-briefs` reports 0 events left.
   Write views in batches so a long file doesn't have to fit in one go.

5. **Score it**, only now:

   ```bash
   swingbot-backtest --events <events> --prices <prices dir> --benchmark NIFTY50 \
       --engine recorded --views runs/views.jsonl
   ```

6. **Report** to the user: number of events scored, hit rate and its p-value,
   average return after costs, Brier score, and how it compares with the
   `lexicon` baseline on the same events
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
