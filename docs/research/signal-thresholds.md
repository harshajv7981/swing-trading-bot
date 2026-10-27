# Evidence for the signal-to-order thresholds

This is where each value in `config/signals.toml` comes from. There are three
kinds of evidence:

- **a test on real NSE prices**, described below;
- **published research**, linked where it is used;
- **arithmetic** from the costs or the risk limits.

Some values have no evidence yet. These are marked **provisional**, and the
bot's own scored calls must set them.

## The price test

**Data.** Daily bars for 94 NSE large caps (the 40 in `data/universe` plus
54 other Nifty 100 names), with NIFTYBEES standing in for the Nifty 50. The
source is [eod2_data](https://github.com/BennyThadikaran/eod2_data), built
from NSE's daily reports and adjusted for splits and bonuses. It is not
adjusted for dividends, and stocks delisted more than a year ago are missing,
so the results have some survivorship bias.

**Method.** Every stock-day from 2010 on is treated as a long call made after
the close and traded with this repo's ticket:

- the entry is a DAY limit buy at the last close, which fills only if the next
  session trades at or below it;
- the exit is an ATR stop (a stop-loss market, so a gap fills at the open), an
  R-multiple target, or a time exit at the close of session N, counting the
  entry day as 1;
- when the stop and the target fall on the same day, the stop is assumed to
  hit first;
- costs are 0.25% per round trip.

That gives 14.7 million simulated trades across 48 stop, target and hold
settings. Values were chosen on **2010–2019** and checked on **2020–Oct 2026**.

**What this test cannot show.** These are base rates for buying any stock on
any day. They are not news-driven calls. They say whether a filter improves the
trade the bot makes once it has a call, not whether the swarm's calls have an
edge. The trades overlap, so the counts overstate how certain the results are.
That is why each claim below is also checked year by year.

To reproduce, run `scripts/research/threshold_study.py`, then
`threshold_tables.py`. Usage is in the study script's docstring.

## Values and their evidence

All results are mean R per trade with the atr:2 / 2R / 5-session ticket,
unless stated otherwise.

| Setting | Value | Evidence |
|---|---|---|
| `min_horizon_hours` | **72** (3 sessions) | **Costs and published research.** NSE delivery costs about 0.22% per round trip: 0.1% STT each side, 0.015% stamp duty on the buy, and exchange, SEBI and GST charges ([Zerodha's charges page](https://zerodha.com/charges)). In the price test, the average 3-session trade lost 0.19%. Only holds of 10 sessions or more were positive, because they collect the market's drift. [Lopez-Lira & Tang](https://arxiv.org/abs/2304.07619) find that news drift is gone once costs reach 20 bps. A 1–2 session swing on large caps cannot pay for itself. |
| `max_horizon_hours` | 480 (20 sessions) | Kept as before. Base returns keep rising up to 20 sessions, but after the first few days that is market drift, not the news. |
| `max_signal_age_sessions` | **1** | **Published research.** Lopez-Lira & Tang find the price drift after news lasts "one to two trading days". Their long-short Sharpe also fell from 6.5 (2021) to 1.2 (2024) as more people used LLMs. A call counts only if it was made after the open of the last session before the entry. Weekend news still counts for Monday. |
| `min_confidence` | 0.60, **provisional** | **No usable evidence.** LLMs' stated confidence is overconfident and poorly calibrated ([Xiong et al., ICLR 2024](https://arxiv.org/abs/2306.13063)), so no published cut-off carries over to this swarm. Once 50+ calls are scored, set it to the lowest confidence band whose hit rate clears the costs. `swingbot-scorecard` and the backtest's Brier score report this. |
| `defensive_min_confidence` | **0.60** (no extra bar) | **Price test, mixed.** Longs bought with NIFTYBEES under its 200-day average did better in 13 of 15 years. In 2010–19 they made +0.020R against −0.032R in an uptrend. They did worse in 2020–26 (−0.057 against −0.009), which came down to the 2020 crash, and in 2025. There is no consistent case for a higher bar. R11 still halves the risk per trade in a defensive mood. |
| `require_above_sma200` | true, **disputed** | **Price test, against.** Above the 200-day SMA was worse in 15 of 17 years: −0.021R against −0.015R in 2010–19, though −0.018 against −0.034 in 2020–26. At 3–10 sessions, short-term reversal outweighs trend in Indian large caps. The overlay keeps it only because the gate's R8 in `config/risk.toml` blocks these trades anyway. Whether to keep R8 is your call. |
| `require_above_sma50` | false | **Price test.** No consistent effect: better in 2020–26, worse in 2010–19. |
| `max_rsi` | **80** | **Price test.** In 2010–19 every RSI14 band above 70 did worse than average (−0.018R): 70–80 −0.068R, 80–85 −0.113R, 85+ −0.177R. In 2020–26 only 85+ was clearly bad (−0.080R), while 75–85 was around average. The cap is set at 80 as the point both periods can live with. The usual 70 would have removed many trades that did fine after 2020. |
| `max_extension_atr` | 3.0 | **Price test.** More than 3 ATRs above EMA10 lost more than the rest in 13 of 17 years. More than 4 ATRs lost in both periods (−0.22R and −0.27R). |
| `stop_rule` | **atr:2.5** | **Price test.** With a 2R target, a 2.5-ATR stop beat 1.5 and 2 ATR in both periods at every hold. At 10 sessions it made +0.017R in 2010–19 and +0.024R in 2020–26, against +0.013R and +0.016R for 2 ATR. 3 ATR was about the same as 2.5 and needs a larger loss per share. |
| `target_rule` | 2R | **Price test.** 1.5R, 2R and 3R were within about 0.01R of each other up to 10 sessions. 3R helps only at 20 sessions. |
| `max_new_orders` | 3 | **Arithmetic, not research.** The gate allows 5 positions and 5% open risk at 1% each, so 3 a day keeps 2 slots for later news. |
| `min_order_value` | **Rs 25,000** | **Costs.** Dhan charges Rs 12.5 + GST per stock sold ([OptionX, July 2026](https://optionx.trade/brokers/dhan)). At Rs 25,000 that is 0.06%, a quarter of the statutory round trip. At Rs 10,000 it would be 0.15%. |

## What to revisit

- **`min_confidence`**, once the swarm has 50+ scored calls.
- **R8**, the 200-day SMA rule in `risk.toml`. The data says it does not help
  at this bot's holding period. Changing it is a risk-limit decision for you
  to make by hand.
- **Rerun the study each year.** The RSI result already differs between the
  two periods.
