"""NSE's daily bhavcopy: one file per session with every stock's close and volume.

The momentum trial books from eod2_data, which syncs about a week late. The
bhavcopy for a session is out the same evening, so the days eod2_data lacks
can be filled from it and the month-end picks known on the day itself
(`swingbot-momentum picks`).

Both of NSE's layouts are read: the UDiFF one in use since July 2024
(`BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv`, columns TckrSymb, SctySrs,
ClsPric, TtlTradgVol, TradDt) and the older `cmDDMONYYYYbhav.csv` (SYMBOL,
SERIES, CLOSE, TOTTRDQTY, TIMESTAMP), plain or zipped.

NSE's terms of use forbid automated collection. Downloading the file by hand
from nseindia.com (Market Data > All Reports > Equities) is the clean route;
`fetch` exists for a single day at a time, only with --accept-nse-terms, as
`scripts/fetch_nse_announcements.py` does. The files are NSE data: keep them in
the git-ignored data/raw/, never in the repo or GitHub Actions.
"""

from __future__ import annotations

import io
import time
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd

DEFAULT_DIR = Path("data/raw/bhavcopy")
URL = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
COLUMNS = ["Date", "Symbol", "Series", "Close", "Volume"]
UDIFF = {"TradDt": "Date", "TckrSymb": "Symbol", "SctySrs": "Series", "ClsPric": "Close", "TtlTradgVol": "Volume"}
OLD = {"TIMESTAMP": "Date", "SYMBOL": "Symbol", "SERIES": "Series", "CLOSE": "Close", "TOTTRDQTY": "Volume"}


def parse(text: str) -> pd.DataFrame:
    """One bhavcopy's rows as Date, Symbol, Series, Close, Volume (cash-market equities only)."""
    df = pd.read_csv(io.StringIO(text), dtype=str)
    df.columns = [c.strip() for c in df.columns]
    names = UDIFF if "TckrSymb" in df.columns else OLD
    missing = [c for c in names if c not in df.columns]
    if missing:
        raise ValueError(f"not a bhavcopy: no {', '.join(missing)} column")
    if "FinInstrmTp" in df.columns:   # UDiFF: STK is the cash-market stock line
        df = df[df["FinInstrmTp"].str.strip() == "STK"]
    out = df[list(names)].rename(columns=names)
    out = out.apply(lambda c: c.str.strip())
    out["Date"] = pd.to_datetime(out["Date"], format="mixed", dayfirst=names is OLD)
    out["Symbol"] = out["Symbol"].str.upper()
    out["Close"] = pd.to_numeric(out["Close"], errors="coerce")
    out["Volume"] = pd.to_numeric(out["Volume"], errors="coerce")
    return out.dropna(subset=["Close"])[COLUMNS]


def _texts(path: Path):
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if name.lower().endswith(".csv"):
                    yield z.read(name).decode("utf-8", "replace")
    else:
        yield path.read_text(encoding="utf-8", errors="replace")


def read_dir(folder: Path) -> pd.DataFrame:
    """Every bhavcopy in `folder` (.csv or .zip), one row per symbol, series and day."""
    frames = [parse(t) for p in sorted(folder.glob("*")) if p.suffix.lower() in (".csv", ".zip")
              for t in _texts(p)]
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.concat(frames, ignore_index=True)
    return df.drop_duplicates(["Date", "Symbol", "Series"], keep="last").sort_values(["Symbol", "Date"])


def fetch(day: date, folder: Path = DEFAULT_DIR, pause: float = 3.0) -> Path:
    """Download one session's UDiFF bhavcopy zip into `folder`. One day per call, slowly."""
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"BhavCopy_NSE_CM_{day:%Y%m%d}.csv.zip"
    if out.exists():
        return out
    req = urllib.request.Request(URL.format(d=day), headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)", "Accept": "*/*",
        "Referer": "https://www.nseindia.com/"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    zipfile.ZipFile(io.BytesIO(data)).testzip()   # refuses an error page saved as a zip
    out.write_bytes(data)
    time.sleep(pause)
    return out
