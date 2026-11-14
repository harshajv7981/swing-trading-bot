# Where this stands and what to build next (November 2026)

Written 2026-11-14. A roadmap, not investment advice: nothing here promises a
return. Repo facts come from the docs, code and logs on this machine. Outside
claims are cited, and anything without a source is marked [unverified].

## Where it stands

The repo is a careful, paper-only research harness for NSE equities. It has:

- a point-in-time data router (eod2_data, Yahoo, NSE bhavcopy, GDELT, NSE filings)
- a risk engine and a paper account and broker
- a Dhan sandbox adapter and a dashboard
- a forward momentum trial and two forward LLM-agent tests
- about 460 tests

The research process is its strongest part: 19 pre-registered studies in
[trials-ledger.md](trials-ledger.md), discovery and holdout periods, costs
plus 20.8% STCG, and PBO and deflated-Sharpe checks.

Two results survive, not one:

- **M6, monthly mid-cap momentum:** +14.8% CAGR, +12.0% after tax, -59% worst
  drawdown. Nifty 50 buy-and-hold made +10.7%, +9.9% after tax, with a -35%
  worst drawdown ([book-rules-prereg.md](book-rules-prereg.md), line 81).
- **G30, 70% M6 plus 30% GOLDBEES:** +16.0% CAGR, -35.1% drawdown and Sharpe
  0.57, before tax ([momentum-gold-prereg.md](momentum-gold-prereg.md)). It
  runs as a shadow ledger. It also leans on two unusual gold years, 2011 and
  2025.

Both are weaker than they look:

- **M6's deflated Sharpe is 0.78 against a 0.95 bar.** Its likely true excess
  over equal weight is nearer +0.3% a month than +0.6%
  ([momentum-overfit-prereg.md](momentum-overfit-prereg.md)). That takes about
  3.5 points a year off and leaves M6 near the Nifty 50 after tax (my
  arithmetic, not a test).
- **Fully delisted stocks are missing from the data**
  ([momentum-broad-prereg.md](momentum-broad-prereg.md), line 24).
- **The cheap passive twin was never tested over 2010–2026.** That twin is a
  Momentum 50 index fund, plus a gold ETF for G30, held a year and taxed at
  13%.

One operational problem is live right now. Since 9 Nov the nightly data pull
has been failing (`git fetch` timed out, then "bad pack header", then `git
reset` exited with status 128). The job crashes into
`~/Library/Logs/swingbot/momentum.log` and nothing tells you. eod2_data's last
bar is 2026-11-09. In short, the process is sound, but the edge is unproven
against survivorship and against the passive twin, and the plumbing can fail
without anyone noticing.

## What's missing

| Area | Gap | Why it matters | Evidence |
|---|---|---|---|
| Monitoring | **Nightly pull is failing silently.** 4 tracebacks in the log, and the data stops at 2026-11-09. There is no alert and no data-freshness check before a booking | If this lasts to 30 Nov, the month-end rebalance books on stale prices or not at all, which breaks the trial's review gate 1 | `momentum.log` lines 9, 20, 27–39; `com.swingbot.momentum.plist` has no notifier |
| Data | Fully delisted stocks are absent (DHFL, Jet Airways, Satyam) | Inflates every mid-cap long result. The large-cap +27% was mostly survivorship | momentum-broad-prereg.md, line 24 |
| Data | Local Momentum 50 index history starts 2022-08-17. The ETFs start 2022 and 2024 | M6 and G30 can't be compared with the passive twin over 2010–26 | `data/raw/eod2_data/daily/nifty midcap150 momentum 50.csv`, `momomentum.csv`, `momentum50.csv` |
| Data | No point-in-time fundamentals | Value, quality and earnings-surprise studies can't be run honestly. Only price factors are testable | data router reads only the latest results filing |
| Data | Price returns, no dividends | The index fund earns dividends that the M6 backtest ignores, so the comparison slightly flatters M6 | momentum-paper-trial.md |
| Execution | Every fill is at the month-end close. Slippage is assumed, never measured | At about 45% monthly turnover, a 0.1% miss per side costs about 1 point a year (arithmetic) | momentum-paper-trial.md, "Known gaps" |
| Execution | The Dhan live path needs DDPI, a static IP and a daily 2FA login, and quotes may cost money | It blocks automated orders. A monthly 20-stock rebalance can be placed by hand | [algo-trading-path.md](../algo-trading-path.md), items 1–6 |
| Risk | No real-money limits for M6 or G30 (capital cap, drawdown halt) and no written plan for a -60% fall. `config/risk.toml` was written for swing trades | Review gate 3 can't pass without them. They also decide whether a real drawdown is survivable | algo-trading-path.md, item 7 |
| Tax | The after-tax figure taxes the net gain once. There is no lot-level FY tax and no harvesting | Wrong past 12 months. Turnover is the main tax lever and isn't optimised | `scorecard.after_tax` shortcut comment (uncommitted) |
| Statistics | Momentum trial count is N = 87, and 24+ price rules have been tried | Every new momentum variant lowers M6's deflated Sharpe | momentum-overfit-prereg.md |

