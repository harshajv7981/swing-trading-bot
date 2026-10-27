"""Tables for docs/research/signal-thresholds.md from threshold_study.py's trades file.

    python scripts/research/threshold_tables.py runs/research/trades.parquet
"""
import sys

import numpy as np
import pandas as pd

t = pd.read_parquet(sys.argv[1])
t["era"] = np.where(t["date"] < "2020-01-01", "train", "test")
pd.set_option("display.width", 250); pd.set_option("display.float_format", "{:.3f}".format)
b = t[(t.k==2.0)&(t.rm==2.0)&(t.hold==5)].copy()
def tab(col, sub=None):
    x = b if sub is None else b[sub]
    g = x.groupby([col,"era"], observed=True)["R"].agg(["mean","count"]).unstack("era")
    g.columns=[f"{a}_{e}" for a,e in g.columns]; return g
b["rsi_b"] = pd.cut(b.rsi, [0,30,40,50,60,70,75,80,85,100])
b["ext_b"] = pd.cut(b.ext, [-99,-1,0,1,2,3,4,99])
print("hold5 meanR by RSI, all"); print(tab("rsi_b"))
print("hold5 meanR by ext, all"); print(tab("ext_b"))
# year-by-year: above200 minus below200
y = b.assign(year=b.date.dt.year).groupby(["year","above200"])["R"].mean().unstack()
print("year: above200 - below200 (hold5)"); print((y[True]-y[False]).round(3).to_string())
y = b.assign(year=b.date.dt.year).groupby(["year","mkt_above200"])["R"].mean().unstack()
print("year: mkt above - below"); print((y[True]-y[False]).round(3).to_string())
# rsi>75 vs <=75 by year
y = b.assign(year=b.date.dt.year, hot=b.rsi>75).groupby(["year","hot"])["R"].mean().unstack()
print("year: rsi>75 minus rest"); print((y[True]-y[False]).round(3).to_string())
y = b.assign(year=b.date.dt.year, hot=b.ext>3).groupby(["year","hot"])["R"].mean().unstack()
print("year: ext>3 minus rest"); print((y[True]-y[False]).round(3).to_string())

print('stop x target x hold grid')
g = t.groupby(["k","rm","hold","era"]).agg(meanR=("R","mean"), pct=("pct", lambda x: x.mean()*100), win=("R", lambda x:(x>0).mean()*100)).unstack("era")
g.columns=[f"{a}_{e}" for a,e in g.columns]
print(g.to_string())
