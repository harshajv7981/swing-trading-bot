"""Small price fixtures shared by the tests."""

import pandas as pd


def bars(closes, start="2026-01-05"):
    idx = pd.bdate_range(start, periods=len(closes))
    df = pd.DataFrame({"close": closes}, index=idx)
    df["open"] = df["close"] - 1
    df["high"] = df["close"] + 2
    df["low"] = df["open"] - 2
    df["volume"] = 1000.0
    return df[["open", "high", "low", "close", "volume"]]


class DictPrices:
    def __init__(self, data):
        self.data = data

    def daily(self, t):
        if t not in self.data:
            raise FileNotFoundError(t)
        return self.data[t]