## What works in the evidence

**Momentum: works in India, mostly outside large caps, and crashes hard.**
- It holds up globally ([Jegadeesh & Titman 1993](https://doi.org/10.1111/j.1540-6261.1993.tb04702.x);
  [Asness, Moskowitz & Pedersen 2013](https://doi.org/10.1111/jofi.12021)).
- In India it survives the 3-factor model
  ([Dash & Mahakud 2015](https://www.emerald.com/insight/content/doi/10.1108/JABS-06-2014-0040/full/html)),
  though some studies find anomalies weakening after 2005
  ([review](https://ideas.repec.org/a/sae/globus/v22y2021i1p255-270.html)).
- IIMA publishes a free, **survivorship-adjusted** four-factor library, WML
  included, covering Oct 1993 to Dec 2025, with daily and monthly files
  ([IIMA library](https://faculty.iima.ac.in/~iffm/Indian-Fama-French-Momentum/),
  checked 2026-11-14).
- Crashes are part of the deal ([Daniel & Moskowitz 2016](https://doi.org/10.1016/j.jfineco.2015.12.002)).
  Live evidence: the Tata Midcap150 Momentum 50 index fund fell about 25% from
  27 Sep 2024 to 14 Mar 2025 and had not regained its peak by Jul 2026
  ([arthgyaan](https://arthgyaan.com/fund/tata-nifty-midcap-150-momentum-50-index-fund-direct-growth/150738),
  a secondary source).
- It can be bought as a fund. The NSE index launched in Aug 2022 with history
  back-filled to 2005 ([NSE press release](https://niftyindices.com/Press_Release/ind_prs16082022.pdf)).
  Direct plans cost about 0.24% (Kotak,
  [Value Research](https://www.valueresearchonline.com/funds/44573/kotak-nifty-midcap-150-momentum-50-index-fund-direct-plan/))
  to about 0.45–0.5% (Tata and Edelweiss, per
  [Kuvera](https://kuvera.in/mutual-funds/fund/tata-nifty-midcap-150-momentum-50-index-growth--TANMGZ-GR)).
  The scorecard's 0.4% sits inside that range.

**Gold as a diversifier: the repo's own result, still fragile.**
- Gold's monthly correlation with M6 was -0.13 over 2010–26, and G30 cut the
  worst drawdown from -62% to -35% (momentum-gold-prereg.md).
- Gold's 12.7% a year over that window is high. The doc itself says the CAGR
  edge goes away if gold only keeps pace with inflation.
- Tax: since 23 Jul 2024 most sources put gold ETFs at 12.5% LTCG after 12
  months ([Jainam](https://www.jainam.in/blog/etf-taxation-in-india/)).
  [Freefincal](https://freefincal.com/?p=210534) notes some ambiguity, so
  check with a CA before relying on it.

**Low volatility: decent Indian evidence, and already available as a cheap index.**
- Supporting studies: [Joshipura & Joshipura 2016](https://ojs.aut.ac.nz/applied-finance-letters/article/download/32/31)
  and [IMFI 2020](https://www.businessperspectives.org/images/pdf/applications/publishing/templates/article/assets/13547/IMFI_2020_02_Joshipura.pdf)
  (top 500, 2004–18).
- Against: [Pandey & Samanta](https://www.sciencegate.app/document/10.21013/jmss.v4.n1.p6)
  found no effect in the CNX 100 (abstract only).
- As an overlay on M6 it failed here. Cheap low-vol index funds exist, so
  there is little reason to build it.

**Earnings drift: mixed in India, and the one untested family free data can reach.**
- Drift found: [Agarwal & Sridharan, IIMB](https://repository.iimb.ac.in/handle/2074/17935).
- None exploitable in 100 NSE firms over 2014–18:
  [Gupta & Dhusia 2021](https://journals.vutbr.cz/index.php/trends/article/view/541).
- Insiders trade ahead of the drift:
  [Krishnan & Rangan, NSE-IGIDR](https://nsearchives.nseindia.com/research/content/NSE-IGIDR-WP4-14-15.pdf).
- A price-only version (earnings-announcement return, Brandt et al. 2008)
  needs no analyst estimates. The about-12.5%-a-year figure for US stocks
  before costs comes from a
  [summary](https://quantpedia.com/strategies/post-earnings-announcement-effect/),
  not the paper [unverified at source].

**Short-term mean reversion, chart patterns, intraday: don't survive costs.**
- The repo's own results: weekly reversal lost -0.36% a week (t -4.8), and
  every price, book and SMC rule failed.
- High-turnover anomalies mostly die after costs, and a buy/hold buffer is the
  most effective cheap fix
  ([Novy-Marx & Velikov 2016](https://www.nber.org/papers/w20721)).
- 71% of individual intraday cash traders lost money in FY23
  ([SEBI, Jul 2024](https://www.sebi.gov.in/media-and-notifications/press-releases/jul-2024/sebi-study-finds-that-7-out-of-10-individual-intraday-traders-in-equity-cash-segment-make-losses_84948.html)).

**Professionals mostly lose to the index.** Most active Indian funds trail
their benchmark over 10 years, with mid and small caps among the worst
([SPIVA India, year-end 2025](https://www.spglobal.com/spdji/en/documents/spiva/spiva-india-scorecard-year-end-2025.pdf)).

**Why one or two survivors need discounting.**
- Anomaly returns fall about 26% out of sample and 58% after publication
  ([McLean & Pontiff 2016](https://doi.org/10.1111/jofi.12365)).
- After many tries, the bar is t > 3, not 2
  ([Harvey, Liu & Zhu 2016](https://www.nber.org/papers/w20592)). M6's t is
  about 2.1 in each period.

**Tax (FY 2026-27).** STCG on listed equity is 20%; LTCG is 12.5% above a
₹1.25 lakh yearly exemption, with no indexation
([Zerodha on Budget 2024](https://zerodha.com/z-connect/business-updates/what-changes-for-investors-after-budget-2024)).
With the 4% cess these become 20.8% and 13%, as the repo uses. At M6's
turnover the 7.8-point gap costs about 1–1.5 points a year (arithmetic).

## Ways to earn

The goal is low-risk side income that holds up if the day job goes, not a job
search. Trading returns are a poor hedge for that: mid-cap drawdowns of about
60% tend to come in recessions, which is also when layoffs come. Ranked by
realistic expected value per hour, with money and legal risk weighed in.

| Rank | Option | Realistic payoff | Money at risk | Legal risk | Effort to start | Long shot? |
|---|---|---|---|---|---|---|
| 1 | **Freelance engineering for traders, fintechs and SEBI-registered advisers.** NSE data pipelines (survivorship-free, point-in-time), backtest harnesses, broker integrations, and a fixed-price "backtest audit" that checks look-ahead, survivorship, PBO/DSR and Indian costs and tax | Repeat project income. Rates not researched [unverified]. The repo already shows every one of these skills | None | Low: sell code and checks, never picks | 3–5 days: offer page, two case studies (builds 1 and 3), outreach | No. Finding clients is the hard part |
| 2 | **Freelance AI and agent work outside trading** (LLM apps, evals) | Likely the biggest market. The forward-tested LLM-agent trials and the ledger show eval discipline, which is rare | None | Low | Same portfolio as rank 1 | No |
| 3 | **The passive twin for your own long-term money:** a Momentum 50 index fund, optionally 70/30 with a gold ETF, held over a year, with the ₹1.25 lakh LTCG exemption harvested each year | Market return, minus 0.24–0.5% in fees. Harvesting saves up to about ₹16k a year (₹1.25L × 13%) | Market risk | None | Hours a year | No. Small and fairly reliable |
| 4 | **A paid code kit** (build 5): the harness as a package run on the buyer's own data. No NSE data and no signals | Small one-off sales, mostly support for ranks 1 and 2 | None | Low | 4–5 days | Yes, as income alone |
| 5 | **M6 or G30 at small size after the Oct 2027 review,** only if builds 1–3 pass, and only with money you won't need if the job goes | On ₹5 lakh, a 2-point after-tax edge is about ₹10k a year at best, and may be about zero | High (-35% to -60% falls) | Low | One manual rebalance a month | Yes. An experiment, not a hustle |
| 6 | **Educational writing:** "what failed on NSE and why" | Brings in rank 1 clients. Direct income is unlikely. SEBI's 2025 rules require price data at least 3 months old and bar return claims ([Business Today](https://www.businesstoday.in/markets/stocks/story/sebis-clampdown-on-finfluencers-cannot-use-live-stock-market-data-in-educational-content-462704-2025-01-31)) | None | Medium if it drifts into stock talk | Ongoing | Yes, for money |
| 7 | **The LLM-agent tests turn into an edge** | Unknown until 2027. The swarm already failed its walk-forward | None | None | Weekly Claude usage | Yes |
| 8 | **Selling algos or signals, or registering as a SEBI RA** | Retail algos go only through brokers and empanelled providers ([QuantInsti summary](https://blog.quantinsti.com/sebi-algo-trading-guidelines-retail-investors)). Signals need RA registration and a proven edge, which you don't have | None | High | Months | Yes. Don't |

Not legal or tax advice. Before taking client money on rank 1, have a CA or
lawyer check whether a given contract, such as a tool that ranks stocks for a
client, could count as research-analyst activity.

## Next 5 builds

Count so far: 19 studies, 24+ price rules, and N = 87 momentum trials for the
DSR. Every build below states what it adds to that count, and every study goes
through a pre-registration and a ledger row first.

| # | Build | Why it matters | Evidence | Effort | Data (free) | Known to fail within weeks if… | Pre-reg? |
|---|---|---|---|---|---|---|---|
| 1 | **Fix and guard the plumbing, then measure the paper-to-live gap.** (a) Repair the eod2_data pull (a shallow, partial clone is failing to fetch) and add a freshness gate: no booking when the last bar is older than the last NSE session, and a macOS notification on any failure. (b) Log next-open and VWAP beside each close fill and report the shortfall per rebalance. (c) Write M6/G30 real-money limits and the -60% plan into a locked config | The data feed is down now, and the 30 Nov booking depends on it. Gates 1 and 3 of the trial review depend on (b) and (c) | `momentum.log`; algo-trading-path.md items 5–7; momentum-paper-trial.md "Known gaps" | 2 | Existing eod2_data; bhavcopy open/close/VWAP via `scripts/fetch_bhavcopy.py` | (a) by 1 Dec: the November booking ran on fresh data, or an alert fired. (b) after 2–3 rebalances: an average shortfall above 0.25% a side means the backtest cost was too low, so re-run M6 at the observed cost | No study. Write the thresholds into momentum-paper-trial.md before the Nov booking. Adds 0 rules |
| 2 | **M6 and G30 against their passive twins, after tax, 2010–2026.** M6 after costs and 20.8% STCG, against the Momentum 50 TRI after a 0.4% fee and 13% LTCG over ₹1.25L. G30 against 70/30 Momentum 50 fund plus gold ETF. Report Aug 2022 onward (live index) separately | Decides where real money goes. If the twin matches, the reason to trade M6 by hand disappears | NSE factsheet; repo's 12-month replay (M6 +25.3% vs Momentum 50 +3.5%, one year only); the tax gap | 1–2 (reuse `scorecard.after_tax` and the study series) | niftyindices.com historical index and TRI files; GOLDBEES already local | Days: if the twin's after-tax CAGR is at or above M6's in both windows, real money goes to the fund and M6 stays research | Yes: a short decision rule written first. Adds 0 rules |
| 3 | **Survivorship-free M6 re-run.** Build a full-history panel from NSE daily bhavcopies, delisted names included, and re-run M6 unchanged. Cross-check against IIMA's survivorship-adjusted WML | The largest known bias in the only passing result | momentum-broad-prereg.md; IIMA library; McLean & Pontiff | 3–4 (parser for the pre-2024 format; symbol changes via eod2's `isin_symbol_map.json`, and by hand before ISINs appear) | NSE bhavcopy archive (2010 and 2018 files checked by hand: they list JETAIRWAYS and DHFL, which eod2_data lacks); IIMA CSVs | Week 1–2: full-period excess over EW below +0.3%/month, or t < 1.5 in either period. Then drop the real-money plan and treat the paper year as a process test | Yes: a re-test with thresholds fixed first. Adds 0 rules |
| 4 | **Rank-buffer M6:** buy the top 20 by 6-1 momentum, sell only when a holding leaves the top 40. Judge on after-tax CAGR | Turnover drives both cost and tax. A buffer is the best-documented fix, and the drawdown study's lower-turnover arm already raised after-tax return | Novy-Marx & Velikov 2016; momentum-drawdown-prereg.md arm C | 1–2 | Build 3's panel | Days: after-tax CAGR not at least 0.5 points above M6's in both periods, or discovery t < 2. Then drop it | Yes. Adds 1 momentum trial (N 87 → 88). Feeds only the Oct 2027 real-money design; the running trial stays as booked |
| 5 | **Side-hustle kit.** Pull the data router, bhavcopy loader, cost-and-tax backtest and trials ledger into a standalone package with a README and one example study. Ship no NSE data and no signals. Write builds 2 and 3 up as two public case studies using data at least 3 months old and no return claims. Add a one-page freelance offer | Turns the repo's real asset, its honest process, into income that doesn't depend on the market or an employer | SEBI intraday study (most retail strategies fail, so an audit has buyers [unverified: demand not measured]) | 4–5 | None | Six weeks after outreach: fewer than 3 serious enquiries and no paid project means narrowing the pitch, for example to registered advisers only | No study. Adds 0 rules |

**Status, 2026-11-14:**
- **Build 1:**
  - (a) Done. The nightly pull retries, notifies on failure, and warns when
    the data is over 7 days old.
  - (b) Done. Each booked month records its next-session shortfall, and the
    judging rule is in momentum-paper-trial.md.
  - (c) Done. The plan is in momentum-paper-trial.md: no real money before
    the Oct 2027 review, a ₹2.5 lakh cap, a review at -25%, and hold with no
    new money through a -60% fall. The scorecard's gate 3 now reads
    "written".
- **Build 2:** done ([passive-twin-prereg.md](passive-twin-prereg.md)). By
  the rule, the twin wins both: real money, if any, goes to the Momentum 50
  fund (or 70/30 with gold), and M6 and G30 stay paper research.
  - Over the full window M6 trailed by 8.0 points a year after tax. That rests
    on the fund's back-calculated 2010–22 history.
  - In the 49 live months M6 led by 10.7 points.
  - The fund's drawdowns were shallower in every window.
- **Build 3:** pre-registered ([survivorship-free-m6-prereg.md](survivorship-free-m6-prereg.md)).
  The judged re-run was dropped: the backfill would break NSE's ban on
  automated collection. The IIMA cross-check ran. Indian momentum survives
  survivorship adjustment (WML t +3.6, 2010–25), but it tracks M6 only weakly
  (correlation 0.27), so M6's own bias is still unmeasured.
- **Build 4:** done on eod2_data ([rank-buffer-prereg.md](rank-buffer-prereg.md)).
  R40 fails: -0.6 points after tax against M6 in 2010–19, and a discovery t
  of 1.6. Turnover only fell from 50% to 38%. N is now 88. No more momentum
  variants.
- **Build 5:** started. Drafts are in [../side-hustle/offer.md](../side-hustle/offer.md)
  (prices and contact left blank) and [../side-hustle/case-studies.md](../side-hustle/case-studies.md)
  (method only, no return figures). Pulling the harness out into a standalone
  package (4–5 days) is not started.

**Order:** 1 now, before the 30 Nov booking. Then 2, the cheapest and
possibly decisive, then 3, then 5 (which uses 2 and 3 as case studies), then
4 on build 3's panel.

**Deferred: price-only earnings-announcement drift in mid-caps.** The one
untested family with Indian evidence that free data (NSE filing timestamps)
can reach. It takes 5–7 days, mostly the filings backfill, and would add 1–2
rules (24 → 26). It's a long shot, so do it only after build 5 and only if M6
still stands. It needs a pre-registration, and it stops if the 2015–19
discovery excess after 0.25% a side is at or below zero, or t < 2.

## What I'd stop doing

- **Reviewing GitHub trading repos.** Eight or more reviews found no edge; the
  one useful import was RD-Agent's ledger idea. Only reopen for a repo that
  publishes a pre-registered, out-of-sample, after-cost result on Indian data.
- **New price-pattern rules** (indicators, SMC, chart books, weekly or gap
  rules). Over 24 have been tried, none passed, and each one raises the trial
  count.
- **New momentum variants beyond build 4.** At N = 87, each one makes M6 less
  credible, and the overfit study showed the settings barely matter.
- **The intraday G-down lead.** It needs minute bars you don't have for free,
  and intraday is where SEBI found most retail traders lose.
- **Forecasting models and ML rankers** (Kronos failed, Chronos was dropped,
  and both rankers failed). Don't reopen them without a new data source.
- **New LLM-agent tests.** Let the two running tests reach their 2027 dates,
  but add none. They are the biggest drain on the Claude window.
- **More US studies.** The US trial runs on its own in Actions. Your tax,
  cost and data advantages are Indian.
- **Live-order plumbing** (static IP, DDPI, Dhan quote plan) before builds 1–3
  say real money is justified. A monthly rebalance can be placed by hand.
- **Treating trading as the job-loss hedge.** Keep money you would live on
  out of M6 and G30.
- **Anything that reads as public advice:** tips, model portfolios, "my
  strategy made X%" posts, broker affiliate links.

## Sources

Repo: [trials-ledger.md](trials-ledger.md), [momentum-broad-prereg.md](momentum-broad-prereg.md),
[momentum-overfit-prereg.md](momentum-overfit-prereg.md), [momentum-gold-prereg.md](momentum-gold-prereg.md),
[momentum-paper-trial.md](momentum-paper-trial.md), [momentum-drawdown-prereg.md](momentum-drawdown-prereg.md),
[book-rules-prereg.md](book-rules-prereg.md), [../algo-trading-path.md](../algo-trading-path.md),
`~/Library/Logs/swingbot/momentum.log` (read 2026-11-14).

Outside sources were fetched or searched in sessions on 2026-11-14 and are
linked inline above. Four standard references are cited from memory for
their headline claims only and were not re-read: Jegadeesh & Titman 1993,
Asness, Moskowitz & Pedersen 2013, Daniel & Moskowitz 2016, and Bailey &
López de Prado 2014 (deflated Sharpe).
