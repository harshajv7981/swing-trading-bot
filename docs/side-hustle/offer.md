# Backtest audits and market-data engineering (draft offer)

Draft written 2026-11-14 for build 5 of
[roadmap-2026-11.md](../research/roadmap-2026-11.md). Fill in the
`[brackets]` before sharing.

**What I sell is code and checks, never stock picks.** I am not a
SEBI-registered research analyst or investment adviser. I give no buy or
sell recommendations, and every trading decision stays with the client.

## 1. Backtest audit (fixed price: [₹ amount])

You send a strategy and its backtest, as code or a written rule. Within
[N] working days you get a written report covering:

- **Look-ahead:** does any signal use data that wasn't known at the time?
- **Survivorship:** are delisted stocks missing, and how much could that
  flatter the result?
- **Overfitting:** how many variants were tried, the probability of backtest
  overfitting, and a deflated Sharpe ratio (Bailey & López de Prado).
- **Indian costs and tax:** STT, charges, slippage, and short-term against
  long-term capital gains on the strategy's real turnover.
- **The cheap alternative:** whether an index fund or ETF does the same job
  after tax.
- **A frozen re-test plan:** a pass bar written before any new data is seen.

The report states what each check found. It never says whether to trade.

## 2. NSE data and research pipelines (per project: [₹ rate])

- Point-in-time price pipelines from eod2, yfinance, or data you are licensed
  to use, with split adjustment and a holiday calendar.
- Backtests that include costs and tax, plus a ledger of every study tried, so
  results can be read against the number of attempts.
- Paper-trading harnesses with monitoring: freshness alerts, fill-shortfall
  logging, monthly reports.

## 3. LLM-agent evaluation (per project: [₹ rate])

Forward, pre-registered tests of LLM or agent decisions, with logged calls,
random-call baselines and a judgment date fixed in advance. These are the
methods used in this repo's two TradingAgents trials.

## How a project runs

1. A 30-minute scoping call, free.
2. A written scope with a fixed price or capped hours.
3. Code and report delivered. The client owns the code and every trading
   decision.

## Proof of work

Two case studies are in [case-studies.md](case-studies.md). Who to contact,
and how, is in [outreach.md](outreach.md). The process
behind them is the repo's [trials ledger](../research/trials-ledger.md):
every pre-registered study, including the failures.

Contact: [email / link]

---
*Before taking client money, have a CA or lawyer confirm that a given scope
(for example, a tool that ranks stocks for a client) doesn't count as
research-analyst or algo-provider activity under SEBI rules.*
