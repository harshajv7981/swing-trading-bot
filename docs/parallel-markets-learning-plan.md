# Parallel NSE and US Trading with Alpaca

Status: planning document. No live trading changes are authorized by this document.

Written 2026-11-07.

## Build status (7 Nov 2026)

| Step | State |
|---|---|
| 1. Freeze plan, US paper-trial preregistration | Plan committed. Preregistered in [research/us-momentum-prereg.md](research/us-momentum-prereg.md): S&P 500 12-1 momentum, top 20, monthly, $100,000 of paper money, judged against RSP, SPY and SPMO. Its code and replay check come next. |
| 2. Market profiles and run namespaces | Built: `swingbot.markets` (profiles, `config/us_holidays.toml`, `RunSpace`, paper/shadow/live modes, global and per-market halt files). NSE behaviour unchanged. |
| 3. Normalized contracts and conformance tests | Built: `OrderIntent`, `OrderStatus`, `OrderUpdate`, `Fill`, `Position`; `tests/test_adapter_conformance.py` runs against the in-memory reference adapter and Alpaca. |
| 4. US historical research | Built: `swingbot-us-research` (frozen Alpaca panel with hash and manifest, S&P 500 members rebuilt for each month-end, four fixed strategies against equal weight, SPY, RSP and SPMO, by year with costs, turnover and worst fall). Pre-registered in [research/us-research-prereg.md](research/us-research-prereg.md). Runs in GitHub Actions (`us-research.yml`). |
| 5. Alpaca paper adapter behind a flag | Built. Phase 2 check on the real paper account: `swingbot-markets alpaca-smoke` (quiet: accept, duplicate id refused, lookup, cancel, no phantom fills; fill: buy and sell 1 SPY, one fill recorded once, position reconciled). Runs in Actions (`smoke-quiet` by hand; `smoke-fill` each weekday at 15:45 UTC until it passes once). Not built: bracket orders, cancel-replace, regulatory-fee activities (the trial uses none of them). |
| 6. Parallel paper trials | Running, in parallel and independently: NSE M6 (launchd on the Mac; waiting for eod2_data to reach the 30 Oct close) and US M12 (GitHub Actions; first booking 30 Nov 2026). `swingbot-markets trials` gives one monthly record for both and a report that compares returns only. See [research/parallel-trials.md](research/parallel-trials.md). |
| 7. Episodes, mistakes, market-scoped memory | Built: `swingbot-markets memory` (`src/swingbot/markets/memory.py`). One episode per month-end booking, settled when the next month books; the plan's ten mistake categories (the classifier names only what the record proves; judgement categories need a reviewer and evidence); lessons kept separate per market, an `nse` store for Indian stocks and a `us` store for US stocks, with no shared or combined store (Harsha's decision, 7 Nov 2026), append-only, proposed by anyone or by the repeated-mistake rule, approved only by a named person. US updates after each booking in Actions; NSE runs `swingbot-markets memory update --market nse` on the Mac. Nothing reads lessons into a decision yet: that is step 8. |
| 8. Candidate skills, replay and approval gates | Built: `swingbot-markets skills` (`src/swingbot/markets/skills.py`), a registry per market (`nse`, `us`), append-only. Version 1 of each is the running trial's rule, approved by its pre-registration. Candidates only from evidence (first: the phase 1 study's answers). Gates: a replay on the frozen panel (more return, a worst fall no more than 5 points deeper, better in 60% of years) allows shadow; 6 forward shadow months and a named person allow approval, which retires the old version. Never changes a running trial. US replays run in Actions (`us-research.yml`, task `skills`); NSE replay is not wired yet. Shadow months are recorded by hand until step 9 wires them into the bookings. |
| 9-11 | Not started. They need forward paper data or approval. |

Phase 0 acceptance: existing NSE tests pass unchanged; an NSE run and an empty
US run can be created without shared state; an Alpaca key alone sends nothing,
because the adapter also needs `enabled = true`, paper mode and no halt file.

## Objective

Run two market-specific trading workers in parallel:

- **NSE worker:** existing Indian-equity research, paper book, and Dhan path.
- **US worker:** new US-equity research, paper book, and Alpaca paper/live path.

Both workers should use similar decision stages and risk discipline, while respecting the different market rules. They should learn from completed trades and repeated mistakes without allowing an unverified change to alter live execution.

The target is not one universal trading brain. The target is a shared trading platform with two bounded market specialists and a controlled learning loop.

## External research

### Alpaca

Alpaca's current Trading API and Market Data API support US stocks and ETFs, paper/live accounts, historical data, streaming data, and order APIs. The basic equity data plan is limited to IEX real-time data; broader US exchange coverage requires the appropriate subscription.

Sources:

