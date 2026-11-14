"""Backup price source for when eod2_data stops updating: NSE's daily equity bhavcopy.

    python scripts/fetch_bhavcopy.py            # download new sessions, rebuild prices_daily
    python scripts/fetch_bhavcopy.py --no-fetch # only rebuild prices_daily

1. For each weekday after eod2's last date (daily/tcs.csv) up to today, download the
   bhavcopy and append its rows to data/raw/bhav_daily/<symbol>.csv in eod2's columns.
   Days already there are skipped; a 404 is a holiday (or not published yet).
2. Rebuild data/raw/prices_daily/: each eod2 file plus the bhav rows dated after that
   file's own last date. Files without bhav rows (indexes) are copied unchanged.

The eod2_data checkout is only read, so its `git pull` keeps working. NSE data is never
committed: data/raw is not tracked.
"""

from __future__ import annotations

import argparse
import io
import shutil
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

RAW = Path("data/raw")
EOD2, BHAV, MERGED = RAW / "eod2_data/daily", RAW / "bhav_daily", RAW / "prices_daily"
URL = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{:%Y%m%d}_F_0000.csv.zip"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
COLS = {"TradDt": "Date", "OpnPric": "Open", "HghPric": "High", "LwPric": "Low", "ClsPric": "Close",
        "TtlTradgVol": "Volume", "SctySrs": "Series"}


def last_date(p: Path) -> str:
    with p.open("rb") as f:
        f.seek(max(f.seek(0, 2) - 300, 0))
        return f.read().decode(errors="replace").strip().splitlines()[-1].split(",")[0]


def download(day: date) -> pd.DataFrame | None:
    """The day's bhavcopy in eod2 columns, or None if NSE has none for it."""
    req = urllib.request.Request(URL.format(day), headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            blob = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        df = pd.read_csv(z.open(z.namelist()[0]))
    df = df[df["FinInstrmTp"] == "STK"]
    out = df[["TckrSymb", *COLS]].rename(columns=COLS)
    assert (out["Date"] == str(day)).all(), f"bhavcopy for {day} has other dates"
    return out


def fetch(today: date) -> None:
    BHAV.mkdir(parents=True, exist_ok=True)
    start = date.fromisoformat(last_date(EOD2 / "tcs.csv")) + timedelta(days=1)
    have = set()
    if (BHAV / "tcs.csv").exists():
        have = set(pd.read_csv(BHAV / "tcs.csv", usecols=["Date"])["Date"])
    day = start
    while day <= today:
        if day.weekday() < 5 and str(day) not in have:
            df = download(day)
            print(day, "no file" if df is None else f"{len(df)} rows")
            if df is not None:
                for sym, rows in df.groupby("TckrSymb"):
                    p = BHAV / f"{sym.lower()}.csv"
                    rows.drop(columns="TckrSymb").to_csv(p, mode="a", header=not p.exists(), index=False)
            time.sleep(1)
        day += timedelta(days=1)


def merge() -> None:
    tmp = MERGED.with_name(MERGED.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    added = 0
    for p in EOD2.glob("*.csv"):
        b = BHAV / p.name
        if not b.exists():
            shutil.copy2(p, tmp / p.name)
            continue
        eod = pd.read_csv(p)
        new = pd.read_csv(b)
        new = new[new["Date"] > eod["Date"].max()].drop_duplicates(["Date", "Series"])
        pd.concat([eod, new]).to_csv(tmp / p.name, index=False)
        added += len(new)
    shutil.rmtree(MERGED, ignore_errors=True)
    tmp.rename(MERGED)
    print(f"prices_daily: {len(list(MERGED.glob('*.csv')))} files, {added} bhav rows added")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true")
    a = ap.parse_args()
    if not EOD2.is_dir():
        sys.exit(f"{EOD2} not found; run from the repo root")
    if not a.no_fetch:
        fetch(date.today())
    merge()
