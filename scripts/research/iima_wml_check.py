"""M6's excess against IIMA's survivorship-adjusted momentum factor (docs/research/survivorship-free-m6-prereg.md).

    python scripts/research/iima_wml_check.py data/raw/eod2_data/daily data/raw/indices/iima_4f_monthly_sba.csv

Information only, not judged. WML is IIMA's winners-minus-losers factor, in percent per month, from the
survivorship-bias-adjusted file of https://faculty.iima.ac.in/iffm/Indian-Fama-French-Momentum/.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from momentum_gold_study import m6_and_ew  # noqa: E402

from swingbot.momentum import load  # noqa: E402

PERIODS = {"full 2010-25": ("2010-01", "2025-12"), "discovery 2010-19": ("2010-01", "2019-12"),
           "holdout 2020-25": ("2020-01", "2025-12")}


def mean_t(x: pd.Series) -> str:
    return f"{x.mean():+.2%}/month (t {x.mean() / x.std() * np.sqrt(len(x)):+.1f})"


def main():
    data, iima = Path(sys.argv[1]), Path(sys.argv[2])
    px, val = load(data)
    me = px.groupby(px.index.to_period("M")).tail(1).index
    m6, ew = m6_and_ew(px, val, me)
    excess = (m6 - ew).dropna()
    excess.index = excess.index.to_period("M")
    f = pd.read_csv(iima, na_values="NA")
    wml = pd.Series(f["WML"].values / 100, index=pd.PeriodIndex(f["Date"], freq="M"))
    both = pd.DataFrame({"m6_excess": excess, "wml": wml}).dropna()
    for name, (lo, hi) in PERIODS.items():
        b = both.loc[lo:hi]
        print(f"{name} ({len(b)} months): M6 excess over mid EW {mean_t(b['m6_excess'])}; "
              f"IIMA WML {mean_t(b['wml'])}; correlation {b['m6_excess'].corr(b['wml']):+.2f}")


if __name__ == "__main__":
    main()