- [Alpaca Trading API](https://docs.alpaca.markets/docs/trading)
- [Alpaca Market Data API](https://docs.alpaca.markets/docs/market-data)
- [Alpaca Python SDK](https://github.com/alpacahq/alpaca-py)

Alpaca is therefore a good US execution and data adapter, but it is not an NSE execution solution. The US worker must have its own universe, calendar, timezone, currency, costs, tax assumptions, corporate-action handling, and benchmark.

### Hermes Agent

Nous Research's Hermes Agent describes a closed learning loop with:

- agent-curated persistent memory;
- autonomous skill creation after complex tasks;
- skills that improve during use;
- session search and summarization;
- delegated parallel workers.

Source: [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent).

The useful architectural idea is the separation of **experience**, **memory**, and **procedural skills**. This project should borrow that pattern without copying the claim that an agent is automatically doing safe reinforcement learning. A trading system needs explicit evaluation, versioning, and promotion gates.

## Current repository fit

The existing repository already provides useful foundations:

- [src/swingbot/broker/base.py](../src/swingbot/broker/base.py): shared broker protocol.
- [src/swingbot/broker/paper.py](../src/swingbot/broker/paper.py): paper execution and OCO behavior.
- [src/swingbot/broker/dhan.py](../src/swingbot/broker/dhan.py): NSE/Dhan adapter.
- [src/swingbot/paper/ledger.py](../src/swingbot/paper/ledger.py): append-only paper ledger.
- [src/swingbot/data/router.py](../src/swingbot/data/router.py): point-in-time data access.
- [src/swingbot/agents_trial.py](../src/swingbot/agents_trial.py): resumable agent decisions and trial-local state.
- [src/swingbot/agent_calls.py](../src/swingbot/agent_calls.py): append-only decisions and delayed scoring.
- [docs/algo-trading-path.md](algo-trading-path.md): sandbox, shadow, and live safety gates.

The current domain model is NSE-specific in several places: IST, INR, NSE holidays, Dhan ticket fields, Indian price bands, EQ/BE series, Indian data sources, and NSE universes. An Alpaca adapter should not be added as a thin conditional branch around those assumptions.

## Proposed architecture

```text
                         Shared research and control plane
       data contracts | signal schema | risk interfaces | audit | evaluation
                                      |
                 +--------------------+--------------------+
                 |                                         |
          NSE market profile                         US market profile
       IST, INR, NSE, Dhan                         ET, USD, US, Alpaca
                 |                                         |
          NSE data worker                              US data worker
          NSE strategy set                             US strategy set
          NSE paper ledger                             US paper ledger
          Dhan shadow/live                             Alpaca paper/live
                 |                                         |
                 +--------------------+--------------------+
                                      |
                       isolated learning and promotion plane
             market memory | shared lessons | skill versions | gates
```

### Shared core

Keep these concepts shared where they are genuinely market-neutral:

- normalized bar and quote schemas;
- signal and forecast records;
- order intent and fill events;
- position and portfolio interfaces;
- append-only event ledgers;
- point-in-time/as-of data contracts;
- backtest and walk-forward evaluation;
- risk metrics, audit logs, and kill switches;
- agent task, memory, and skill metadata.

### Market-specific profiles

Create explicit profiles rather than scattered conditionals. Each profile should define:

- timezone and sessions;
- holiday calendar and early closes;
- currency and notional precision;
- symbol normalization;
- universe and benchmark;
- data vendor and entitlements;
- price tick and price-band rules;
- fees, taxes, and slippage assumptions;
- corporate actions;
- order types, time-in-force, and bracket/OCO semantics;
- broker adapter and credentials source.

Candidate profiles:

- `nse_equities`: existing behavior, preserved first.
- `us_equities`: Alpaca symbols, USD, US sessions, US costs, and US corporate actions.

## Parallel execution model

Use one scheduler and two independent market runs. They may execute concurrently, but they must not share mutable trading state.

Each run gets its own:

- run ID and market profile;
- frozen configuration;
- data snapshot or data watermark;
- signal artifacts;
- orders and fills;
- portfolio ledger;
- memory namespace;
- skill version;
- promotion status.

Suggested run layout:

```text
runs/markets/nse/<run-id>/...
runs/markets/us/<run-id>/...
data/state/markets/nse/...
data/state/markets/us/...
```

The NSE and US workers should be independently stoppable. A Dhan outage must not prevent the US paper worker from completing, and an Alpaca outage must not alter NSE decisions.

### Time coordination

- NSE decisions run around NSE close and the existing month-end/weekly schedule.
- US decisions run around US close and US market open as required by the strategy.
- Store all event timestamps in UTC, with market-local timestamps retained for display and decision rules.
- Never use the host machine timezone as a trading rule.
- Do not let a US close become visible to an NSE decision or vice versa unless the strategy explicitly declares that cross-market feature.

## Alpaca integration phases

### Phase 0: contracts and configuration

- Add a market profile abstraction without changing NSE behavior.
- Add USD and US timezone support to shared value objects.
- Define a normalized `OrderIntent`, `OrderStatus`, `Fill`, and `Position` contract.
- Keep broker credentials in environment variables or a secret manager.
- Add an explicit `paper`, `shadow`, and `live` mode.
- Add a global and per-market halt file.

Acceptance criteria:

- Existing NSE tests pass unchanged.
- An NSE run and an empty US run can be created without shared state.
- No code path can send an order merely because an Alpaca key exists.

### Phase 1: US data and research-only worker

- Add Alpaca historical data support, or begin with a frozen US data source for reproducible studies.
- Add a US universe and benchmark.
- Implement US session and early-close handling.
- Reuse the existing walk-forward evaluation shape.
- Port only market-neutral strategies first: momentum, trend, and forecast ranking.
- Include delisted/security survivorship limitations in the study documentation.

Acceptance criteria:

- Point-in-time US panels can be regenerated.
- The same strategy produces reproducible results from the same frozen inputs.
- Results include costs, slippage, turnover, drawdown, and a simple US benchmark.

### Phase 2: Alpaca paper adapter

Implement the broker protocol for Alpaca:

- submit entry orders;
- cancel and replace orders;
- submit and cancel protective exits;
- close positions safely;
- sync orders, fills, and positions;
- deduplicate events;
- handle partial fills, rejects, cancellations, expirations, and market gaps;
- map Alpaca order IDs to internal IDs;
- support idempotent retries;
- record raw broker responses for audit.

Start with paper trading only. Bracket orders should be used only after their behavior is verified against the internal OCO/stop contract.

Acceptance criteria:

- Fake-client tests cover all adapter paths.
- Alpaca paper orders reconcile to the internal ledger.
- Re-running sync does not duplicate fills.
- A stale or malformed broker response fails closed.
- Live mode is rejected by code until separately approved.

### Phase 3: parallel paper trials

Run fixed, preregistered trials rather than changing strategies while looking at results.

For each market, record:

- strategy and version;
- signal timestamp and information cutoff;
- selected universe;
- forecast and confidence;
- intended orders;
- actual paper fills;
- realized return after costs;
- benchmark return;
- drawdown and risk exposure;
- reason for the decision;
- any data or execution anomaly.

Compare each strategy against a simple market-appropriate benchmark. Do not compare raw NSE rupees with US dollars; compare normalized returns and risk metrics.

### Phase 4: shadow execution

For each market separately:

- produce the exact orders that would be sent;
- query broker state;
- reconcile positions and cash;
- report differences;
- require explicit approval before any live send.

The shadow output must be stable enough that a human can review it. No autonomous promotion to live execution is allowed.

### Phase 5: limited live capital

Only after the relevant market passes its own paper and shadow gates:

- unlock one market at a time;
- use a small fixed capital cap;
- keep a separate live ledger;
- require a per-market kill switch;
- cap order count, notional, turnover, and daily loss;
- alert on every reject, partial fill, reconciliation difference, and stop failure;
- review after a fixed number of live cycles before increasing capital.

## Memory and learning design

### Memory namespaces

Use four levels of memory:

1. **Market memory:** NSE or US facts, broker behavior, session quirks, and validated local rules.
2. **Strategy memory:** strategy-specific performance, known failure regimes, and tested parameter ranges.
3. **Trade episode memory:** one decision, its evidence, expected outcome, realized outcome, and error classification.
4. **Shared lessons:** only general principles that have survived validation in more than one market or are clearly market-neutral.

A US broker rejection must never become an NSE rule. An NSE price-band lesson must never be applied to US orders. Shared memory should contain only tagged, confidence-rated lessons.

### Skills

Treat a skill as a versioned procedural document or executable policy, not as an untracked prompt mutation.

Examples:

- `nse-month-end-momentum-v1`
- `us-weekly-momentum-v1`
- `alpaca-order-reconciliation-v1`
- `stale-data-refusal-v1`
- `post-trade-review-v1`

Each skill records:

- scope: `nse`, `us`, or `shared`;
- owner and creation reason;
- inputs and expected outputs;
- evidence that motivated it;
- tests and backtest IDs;
- performance before and after;
- status: `candidate`, `shadow`, `approved`, `retired`;
- approval and supersession history.

The agent may propose a new skill after repeated evidence, but it may not silently replace an approved live skill.

### Mistake taxonomy

Every completed decision should be classified when evidence allows:

- data leakage or incorrect cutoff;
- bad universe membership;
- forecast error;
- regime mismatch;
- overconfidence or poor uncertainty calibration;
- execution error;
- broker/API error;
- risk-policy violation;
- market-specific assumption incorrectly reused;
- expected loss with correct process.

The last category matters. A losing trade is not automatically a mistake, and a profitable trade is not automatically a good decision.

### Closed learning loop

```text
observe -> decide -> paper/shadow/live -> settle -> score -> classify ->
store episode -> propose lesson/skill -> replay and walk-forward -> approve ->
shadow new version -> promote or reject
```

The loop must be append-only and reproducible. A later model must be able to explain which memory and skill version influenced a decision.

## Reinforcement learning boundary

Do not begin with online reinforcement learning that changes weights or execution rules using live money. Financial rewards are delayed, noisy, non-stationary, and vulnerable to backtest overfitting.

A safer progression is:

1. **Case-based memory:** retrieve similar historical market states and postmortems.
2. **Offline policy evaluation:** test candidate rules on historical, frozen data.
3. **Shadow selection:** compare candidate strategy weights without sending orders.
4. **Contextual bandit later:** select among already-approved strategies using a bounded action set and risk-adjusted reward.
5. **Promotion gate:** require out-of-sample improvement, stability, and human approval.

A first bandit action space could select weights among approved NSE strategies or US strategies, not invent new order types. Rewards should be net return adjusted for drawdown, turnover, and risk breaches, with penalties for data-quality or execution violations.

No learning component may:

- bypass risk limits;
- change capital caps;
- disable a kill switch;
- alter broker credentials;
- submit an unapproved order type;
- train on future information;
- mix market-specific data without an explicit feature contract.

## Shared versus separate learning

### Safe to share

- point-in-time data discipline;
- event and audit schemas;
- post-trade review format;
- uncertainty calibration methods;
- generic retry/idempotency patterns;
- principles for refusing incomplete data;
- evaluation and promotion workflow.

### Keep separate initially

- price and volume features;
- market calendars and session cutoffs;
- universe construction;
- tax and cost models;
- portfolio sizing parameters;
- stop and tick rules;
- broker behavior;
- strategy parameters;
- memory retrieval results;
- performance baselines.

Cross-market transfer should be an explicit experiment, with a feature manifest and an ablation showing whether the transferred lesson helps or harms.

## Risk controls

- Paper first, shadow second, live last.
- Separate credentials and ledgers for NSE and US.
- Separate market-level kill switches plus one global kill switch.
- Reject orders when data is stale, incomplete, outside session, or unreconciled.
- Reconcile broker positions before every new order batch.
- Cap total exposure per market and combined exposure.
- Keep raw broker responses and normalized events.
- Never let an LLM directly construct an unvalidated broker payload.
- Require deterministic validation between agent output and order submission.
- Keep the existing Dhan live refusal until its documented approval path is complete.
- Treat Alpaca live mode as blocked until the paper and shadow gates pass.

## Observability

Create one dashboard/report with separate NSE and US sections showing:

- current market mode: paper, shadow, or live;
- open positions and cash;
- pending orders and broker reconciliation status;
- latest signal and skill versions;
- data watermark and staleness;
- daily and cumulative PnL;
- drawdown and exposure;
- strategy-level attribution;
- memory lessons proposed, approved, rejected, or retired;
- unresolved anomalies.

Every decision should be answerable with:

- what data was visible;
- which market profile was active;
- which strategy and skill versions ran;
- what the agent believed;
- what order was intended;
- what the broker did;
- what happened afterward;
- whether the result changed any approved knowledge.

## Initial implementation order

1. Freeze this plan and define the US paper-trial preregistration.
2. Add market profiles and run namespaces without changing NSE behavior.
3. Add normalized order/fill contracts and adapter conformance tests.
4. Build US historical research and benchmark reports.
5. Implement Alpaca paper adapter behind a feature flag.
6. Run independent NSE and US paper trials in parallel.
7. Add episode records, mistake taxonomy, and market-scoped memory.
8. Add candidate skill generation with replay and approval gates.
9. Add shadow reconciliation for both brokers.
10. Consider bounded strategy-selection/bandit experiments only after enough forward paper data exists.
11. Approve live execution separately for each market.

## Definition of done

This project is ready for a cautious live pilot only when:

- NSE behavior remains regression-tested and unchanged by the US work;
- Alpaca paper reconciliation is reliable;
- both markets have independent paper and shadow histories;
- every decision is reproducible from frozen inputs;
- memory and skills are scoped, versioned, and auditable;
- no skill or policy changes reach live execution without promotion;
- the US and NSE strategies each meet their own preregistered performance gates;
- broker, data, risk, and kill-switch failures have been tested;
- live capital limits and human approval procedures are documented.

The central rule is: **parallel execution, shared engineering, isolated trading knowledge, and controlled promotion.**
