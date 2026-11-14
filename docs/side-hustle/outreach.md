# Outreach plan (draft)

Written 2026-11-14 to go with [offer.md](offer.md) and
[case-studies.md](case-studies.md). Fill in the `[brackets]`, then send
everything yourself. Nothing here has been sent.

**The six-week test** from [roadmap-2026-11.md](../research/roadmap-2026-11.md):
by six weeks after the first message, fewer than 3 serious enquiries and no
paid project means narrowing the pitch (for example, to registered advisers
only). Keep the pitch the same until then, so the test means something.

## Who to approach, in order

| # | Who | Why they might pay | Where to find them | Lead with |
|---|---|---|---|---|
| 1 | **SEBI-registered research analysts and investment advisers** who publish model portfolios or factor strategies | They have to justify their method, and an outside audit of look-ahead, survivorship and tax is cheap insurance | SEBI's public list of registered intermediaries; their own websites and LinkedIn | Backtest audit |
| 2 | **Small fintech and wealthtech teams** (smallcase-style managers, PMS or AIF quant desks, robo-advisers) | They need NSE data pipelines and backtests built correctly, but are too small to hire a quant engineer | LinkedIn job posts asking for "quant developer" or "Python + NSE data"; startup job boards | Data pipelines, then audits |
| 3 | **Serious retail algo builders** on broker APIs (Kite Connect, Dhan, Upstox) who want a second opinion before going live | Most retail strategies fail after costs (SEBI's own study), and they know it | Broker developer forums (for example, TradingQnA), r/algotrading, Python and PyData meetups in India | Backtest audit at a smaller fixed price |
| 4 | **Teams building LLM or agent products** outside trading | The evaluation discipline (pre-registered, forward-tested, with random-call baselines) applies to any agent product | Your existing network, AI meetups, LinkedIn | LLM-agent evaluation |

**Don't approach:** finfluencers, tip channels, paid "strategy" courses, or
anyone who wants you to say a strategy works. SEBI is acting against
unregistered advice, and putting your name next to theirs is the main
reputational risk to this whole plan.

## Messages

Keep each one short, specific and about their problem. Never mention
returns, never offer to pick stocks, and never imply registration.

### 1. To a registered adviser or analyst (email or LinkedIn)

> Subject: An independent check of [firm]'s backtests
>
> Hi [name],
>
> I'm a software engineer who builds and audits backtests on NSE data. I saw
> that [firm] publishes [strategy or model portfolio]. I offer a fixed-price
> audit that checks the things that most often inflate Indian backtests:
> look-ahead, missing delisted stocks, overfitting (deflated Sharpe and PBO),
> and STT plus short-term against long-term tax on the real turnover. It also
> checks whether a cheap index fund does the same job after tax.
>
> You get a written report of what each check found. It's never a view on
> whether to trade, and every decision stays yours.
>
> Here is a short case study of a momentum rule that passed its own backtest
> and still lost to an index fund once tax and the benchmark's back-filled
> history were handled properly: [link]. If that's useful, I'm happy to do a
> free 30-minute call to scope it.
>
> [your name] · [contact]

### 2. To a fintech or wealthtech team (LinkedIn, or a reply to a job post)

> Hi [name], I noticed [company] is hiring for [role or "NSE data work"]. If
> part of it is a point-in-time NSE price pipeline, or a backtest harness that
> handles costs and Indian tax properly, I build those on contract: [scope],
> delivered with tests, and you own the code. Two examples of the kind of
> checks I build in are here: [link]. Would a short call to see whether a
> fixed-scope project fits be useful?
>
> [your name]

### 3. To a retail algo builder (forum or community reply, only where self-promotion is allowed)

Answer their actual question first. Mention the service only in your
signature or when they ask for help.

> One thing worth checking before going live: does your backtest include
> stocks that were later delisted, and does it apply 20% short-term tax on
> the turnover? Those two alone can turn a good-looking result into an
> ordinary one. I wrote up how I check both here: [link]. (I also do this as a
> fixed-price audit, [price], if you ever want a second pair of eyes.)

### 4. Follow-up (once, 5–7 days later, then stop)

> Hi [name], just following up on my note about [audit or pipeline]. If the
> timing's wrong, no problem. If a one-page sample of the audit report would
> help, I can send one.

## Before the first message

- [ ] Fill in the prices and contact in [offer.md](offer.md).
- [ ] Decide where the case studies live (a public page or a gist) so `[link]`
      points somewhere. Use no return figures in them, and no data newer than
      three months.
- [ ] Make a one-page sample audit report from build 2, with method and
      findings only.
- [ ] Have a CA or lawyer confirm the audit and pipeline scopes aren't
      research-analyst or algo-provider work under SEBI rules.

## Tracker

Copy this table and add a row per contact. Count a "serious enquiry" when
someone asks for a call, a scope or a price.

| Date | Segment (1–4) | Who (firm or handle) | Channel | Sent | Reply? | Call? | Scope or price sent? | Paid? |
|---|---|---|---|---|---|---|---|---|
| | | | | | | | | |

A pace that makes the six-week test meaningful: about 10 well-chosen
contacts a week across segments 1–2, plus whatever comes from segment 3
replies. That works out to roughly 60 messages in total.
