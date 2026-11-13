# Review: HKUDS/AI-Trader (read 2026-11-13, web pages only, nothing run)

Verdict: SKIP the framework. Borrow two ideas, no code. See the end.

## What it is
Two things share one name. (1) A paper and benchmark (arXiv 2512.10971, Dec 2025)
that lets six LLMs trade Nasdaq-100, SSE-50 (China A-shares) and crypto with
fake money. (2) Since 2026 the repo is mostly ai4trade.ai, a social site where
agents (Claude Code, Codex, OpenClaw...) post signals, follow and copy each
other, and earn points. It is MIT licensed.
Links: https://github.com/HKUDS/AI-Trader , https://arxiv.org/abs/2512.10971

## Architecture
- Benchmark: each model gets 5 tools (price, search, news, math, trade) and only
  "minimal information", then decides on its own. US hourly, A-shares and crypto daily.
- Platform: FastAPI backend plus React front end (service/), Postgres or SQLite,
  background workers for prices and settlement. Agents join by reading
  skills/ai4trade/SKILL.md and calling HTTP endpoints. The skill needs no LLM
  of its own and the agent polls a heartbeat every 30-60 seconds.
- Copy trading and broker sync: Binance, Coinbase, Interactive Brokers.
  Nothing for Indian brokers.

## Cost to run
- The benchmark needs paid LLM APIs (GPT-5, Claude, Gemini, Qwen, DeepSeek) and
  a search/news feed. US prices come from Alpha Vantage, with a yfinance
  fallback added June 2026. The paper does not say how many LLM calls a day.
  Rough guess: hourly decisions on a 100-stock list is many calls a day, far
  above our 10 stocks a week.
- The platform route is free to join, but it is a hosted service run by others,
  and the agent still has to be driven by an LLM. We have no API budget.
- Claude Code can sit at the agent end, but that adds nothing we lack: our
  trading-agents skills already play the agent roles inside the Claude plan.

## India fit
Poor. Markets covered are US, China A-shares, crypto, Polymarket. No NSE data,
no NSE calendar or hours, no Indian brokers. We would have to write the
data adapter ourselves, and then little of the repo would remain.

## Evidence of performance, and how it was measured
- Window: 1 Oct to 7 Nov 2025 for stocks (about 5.5 weeks), 14 days for crypto.
  Six models, one run each. No significance test, no confidence interval, no repeats.
- US: QQQ +1.87%. MiniMax-M2 +9.56%, DeepSeek +8.39%, Claude 3.7 +3.11%,
  GPT-5 +1.56%, Qwen +0.39%, Gemini -0.06%. Drawdowns were deeper than QQQ's.
- A-shares: no model beat the index. Crypto: only DeepSeek beat the index, and
  it still lost 12%.
- Look-ahead: the paper claims "strict temporal filtering" of search and price
  to the simulated time, with no detail. The run dates (Oct-Nov 2025) were
  after most tested models' training cut-offs, which is the real protection.
  Our rule is forward-only, and this is a 5-week run with no stats, so it
  is not evidence of edge. Mostly the paper says "most agents are poor".
- Live platform: leaderboards rank by mark-to-market return and risk score.
  Synced trades use the agent's own price and time, with no check on backdating,
  so the board cannot be trusted as a performance record.

## Worth borrowing (ideas only)
1. Benchmark against the plain index per run, and report drawdown or Sortino
   beside return (paper's Table). Our TradingAgents prereg already scores
   excess return over the Nifty 50. Add max drawdown to the final write-up.
   Free.
2. The "minimal information" framing: give the agent only the data it
   fetches itself, so a good result is not due to hand-fed context. Our
   skills already restrict inputs, so just keep it that way.
3. Challenge-window rule: trades only count if time-stamped by the server
   inside the window. Our `swingbot-agent-calls record` already refuses past
   dates, so this confirms the design.

## Skip
- ai4trade.ai signals and copy trading: crowd signals of unknown quality,
  US and crypto only, spoofable timestamps, and it sends our views to a
  third-party site.
- The points economy, the 4,000-agent "competition and cooperation" research
  pipeline, the React front end, the broker sync.
- Their agent loop: our two TradingAgents tests already cover the same idea
  with better look-ahead control, and no API cost.

## One-line verdict
Skip. It is US/China/crypto only, needs paid LLM APIs, and its evidence is
a 5-week single-run benchmark with no stats; borrow only the habit of
reporting drawdown next to excess return versus the index.
