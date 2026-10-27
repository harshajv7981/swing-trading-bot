import csv

from swingbot.news import load_universe
from swingbot.risk.portfolio import load_sectors
from swingbot.universe import SECTORS, UNIVERSE, Member, add_joined, compare, parse_nse_list

NSE_CSV = """Company Name,Industry,Symbol,Series,ISIN Code
Reliance Industries Ltd.,Oil Gas & Consumable Fuels,RELIANCE,EQ,INE002A01018
Newco Bank Ltd.,Financial Services,NEWBANK,EQ,INE000000001
Newco Hospitals Ltd.,Healthcare,NEWHOSP,EQ,INE000000002
Newco Robotics Ltd.,Robotics,NEWBOT,EQ,INE000000003
"""


def test_the_repo_universe_is_the_nifty_100_with_a_sector_each():
    symbols = [c.symbol for c in load_universe(UNIVERSE)]
    assert len(symbols) == len(set(symbols)) == 100
    sectors = load_sectors(SECTORS)
    assert all(sectors.get(s) for s in symbols), [s for s in symbols if not sectors.get(s)]


def test_nse_industries_map_to_our_sectors():
    by = {m.symbol: m for m in parse_nse_list(NSE_CSV)}
    assert by["RELIANCE"].sector == "Energy"
    assert by["NEWBANK"].sector == "Banks"
    assert by["NEWHOSP"].sector == "Healthcare"
    assert by["NEWBOT"].sector == ""  # an industry we have no sector for: left for a human


def test_compare_adds_joiners_and_never_drops_leavers(tmp_path):
    uni, sec = tmp_path / "u.csv", tmp_path / "s.csv"
    uni.write_text("symbol,name,aliases\nRELIANCE,Reliance Industries,RIL\nOLDCO,Old Co,\n")
    sec.write_text("symbol,sector\nRELIANCE,Energy\nOLDCO,Metals\n")
    changes = compare(parse_nse_list(NSE_CSV), uni)
    assert [m.symbol for m in changes.joined] == ["NEWBANK", "NEWHOSP", "NEWBOT"]
    assert changes.left == ["OLDCO"] and changes.no_sector == ["NEWBOT"]
    add_joined(changes, uni, sec)
    rows = list(csv.DictReader(uni.open()))
    assert [r["symbol"] for r in rows] == ["RELIANCE", "OLDCO", "NEWBANK", "NEWHOSP", "NEWBOT"]
    assert load_sectors(sec) == {"RELIANCE": "Energy", "OLDCO": "Metals", "NEWBANK": "Banks",
                                 "NEWHOSP": "Healthcare", "NEWBOT": ""}
    assert compare(parse_nse_list(NSE_CSV), uni).joined == []


def test_member_sector_keeps_bank_apart_from_other_finance():
    assert Member("X", "Bajaj Finance Ltd.", "Financial Services").sector == "Financial Services"
    assert Member("Y", "Bank of Baroda", "Financial Services").sector == "Banks"
