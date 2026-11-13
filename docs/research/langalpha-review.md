# LangAlpha review (2026-11-13)

Repo: https://github.com/ginlix-ai/LangAlpha (Apache 2.0, about 2.3k stars,
4,189 commits). Read from the GitHub pages and raw README/AGENTS.md only; no
code was cloned or run. File listings were partial, so some details are inferred.

## What it is
"Claude Code for financial markets": a self-hosted or hosted app where a lead
LLM agent plus subagents research stocks, write reports and Excel models, and
can place orders through a broker with approval. A product, not a strategy.

## Architecture
- FastAPI backend, Postgres, Redis streams, web and desktop clients.
- Agent built on LangChain `create_agent` with middleware
  ([src/ptc_agent/agent/agent.py](https://github.com/ginlix-ai/LangAlpha/blob/main/src/ptc_agent/agent/agent.py)).
- The model writes Python that runs in a sandbox (Docker, Daytona, E2B) and calls
  data tools exposed as MCP servers under `plugins/`.
- Five built-in subagents (research, general, data-prep, equity-analyst,
  report-builder), 35 skills, long-term memory, scheduled "wakeups" and price triggers.
- Orders go through a direct tool path so each call can be inspected and approved
  ([src/server/services/tool_binding.py](https://github.com/ginlix-ai/LangAlpha/blob/main/src/server/services/tool_binding.py)).
- Brokers: Robinhood, Interactive Brokers, moomoo, Webull. No Indian broker.

## Data and India coverage
- Self-hosted: Yahoo Finance (free), SEC EDGAR, optional FMP (free tier) for
  fundamentals, web search via Tavily/Exa etc.
- Hosted: live US equities and options. China A-shares "coming soon".
- No NSE or India mention anywhere. Its Yahoo plugin
  ([plugins/yfinance](https://github.com/ginlix-ai/LangAlpha/tree/main/plugins/yfinance))
  could in principle fetch `.NS` tickers, but nothing is built or tested for it,
  and SEC filings are useless for NSE. We already have a better NSE data router.

## Cost to run
- Software is free, but needs Docker, Postgres, Redis: heavy for one person.
- It does allow Claude or ChatGPT subscription sign-in, so a Claude plan could in
  theory power it. Unclear how far the plan's limits stretch with many parallel
  subagents. Optional paid pieces: sandboxes, search APIs, FMP paid tiers.
- Our skills already run inside Claude Code on the plan, at zero extra cost.

## Evidence of real performance
None. No backtest, no live track record, no returns. The README shows demo
reports (e.g. a Sell rating with a target price) and a not-financial-advice
disclaimer. AGENTS.md says nothing about evaluation or look-ahead control, which
is the exact thing our forward-only rule exists for.

## Worth borrowing (small ideas only, no code needed)
1. Per-call order gating: live orders only via an inspectable direct path, never
   free-form code. We already have `swingbot-risk` PASS/BLOCK; same principle,
   nothing new to add.
2. Wakeups on time, price move, or order event
   ([src/tools/wakeups/](https://github.com/ginlix-ai/LangAlpha/tree/main/src/tools/wakeups)):
   a possible model if we later want trailing-stop or entry alerts, but our
   existing cron and Claude Code scheduled tasks cover it.
3. Snapshot-locking tool descriptions so edits fail tests
   (`tests/unit/mcp_servers/agent_docstring_lock.json`): a cheap way to stop the
   `swingbot-data` and skill wording drifting mid-test. Optional.

## Skip
- The whole stack (Postgres, Redis, sandboxes, web/desktop apps, chat channels).
- Its 35 research skills and report/Excel builders: US-centric, and report
  quality is not what our forward tests measure.
- Subagent swarm: same shape as the removed news swarm that showed no edge.
- Brokers and hosted service: none for India, and we are paper-only.
- Adding it mid-test would change the tooling and invalidate the TradingAgents
  forward tests, which are fixed in their pre-registrations.

## Verdict
Skip. Nothing to adopt: no NSE data, no evidence of edge, heavy infrastructure,
and it duplicates what our Claude Code skills and `swingbot-risk` already do.
At most, borrow the docstring-lock idea later.
