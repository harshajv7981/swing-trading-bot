"""The stocks the bot trades: the Nifty 100, kept in data/universe/.

`nifty100.csv` (symbol, name, aliases) is what the news fetchers and the daily
refresh read; `sectors.csv` (symbol, sector) is what R4's two-per-sector limit
reads. NSE rebalances the index twice a year (March and September), so
`scripts/update_universe.py` compares the files with NSE's own constituent list
and adds what joined. It never deletes a stock that left: one may still be
held, and the gate needs its sector to count it. Drop those by hand once no
position is open.

Sector names are this repo's own, coarser than NSE's industries where it
matters for risk: banks apart from other finance, hospitals apart from drug
makers.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

UNIVERSE = Path("data/universe/nifty100.csv")
SECTORS = Path("data/universe/sectors.csv")
NSE_LIST_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty100list.csv"

# NSE's "Industry" column -> this repo's sector.
INDUSTRY_SECTORS = {
    "automobile and auto components": "Autos",
    "capital goods": "Capital Goods",
    "chemicals": "Chemicals",
    "construction": "Capital Goods",
    "construction materials": "Cement",
    "consumer durables": "Consumer Durables",
    "consumer services": "Retail",
    "fast moving consumer goods": "FMCG",
    "financial services": "Financial Services",
    "healthcare": "Pharma",
    "information technology": "IT",
    "metals & mining": "Metals",
    "oil gas & consumable fuels": "Energy",
    "power": "Power",
    "realty": "Realty",
    "services": "Services",
    "telecommunication": "Telecom",
}


@dataclass(frozen=True)
class Member:
    symbol: str
    name: str
    industry: str

    @property
    def sector(self) -> str:
        """This repo's sector, or "" when the industry is new (the gate then blocks it until set)."""
        sector = INDUSTRY_SECTORS.get(self.industry.strip().lower(), "")
        name = self.name.lower()
        if sector == "Financial Services" and "bank" in name.split():
            return "Banks"
        if sector == "Pharma" and ("hospital" in name or "healthcare" in name):
            return "Healthcare"
        return sector


def parse_nse_list(text: str) -> list[Member]:
    """NSE's constituent CSV: Company Name, Industry, Symbol, Series, ISIN Code."""
    rows = csv.DictReader(text.splitlines())
    out = []
    for r in rows:
        r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
        if r.get("Symbol"):
            out.append(Member(r["Symbol"].upper(), r.get("Company Name", ""), r.get("Industry", "")))
    return out


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


@dataclass(frozen=True)
class Changes:
    joined: list[Member]
    left: list[str]          # in our files, no longer in the index (kept; drop by hand)
    no_sector: list[str]     # joined with an industry this repo has no sector for

    def text(self) -> str:
        if not self.joined and not self.left:
            return "data/universe matches NSE's Nifty 100 list."
        lines = []
        lines += [f"joined: {m.symbol} ({m.name}, {m.industry} -> {m.sector or 'NO SECTOR'})" for m in self.joined]
        lines += [f"left:   {s} (kept: drop it by hand once no position is open)" for s in self.left]
        if self.no_sector:
            lines.append("set a sector by hand in data/universe/sectors.csv for: " + ", ".join(self.no_sector))
        return "\n".join(lines)


def compare(members: list[Member], universe: Path = UNIVERSE) -> Changes:
    ours = {r["symbol"].strip().upper() for r in _read(universe)}
    theirs = {m.symbol for m in members}
    joined = [m for m in members if m.symbol not in ours]
    return Changes(joined, sorted(ours - theirs), [m.symbol for m in joined if not m.sector])


def add_joined(changes: Changes, universe: Path = UNIVERSE, sectors: Path = SECTORS) -> None:
    """Append the stocks that joined to both files. Nothing is removed."""
    if not changes.joined:
        return
    known = {r["symbol"].strip().upper() for r in _read(sectors)}
    with universe.open("a", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerows([m.symbol, m.name, ""] for m in changes.joined)
    with sectors.open("a", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerows([m.symbol, m.sector] for m in changes.joined if m.symbol not in known)
