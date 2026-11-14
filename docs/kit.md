# The research kit

A small set of tools for testing a trading idea on Indian equities honestly:
with costs, Indian tax, a written plan before the run, and a ledger of every
attempt. It ships **no market data and no trading signals**; you bring
prices you're licensed to use.

## Install

```bash
git clone <this repo> && cd swing-trading-bot
python -m venv .venv && .venv/bin/pip install -e .
.venv/bin/python examples/kit_example.py   # runs on made-up prices
```

## Data

One CSV per symbol in a folder, with eod2_data's columns: `Date`, `Close`,
`Volume` and `Series` (only `EQ` rows are used). `swingbot.research.load(folder)`
returns two DataFrames, split-adjusted closes and traded value, with one
column per stock and one row per day.

## The workflow (the order matters)

1. **Write the pre-registration first,** before running anything:
   `docs/research/<name>-prereg.md`. It sets out the data, the rule, the
   baseline, the periods (discovery and holdout) and a pass bar.
   [passive-twin-prereg.md](research/passive-twin-prereg.md) is a short
   example.
2. **Add its row to [trials-ledger.md](research/trials-ledger.md).** A test
   (`ledger_missing`) fails if you don't, so the count of ideas tried can't
   quietly shrink.
3. **Write the study script** using the functions below, and include a
   self-check. For example, show that the new rule with its new setting
   switched off reproduces the old result.
4. **Run it once and record the result in the pre-registration,** pass or
   fail. Changing the bar after seeing the result needs a new
   pre-registration and a new ledger row.

## The functions (`swingbot.research`)

| Function | What it answers |
|---|---|
| `load(folder)` | Split-adjusted closes and traded value from the CSVs |
| `tstat(excess)` | Is the excess return over the baseline more than noise? |
| `after_tax_cagr(returns)` | The CAGR after 20.8% short-term tax on each year's net gain, losses carried forward. This is how a strategy trading every month is taxed |
| `hold_after_tax_cagr(returns)` | The CAGR of buying once and selling at the end at 13% long-term tax, as for an index fund held over a year |
| `deflated_sharpe(x, trial_srs, trials)` | How likely is the Sharpe ratio to be real, given how many variants were tried (Bailey & López de Prado 2014)? |
| `ledger_missing(folder)` | Which pre-registrations aren't in the ledger yet? |

`examples/kit_example.py` shows each call on one toy rule. Running it on your
own data without a pre-registration is the exact mistake the kit exists to
prevent.

## What it deliberately leaves out

- Market data: NSE's terms forbid automated collection, so you supply your
  own.
- Signals, model portfolios or "best settings".
- Any claim that a strategy works. The repo's own ledger shows most ideas
  fail after costs and tax.

Not investment advice.
