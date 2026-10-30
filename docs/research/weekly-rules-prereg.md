# Weekly stock-pick rules: plan written before looking at results

Written 2026-10-30, before running anything. Harsha wants a model that picks
Indian stocks to hold for about a week, proven quickly, and then a list of stocks
to watch each week. One week of live paper trading cannot tell skill from luck
(a single week's return is mostly noise), so the proving is done on history:
every week from Jan 2010 to Oct 2026, with the rules fixed here first.

## Data and universe

- eod2_data daily bars (EQ series, jump-adjusted as in `swingbot.momentum`;
  opens, highs and lows scaled by the same factor). No dividends.
- At each week's last session (the **signal day**, normally Friday) the universe
  is rebuilt from data known that day, as in
  [momentum-broad-prereg.md](momentum-broad-prereg.md): 260+ sessions of
  history and a 60-session median traded value of at least ₹5 crore, ranked by
  that traded value.
  - **Liquid 250 (U):** ranks 1–250.
  - **Mid:** ranks 101–250.
- Same remaining bias as before: fully delisted stocks are missing, so results
  lean optimistic.

## Timing

Signals use the signal day's close. Trades happen at the **next session's
open** (normally Monday) and the position is held until the open after the next
signal day. Returns are open to open, so nothing uses a price the rule could not
have known.

## The five candidate rules (20 stocks, equal weight, reset every week)

| Rule | Universe | Picks |
|---|---|---|
| **W1 Reversal** | U | The 20 with the worst 5-session return. |
| **W2 Short momentum** | U | The 20 best by return from 68 to 5 sessions back (3 months, skipping the last week). |
| **W3 52-week high** | U | The 20 with the close nearest its 252-session closing high. |
| **W4 Mid M6 weekly** | Mid | The 20 best by return from 126 to 21 sessions back: the paper trial's rule, rebalanced weekly instead of monthly. |
| **W5 Breakout** | U | Stocks whose highest close this week is above every close of the 252 sessions before the week, with the signal-day close above its 200-day average and 5-day average volume at least 1.5× the 50-day average. Up to 20, ranked by that volume ratio; each gets 1/10 of the money at most (10%), the rest sits in cash at 6% a year. |

## Costs and tax (India, delivery trades)

- **Per side**, on the value traded: 0.15% statutory (STT 0.1% each side,
  stamp 0.015% on buys, exchange, SEBI and GST; Dhan charges no delivery
  brokerage) plus slippage at the open of 0.10% in U and 0.15% in Mid. So
  **0.25% a side in U, 0.30% a side in Mid**. Weights drift during the week, and
  each Monday is charged on the actual trades back to equal weight.
- **Tax:** every rule holds stocks for under a year, so gains are short-term:
  **20% STCG plus 4% cess = 20.8%**, charged each calendar year on the year's net
  gain, with losses carried forward. The Nifty 50 buy-and-hold is charged
  long-term tax once at the end (12.5% plus cess = 13%). The ₹1.25 lakh
  exemption and surcharge are ignored.

## Baselines

- **EW:** equal weight in every stock of the rule's universe, reset weekly, same
  costs and tax (W1, W2, W3, W5 against EW of U; W4 against EW of Mid).
- **Nifty 50** buy and hold (price index, no costs, LTCG at the end).
- **Monthly mid-cap M6** (the running paper trial's rule, close to close,
  0.20% a side), after STCG. This is the best-evidenced rule so far.

## Split and pass bar

- **Discovery:** weeks ending Jan 2010 – Dec 2019. **Holdout:** Jan 2020 – Oct 2026.
- **Pass, discovery:** mean weekly excess over its EW (after costs, before tax)
  above 0 with a t-stat above 2.
- **Pass, holdout:** mean weekly excess above 0, and beating EW in at least 4
  of the 7 holdout calendar years.
- **Worth using instead of the monthly picks:** a rule that passes both must
  also have a higher after-tax CAGR in the holdout than the monthly mid-cap M6
  and than the Nifty 50. If it passes but loses to the monthly M6 after tax, the
  monthly picks stay the recommendation.
- If more than one rule passes, the one with the highest discovery t-stat is
  chosen (picking on holdout numbers would be hindsight).
- Five rules are tried, so one may pass at t ≈ 2 by luck; that is said with any pass.
- Also reported: CAGR before and after tax, worst drawdown, Sharpe (cash 6%),
  weekly turnover, and each year's excess.

## Secondary (reported, not judged): trading the picks under the risk rules

The rule chosen above (or, if none passes, the one with the highest discovery
t-stat, flagged as failed) is traded the way `config/risk.toml` allows: the top
5 picks each Monday at the open, stop at 2 × ATR(14) below the entry, size so
the loss at the stop (plus a 0.5% gap allowance) is 1% of equity, no position
above 15% of equity. A stop is filled at the stop price, or at the open if the
stock opens below it; anything not stopped is sold at the next Monday open.
Same costs. Reported: CAGR, worst drawdown, hit rate, average R.

## After the test

- The chosen rule (or the monthly M6 if nothing passes) gives this week's watch
  list: entry at Monday's open, stop and share count under the 1%-risk rule.
- That list is booked as a one-week paper trade and its result reported. One
  week is reported as what it is, not as proof.
- The running monthly paper trial is not touched.
