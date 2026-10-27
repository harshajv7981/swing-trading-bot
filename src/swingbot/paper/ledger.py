"""The paper account: replay recorded fills into cash, positions and a daily equity curve.

The idea is the challenge replay in HKUDS/AI-Trader (README says MIT;
github.com/HKUDS/AI-Trader): start from cash, apply each trade in time order,
mark what is held at each close, track the peak and the drawdown, and flag any
trade that breaks the rules. The code, the rules (the build plan's R1 to R7)
and the Indian details are this repo's own.

The ledger is one JSON line per record, appended by `swingbot-paper` and never
rewritten, so the history can't quietly change:

    {"kind": "open", "at": "2026-11-02 09:00", "cash": 1000000}
    {"kind": "fill", "at": "2026-11-03 09:20", "ticker": "RELIANCE", "side": "buy", "qty": 30,
     "price": 1450.0, "costs": 54.4, "stop": 1400.0, "target": 1550.0, "sector": "Energy",
     "fingerprint": "3f2a...", "verdict": "PASS"}
    {"kind": "stop", "at": "2026-11-06 15:40", "ticker": "RELIANCE", "stop": 1450.0}

A fill is what actually happened (the sandbox's or broker's price), not what
was planned. Stops are held by the broker, so the ledger does not fake a stop
fill: when a close or low goes through a stop and no sell is recorded that
session, the replay says so.

Prices come only from sessions that had closed by the as-of time. Days are
marked at the close; a fill on a non-trading day counts on the next session.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

from ..models import IST
from ..risk.config import RiskConfig
from ..risk.portfolio import Portfolio, Position

DEFAULT_LEDGER = Path("data/state/paper_ledger.jsonl")
DEFAULT_COST_BPS = 12.5   # per side: half the backtest's 25 bps round trip; record real charges with --costs
GATE_MONTHS = 6           # the build plan's paper-trading gate: 6 months
GATE_TRADES = 50          # ... and 50 closed trades
# Fills rarely match the plan to the rupee, so a size limit counts as broken
# only beyond this slack.
SLACK = 0.02


def _ts(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize(IST) if ts.tzinfo is None else ts.tz_convert(IST)


def _day(ts: pd.Timestamp) -> pd.Timestamp:
    return ts.tz_convert(IST).tz_localize(None).normalize()


def default_costs(qty: int, price: float, cost_bps: float = DEFAULT_COST_BPS) -> float:
    return round(qty * price * cost_bps / 1e4, 2)


class Ledger:
    """The append-only record file."""

    def __init__(self, path: str | Path = DEFAULT_LEDGER):
        self.path = Path(path)

    def records(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for n, line in enumerate(self.path.read_text().splitlines(), 1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                if r.get("kind") not in ("open", "fill", "stop"):
                    raise ValueError(f"unknown kind {r.get('kind')!r} (use open, fill or stop)")
                _ts(r["at"])
            except (KeyError, ValueError, json.JSONDecodeError) as e:
                raise ValueError(f"{self.path}:{n}: bad ledger line ({e})") from e
            out.append(r)
        # Stable: records at the same minute keep their file order.
        return sorted(out, key=lambda r: _ts(r["at"]))

    def append(self, record: dict) -> None:
        if record["kind"] != "open" and not any(r["kind"] == "open" for r in self.records()):
            raise ValueError(f"{self.path} has no opening balance; run `swingbot-paper open --cash N` first")
        if record["kind"] == "open" and any(r["kind"] == "open" for r in self.records()):
            raise ValueError(f"{self.path} already has an opening balance; start a new ledger file instead")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as fh:
            fh.write(json.dumps(record) + "\n")


@dataclass
class Holding:
    ticker: str
    qty: int
    avg_entry: float
    stop: float | None
    initial_stop: float | None
    target: float | None
    sector: str
    opened: str
    costs: float  # entry costs not yet charged to a closed trade


@dataclass(frozen=True)
class ClosedTrade:
    ticker: str
    opened: str
    closed: str
    qty: int
    entry: float
    exit: float
    pnl: float           # after both sides' costs
    r_multiple: float | None  # pnl / the rupees at risk to the initial stop


@dataclass(frozen=True)
class Flag:
    at: str
    rule: str
    detail: str


@dataclass
class Replay:
    start: str
    as_of: str
    starting_cash: float
    cash: float
    equity: pd.Series                     # equity at each session's close
    holdings: dict[str, Holding]
    trades: list[ClosedTrade] = field(default_factory=list)
    breaks: list[Flag] = field(default_factory=list)     # rules broken
    warnings: list[Flag] = field(default_factory=list)   # things to look at
    costs: float = 0.0
    closes: dict[str, float] = field(default_factory=dict)  # last close per held ticker
    final_equity: float | None = None  # includes fills after the last close, marked at their price

    # --- the numbers -------------------------------------------------------

    @property
    def end_equity(self) -> float:
        if self.final_equity is not None:
            return self.final_equity
        return float(self.equity.iloc[-1]) if len(self.equity) else self.starting_cash

    def max_drawdown(self) -> tuple[float, str | None, str | None]:
        """Worst fall from a peak, in %, with the peak and trough dates."""
        if self.equity.empty:
            return 0.0, None, None
        peak = self.equity.cummax()
        dd = (peak - self.equity) / peak * 100
        trough = dd.idxmax()
        if dd[trough] <= 0:
            return 0.0, None, None
        top = self.equity[:trough].idxmax()
        return float(dd[trough]), f"{top:%Y-%m-%d}", f"{trough:%Y-%m-%d}"

    def portfolio(self) -> Portfolio:
        """What the risk engine reads (`swingbot-risk size/check --portfolio`).

        With daily closes only, today's open is taken as the last close and
        Monday's open as the previous week's last close.
        """
        eq = self.end_equity
        if self.equity.empty:
            week_open = day_open = eq
        else:
            last = self.equity.index[-1]
            day_open = eq
            before = self.equity[self.equity.index < last - pd.Timedelta(days=last.weekday())]
            week_open = float(before.iloc[-1]) if len(before) else self.starting_cash
        positions = tuple(
            Position(h.ticker, h.qty, round(h.avg_entry, 2), h.stop if h.stop is not None else 0.0, h.sector)
            for h in self.holdings.values())
        return Portfolio(round(eq, 2), round(self.cash, 2), round(week_open, 2), round(day_open, 2), positions)

    def summary(self, bench: pd.Series | None = None) -> dict:
        dd, top, trough = self.max_drawdown()
        wins = [t for t in self.trades if t.pnl > 0]
        losses = [t for t in self.trades if t.pnl <= 0]
        rs = [t.r_multiple for t in self.trades if t.r_multiple is not None]
        start, end = pd.Timestamp(self.start), pd.Timestamp(self.as_of)
        months = (end.year - start.year) * 12 + (end.month - start.month) - (end.day < start.day)
        out = {
            "start": self.start,
            "as_of": self.as_of,
            "sessions": int(len(self.equity)),
            "starting_cash": self.starting_cash,
            "equity": round(self.end_equity, 2),
            "cash": round(self.cash, 2),
            "return_pct": round((self.end_equity / self.starting_cash - 1) * 100, 2),
            "max_drawdown_pct": round(dd, 2),
            "drawdown_peak": top,
            "drawdown_trough": trough,
            "costs": round(self.costs, 2),
            "closed_trades": len(self.trades),
            "win_rate": round(len(wins) / len(self.trades), 4) if self.trades else None,
            "avg_r": round(sum(rs) / len(rs), 3) if rs else None,
            "expectancy": round(sum(t.pnl for t in self.trades) / len(self.trades), 2) if self.trades else None,
            "profit_factor": (round(sum(t.pnl for t in wins) / -sum(t.pnl for t in losses), 3)
                              if losses and sum(t.pnl for t in losses) < 0 else None),
            "open_positions": [asdict(h) for h in self.holdings.values()],
            "rule_breaks": [asdict(f) for f in self.breaks],
            "warnings": [asdict(f) for f in self.warnings],
            "gate": {
                "months": max(months, 0), "months_needed": GATE_MONTHS,
                "trades": len(self.trades), "trades_needed": GATE_TRADES,
                "met": months >= GATE_MONTHS and len(self.trades) >= GATE_TRADES and not self.breaks,
            },
        }
        if bench is not None and len(self.equity):
            b = bench.reindex(self.equity.index).ffill().dropna()
            if len(b) >= 2:
                # From the close before the first session, when the cash was put in.
                prior = bench[bench.index < self.equity.index[0]]
                base = float(prior.iloc[-1]) if len(prior) else float(b.iloc[0])
                out["benchmark_return_pct"] = round((float(b.iloc[-1]) / base - 1) * 100, 2)
                out["excess_return_pct"] = round(out["return_pct"] - out["benchmark_return_pct"], 2)
        return out


def replay(records: list[dict], bars: dict[str, pd.DataFrame], as_of, sessions: pd.DatetimeIndex,
           config: RiskConfig | None = None) -> Replay:
    """Apply the ledger to the sessions up to `as_of`.

    `bars` per ticker must already be cut to what had closed by `as_of`;
    `sessions` are the trading days to mark (the benchmark's, usually).
    """
    config = config or RiskConfig()
    as_of = _ts(as_of)
    opens = [r for r in records if r["kind"] == "open"]
    if not opens:
        raise ValueError("the ledger has no opening balance; run `swingbot-paper open --cash N` first")
    start = _ts(opens[0]["at"])
    cash = float(opens[0]["cash"])
    records = [r for r in records if r["kind"] != "open" and _ts(r["at"]) <= as_of]

    days = sessions[(sessions >= _day(start)) & (sessions <= _day(as_of))]
    rp = Replay(f"{start:%Y-%m-%d}", f"{as_of:%Y-%m-%d %H:%M}", cash, cash, pd.Series(dtype=float), {})
    holdings = rp.holdings
    last_close: dict[str, float] = {}
    curve: dict[pd.Timestamp, float] = {}
    pending = list(records)

    def equity_now() -> float:
        return rp.cash + sum(h.qty * last_close.get(t, h.avg_entry) for t, h in holdings.items())

    def week_open_equity(day: pd.Timestamp) -> float:
        monday = day - pd.Timedelta(days=day.weekday())
        before = [v for d, v in curve.items() if d < monday]
        return before[-1] if before else rp.starting_cash

    for day in days:
        todays, pending = [r for r in pending if _day(_ts(r["at"])) <= day], \
            [r for r in pending if _day(_ts(r["at"])) > day]
        sold_today: set[str] = set()
        for r in todays:
            at = f"{_ts(r['at']):%Y-%m-%d %H:%M}"
            if r["kind"] == "stop":
                _move_stop(rp, holdings, r, at)
            elif r["side"] == "buy":
                _buy(rp, holdings, r, at, equity_now(), week_open_equity(day), config)
            elif r["side"] == "sell":
                _sell(rp, holdings, r, at)
                sold_today.add(r["ticker"].upper())
            else:
                rp.breaks.append(Flag(at, "format", f"side {r['side']!r}: only buy or sell"))
        for t, h in holdings.items():
            b = bars.get(t)
            if b is None or day not in b.index:
                continue
            row = b.loc[day]
            last_close[t] = float(row["close"])
            if h.stop is not None and float(row["low"]) <= h.stop and t not in sold_today:
                rp.warnings.append(Flag(f"{day:%Y-%m-%d}", "stop", (
                    f"{t} traded at {float(row['low']):,.2f}, through its stop {h.stop:,.2f}, and no sell is "
                    "recorded that session. Check the broker: if the stop filled, record the sell.")))
        curve[day] = equity_now()

    for r in pending:  # fills dated after the last closed session: apply, mark at cost
        at = f"{_ts(r['at']):%Y-%m-%d %H:%M}"
        if r["kind"] == "stop":
            _move_stop(rp, holdings, r, at)
        elif r["side"] == "buy":
            _buy(rp, holdings, r, at, equity_now(), week_open_equity(_day(_ts(r["at"]))), config)
        else:
            _sell(rp, holdings, r, at)
    rp.equity = pd.Series(curve, dtype=float)
    rp.final_equity = equity_now()
    rp.closes = {t: last_close[t] for t in holdings if t in last_close}
    return rp


def _move_stop(rp: Replay, holdings: dict[str, Holding], r: dict, at: str) -> None:
    t, new = r["ticker"].upper(), float(r["stop"])
    h = holdings.get(t)
    if h is None:
        rp.warnings.append(Flag(at, "stop", f"stop for {t}, which is not held; ignored"))
        return
    if h.stop is not None and new < h.stop:
        rp.breaks.append(Flag(at, "R7 stop only moves up", f"{t} stop lowered from {h.stop:,.2f} to {new:,.2f}"))
    h.stop = new


def _buy(rp: Replay, holdings: dict[str, Holding], r: dict, at: str, equity: float,
         week_open: float, config: RiskConfig) -> None:
    t, qty, price = r["ticker"].upper(), int(r["qty"]), float(r["price"])
    costs = float(r.get("costs", default_costs(qty, price)))
    stop, target, sector = r.get("stop"), r.get("target"), r.get("sector", "")
    brk = lambda rule, detail: rp.breaks.append(Flag(at, rule, f"{t}: {detail}"))  # noqa: E731

    if r.get("verdict") != "PASS":
        brk("risk gate", "bought without a PASS from `swingbot-risk check` for this order")
    if stop is None:
        brk("R1 stop and target", "bought without a stop")
    elif float(stop) >= price:
        brk("R1 stop and target", f"stop {float(stop):,.2f} is not below the fill {price:,.2f}")
    if t in holdings and not config.allow_adding:
        brk("adding", f"bought more while already holding {holdings[t].qty}")
    if stop is not None and float(stop) < price:
        risk = qty * (price - float(stop))
        if risk > equity * config.risk_pct / 100 * (1 + SLACK):
            brk("R2 risk per trade", f"{risk:,.0f} at the stop = {risk / equity * 100:.2f}% of equity "
                                     f"(limit {config.risk_pct}%)")
    if qty * price > equity * config.max_position_pct / 100 * (1 + SLACK):
        brk("R3 position cap", f"{qty * price:,.0f} = {qty * price / equity * 100:.1f}% of equity "
                               f"(limit {config.max_position_pct}%)")
    if week_open > 0 and (week_open - equity) / week_open * 100 >= config.weekly_kill_pct:
        brk("R5 weekly kill switch", f"bought with equity {(week_open - equity) / week_open * 100:.1f}% under "
                                     "the week's open")

    rp.cash -= qty * price + costs
    rp.costs += costs
    if rp.cash < -1e-6:
        brk("cash", f"cash went to {rp.cash:,.0f}: that is margin, which the plan never uses")
    if t in holdings:
        h = holdings[t]
        h.avg_entry = (h.avg_entry * h.qty + price * qty) / (h.qty + qty)
        h.qty += qty
        h.costs += costs
        if stop is not None:
            h.stop = float(stop)
    else:
        holdings[t] = Holding(t, qty, price, None if stop is None else float(stop),
                              None if stop is None else float(stop),
                              None if target is None else float(target), sector, at[:10], costs)
    if len(holdings) > config.max_open_positions:
        brk("R4 open positions", f"{len(holdings)} held (limit {config.max_open_positions})")
    if sector and sum(h.sector == sector for h in holdings.values()) > config.max_per_sector:
        brk("R4 sector", f"{sum(h.sector == sector for h in holdings.values())} in {sector} "
                         f"(limit {config.max_per_sector})")


def _sell(rp: Replay, holdings: dict[str, Holding], r: dict, at: str) -> None:
    t, qty, price = r["ticker"].upper(), int(r["qty"]), float(r["price"])
    h = holdings.get(t)
    if h is None or qty > h.qty:
        rp.breaks.append(Flag(at, "long only", f"{t}: sold {qty} with {0 if h is None else h.qty} held"))
        if h is None:
            return
        qty = h.qty
    costs = float(r.get("costs", default_costs(qty, price)))
    entry_costs = h.costs * qty / h.qty
    pnl = qty * (price - h.avg_entry) - costs - entry_costs
    at_risk = qty * (h.avg_entry - h.initial_stop) if h.initial_stop is not None else 0.0
    rp.trades.append(ClosedTrade(t, h.opened, at[:10], qty, round(h.avg_entry, 2), price, round(pnl, 2),
                                 round(pnl / at_risk, 3) if at_risk > 0 else None))
    rp.cash += qty * price - costs
    rp.costs += costs
    h.costs -= entry_costs
    h.qty -= qty
    if h.qty == 0:
        del holdings[t]
