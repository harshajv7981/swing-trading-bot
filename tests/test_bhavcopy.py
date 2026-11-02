import zipfile

import pandas as pd
import pytest
from test_momentum import KNOWN_2025, small_rule, write_stocks  # noqa: F401  (fixture)

from swingbot import bhavcopy, momentum

UDIFF_HEAD = ("TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,"
              "StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,"
              "SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,"
              "Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4\n")


def udiff_row(day, sym, series, close, vol, tp="STK"):
    return (f"{day},{day},CM,NSE,{tp},1,INE000000000,{sym},{series},,,,,{sym} LTD,1,1,1,{close},{close},1,,"
            f"{close},,,{vol},1,1,F1,1,,,,,\n")


def write_bhavcopies(full, folder, days):
    """UDiFF bhavcopies (zipped) for `days`, from the full eod2-style files."""
    folder.mkdir()
    for day in days:
        rows = [UDIFF_HEAD]
        for p in sorted(full.glob("s*.csv")):
            df = pd.read_csv(p, index_col="Date")
            r = df.loc[day]
            rows.append(udiff_row(day, p.stem.upper(), "EQ", r["Close"], r["Volume"]))
        rows.append(udiff_row(day, "S1", "", 5.0, 9, tp="IDX"))   # not a stock line: ignored
        with zipfile.ZipFile(folder / f"BhavCopy_NSE_CM_{day.replace('-', '')}.csv.zip", "w") as z:
            z.writestr(f"BhavCopy_NSE_CM_0_0_0_{day.replace('-', '')}_F_0000.csv", "".join(rows))


def test_parse_reads_both_layouts():
    new = bhavcopy.parse(UDIFF_HEAD + udiff_row("2026-10-30", "abc", "EQ", 101.5, 1000)
                         + udiff_row("2026-10-30", "IDXX", "", 1, 1, tp="IDX"))
    assert new.to_dict("records") == [{"Date": pd.Timestamp("2026-10-30"), "Symbol": "ABC", "Series": "EQ",
                                       "Close": 101.5, "Volume": 1000}]
    old = bhavcopy.parse("SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,\n"
                         "ABC,BE,1,1,1,99.0,99,1,500,1,01-JUL-2024,\n")
    assert old.iloc[0].to_dict() == {"Date": pd.Timestamp("2024-07-01"), "Symbol": "ABC", "Series": "BE",
                                     "Close": 99.0, "Volume": 500}
    with pytest.raises(ValueError, match="not a bhavcopy"):
        bhavcopy.parse("a,b\n1,2\n")


def test_bhavcopies_give_the_month_end_picks_before_eod2_has_them(tmp_path):
    write_stocks(tmp_path / "full")
    write_stocks(tmp_path / "short", end="2025-08-22")       # eod2_data a week behind
    write_bhavcopies(tmp_path / "full", tmp_path / "bhav", ["2025-08-25", "2025-08-26", "2025-08-27",
                                                            "2025-08-28", "2025-08-29"])
    px, val = momentum.load(tmp_path / "full")
    _, want = momentum.select(px, val, pd.Timestamp("2025-08-29"))
    e = momentum.same_day_picks(tmp_path / "short", tmp_path / "bhav", KNOWN_2025,
                                picks_path=tmp_path / "picks.json", ledger_path=tmp_path / "none.json")
    assert e["date"] == "2025-08-29" and e["picks"] == want and e["missing_sessions"] == []
    saved = __import__("json").loads((tmp_path / "picks.json").read_text())
    assert [m["date"] for m in saved["months"]] == ["2025-08-29"]          # the shape rebalance reads

    # Once eod2_data catches up and the trial books the month, the two are compared.
    momentum.update(tmp_path / "full", tmp_path / "ledger.json", KNOWN_2025, start="2025-08-01")
    e = momentum.same_day_picks(tmp_path / "short", tmp_path / "bhav", KNOWN_2025,
                                picks_path=tmp_path / "picks.json", ledger_path=tmp_path / "ledger.json")
    assert e["trial_match"] is True


def test_a_missing_day_is_reported_and_a_missing_month_end_refused(tmp_path):
    write_stocks(tmp_path / "full")
    write_stocks(tmp_path / "short", end="2025-08-22")
    write_bhavcopies(tmp_path / "full", tmp_path / "bhav", ["2025-08-26", "2025-08-29"])
    e = momentum.same_day_picks(tmp_path / "short", tmp_path / "bhav", KNOWN_2025,
                                picks_path=tmp_path / "p.json", ledger_path=tmp_path / "none.json")
    assert e["missing_sessions"] == ["2025-08-25", "2025-08-27", "2025-08-28"]
    (tmp_path / "bhav" / "BhavCopy_NSE_CM_20250829.csv.zip").unlink()
    with pytest.raises(ValueError, match="no bhavcopy for the month-end 2025-08-29"):
        momentum.same_day_picks(tmp_path / "short", tmp_path / "bhav", KNOWN_2025, month="2025-08",
                                picks_path=tmp_path / "p.json", ledger_path=tmp_path / "none.json")


def test_fetch_needs_the_terms_flag(tmp_path, capsys):
    write_stocks(tmp_path / "d")
    assert momentum.main(["picks", "--data", str(tmp_path / "d"), "--fetch", "--bhavcopy",
                          str(tmp_path / "b")]) == 2
    assert "terms" in capsys.readouterr().err
