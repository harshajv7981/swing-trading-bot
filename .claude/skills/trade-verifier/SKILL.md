---
name: trade-verifier
description: Review closed swarm trades as the Verifier, write a lesson for each into the learning memory, and run the walk-forward test of the swarm with versus without those lessons. Use when asked to review trades, build or update the trade memory, learn from past trades, or measure whether memory helps.
---

# Verifier and learning memory, played by Claude Code

The memory has three parts, all in one local SQLite file (`runs/memory.db`):

1. **Ledger**: every closed trade, with the news, the swarm's call and reasoning,
   and what the price did. `swingbot-memory ingest` fills it from a backtest run.
2. **Lessons**: you, as the Verifier, review each closed trade and write one
   rule of thumb about it. `swingbot-memory learn` stores them.
3. **Retrieval**: `swingbot-briefs --with-lessons runs/memory.db` puts up to
   three lessons from similar past trades into each event of the swarm brief.
   A lesson only shows up for news published after its trade closed.

Nothing here trains model weights. The swarm "learns" by reading its own past
mistakes before each new call, the way a trader rereads a journal.

## Rules that keep the test honest

1. Everything from the `swarm-backtest` skill still applies when you play the
   swarm: no prices, no results files, no lookups, flag `[recalled]` events.
2. **As the Verifier, judge each trade from its own record in the Verifier
   brief.** Don't open `results.csv` directly and don't carry over what you saw
   in trades that closed later.
3. **Never review a trade before its window.** In the walk-forward below, only
   review with `--closed-before <window start>`. Reviewing everything up front
   and then playing the swarm would let lessons from the future into your head
   even though the briefs are gated.
4. Run `swingbot-memory audit` at the end. It must report 0 retrievals that saw
   the future; if not, tell the user and don't report the comparison.

## Writing lessons

```bash
swingbot-memory review --closed-before <date> --limit 15 --out runs/verify.md
```

Read `runs/verify.md` and append one line per trade to `runs/lessons.jsonl`:

```json
{"trade_id": "base:e01", "verdict": "right_for_wrong_reason", "lesson": "Big order wins in low-margin segments rose with the market, not on the order; check the index before crediting the news.", "tags": ["order win", "infra", "market beta"]}
```

- `verdict`: `right_for_right_reason`, `right_for_wrong_reason`, `wrong` or
  `no_call` (the swarm stayed neutral).
- `lesson`: one or two sentences naming a pattern, usable on a different stock.
- `tags`: two to five short words for the news type and sector.

Then `swingbot-memory learn --lessons runs/lessons.jsonl`. Re-running `learn`
on the same file is safe; a later line for a trade replaces the earlier one.

## Measuring it: with versus without memory

1. **Baseline.** Run the `swarm-backtest` skill as usual into `runs/views.jsonl`
   and score it. This is the "without memory" run. Note its `runs/<run>/`
   folder. Then record its trades:

   ```bash
   swingbot-memory ingest --results runs/<base run>/results.csv --run base --views runs/views.jsonl
   ```

2. **Walk forward**, one week at a time from the first event's date. For each
   window starting on date D:
   - `swingbot-memory review --closed-before D`, write lessons, `learn` them;
     repeat until it says 0 more need a lesson.
   - Play the swarm for that week's events only, with lessons in the brief:

     ```bash
     swingbot-briefs --events <events> --views runs/views-mem.jsonl --until <D + 7 days> \
         --with-lessons runs/memory.db --out runs/brief.md
     ```

     Append views to `runs/views-mem.jsonl` exactly as in the `swarm-backtest`
     skill (two persona rounds, then the debate). Repeat the brief until it
     reports 0 events, then move to the next window.

   The lessons all come from the baseline run's trades, so both runs face the
   same events and only the brief differs.

3. **Score and compare.**

   ```bash
   swingbot-backtest --events <events> --prices <prices dir> --benchmark NIFTY50 \
       --engine recorded --views runs/views-mem.jsonl
   swingbot-memory compare --without runs/<base run>/results.csv --with runs/<mem run>/results.csv
   swingbot-memory compare ... --only-with-lessons   # events that were shown a lesson
   swingbot-memory audit
   ```

4. **Report** both columns, how many calls changed, how many of those memory
   got better or worse, and the sign test p-value. Early events have no
   lessons yet, so most of any effect sits in `--only-with-lessons`. Say plainly
   when the number of changed calls is too small to mean anything (under about
   20 it almost always is), and when the data is the synthetic sample.

## Paper trading later

When paper trading runs, each closed bracket order becomes a
`swingbot.memory.ClosedTrade(mode="paper", closed_at=<fill time>, ...)` passed
to `MemoryStore.record_trade`. The review, learn and retrieval steps are the
same, and the time gate uses the real fill time instead of the 15:30 close.

## Limits to tell the user about

- One model plays the swarm and the Verifier in one session, so the walk-forward
  order is what keeps the future out, not separate processes. Follow it exactly.
- Retrieval is word overlap (TF-IDF) plus a same-stock bonus. It finds "bad
  loans" when the news says "bad loans", not when it says "NPAs". Good tags
  help; local embeddings are the upgrade if it misses too much.
