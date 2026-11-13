# Review: Microsoft RD-Agent (and RD-Agent(Q))

Written 2026-11-13. Desk review only (README, readthedocs, paper abstract and secondary summaries). Nothing was
cloned, installed or run. Where a fact could not be confirmed it says so.

## What it is
Open-source (MIT, ~15k stars) framework from Microsoft Research Asia where LLM agents propose ideas and write the
code for them, in a loop. Scenarios: quant finance, Kaggle-style ML, paper implementation. The quant one is
RD-Agent(Q) ([repo](https://github.com/microsoft/RD-Agent), [paper arXiv 2505.15155](https://arxiv.org/abs/2505.15155),
NeurIPS 2025).

## How the quant loop works
Each round: an LLM writes a hypothesis (a new factor or a model tweak), turns it into a task, a coder agent writes
the Python, a runner backtests it inside Qlib, a summariser reads the result and feeds the next round. A bandit
(default) chooses whether the next round works on factors or on the model. Defaults: 10 coder evolutions per task,
neural net model (PyTorch), top-50 dropout strategy
([scenario docs](https://rdagent.readthedocs.io/en/latest/scens/quant_agent_fin.html)).

## Cost to run
- Needs chat + JSON mode + an embedding model, via LiteLLM ([config docs](https://rdagent.readthedocs.io/en/latest/installation_and_configuration.html)).
  OpenAI, Azure and DeepSeek are documented. Claude, Ollama and local models are NOT documented (LiteLLM could
  in theory route to them, unverified). There is no Claude Code plan path: it wants an API key.
- Linux plus Docker (conda mode exists for the quant scenario). Owner is on a Mac.
- Calls per loop: not documented. Each round is several agent steps plus up to 10 code-fix retries, so
  dozens of calls per round and hundreds per run is a fair guess. The README quotes "under $10" for a full run
  on a paid frontier model; that is the vendor's number, not checked.
- With no API budget a weak local model would write broken factor code most of the time. Verdict: not runnable here.

## India data fit
Qlib ships China (and US) data. NSE needs our daily CSVs converted to Qlib's binary format (dump_bin script), a
trading calendar, an instruments file and the adjusted-price/factor columns. The docs hard-code CSI300 paths and
dates (train 2008-14, valid 2015-16, test 2017-2020). Doable, maybe a day of plumbing, but the docs say nothing
about non-Chinese markets, and the scenario prompts describe China A-shares. Weekly/monthly top-20 rebalancing
(our setup) does not match its daily top-50 strategy without code changes.

## Evidence of real performance
Paper claims about 2x annualised return of the Alpha158 factor library with 70% fewer factors, on CSI300. Third-party
summary figures (IC ~0.053, ARR ~14%) could not be checked against the paper tables. Measured on one
fixed China test window (2017-2020), by the authors, with the same period used to compare many variants. No
multiple-testing correction or out-of-sample live test was found. Treat as a research claim, not proof.

## Overfitting and look-ahead risk
- The LLM has read years of market commentary and published factor papers; it can "invent" factors that are really
  remembered ones. Pre-cutoff history cannot be trusted as a test.
- Every round is a tested hypothesis. 50 rounds is 50 tries on the same data, so the best one is inflated.
- This repo already tried a trained model (ml-model-prereg.md), overfit checks (momentum-overfit-prereg.md) and a
  foundation model (kronos-prereg.md). RD-Agent adds no new kind of evidence.

How to check it honestly if ever used: (1) freeze the data split before the run, with a hold-out of dates AFTER
the LLM's training cut-off (late 2025 onward) that the loop never sees; (2) cap the number of rounds up front and
count every candidate as a trial; (3) deflate the best result for that count (Bonferroni, or deflated Sharpe);
(4) accept only candidates that clear the hold-out and then a forward paper period, as with the other
pre-registered studies; (5) charge our real costs and tax.

## Worth borrowing (small ideas, no framework)
- Bandit/"what to try next" bookkeeping: a plain log of every hypothesis tried and its score, so the trial count
  is known. Matches our pre-registration habit; a table in docs/research/ is enough.
- Alpha158-style feature list as a checklist of cheap price/volume features we have not tried
  ([Qlib Alpha158](https://github.com/microsoft/qlib/blob/main/qlib/contrib/data/handler.py)). Test in our own
  harness under a pre-registration.
- Factor-pruning idea: drop new factors highly correlated with ones already held (their "fewer factors" claim).

## Skip
The whole framework (Linux/Docker/API key), Qlib data conversion, the neural-net model template, the LLM
code-generation loop (we have no budget and no honest way to isolate its look-ahead), and anything China-specific.

## Verdict
SKIP the framework; borrow only the trial-log and Alpha158 feature-list ideas. Top reason: it needs a paid LLM API
and its gains come from automated search that this repo's pre-registered discipline would have to heavily discount.
