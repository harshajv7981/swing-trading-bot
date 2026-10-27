"""swingbot-walkforward: replay the daily paper loop one session at a time over saved data.

    swingbot-walkforward init --from 2026-07-01 --to 2026-10-23 --cash 500000 \\
        --prices data/raw/prices --events data/raw/news/events.csv
    swingbot-walkforward next      # runs days until one needs the swarm, then stops with its brief
    (play the swarm on the brief, appending to that day's views.jsonl)
    swingbot-walkforward next      # the next batch, or finish that day and go on
    swingbot-walkforward status
    swingbot-walkforward report    # the account, trades and the swarm's hit rate over the run

This is the backtest that matches what the bot will really do. Each session
day runs the same `swingbot-daily` start and finish, at 08:30 and 08:50 IST,
with the same signal filters, risk gate, paper broker, kill switch and
learning memory, so positions, sector limits and open risk interact as they
would live. The per-event backtest (`swingbot-backtest`) cannot show that.

Nothing is fetched. Prices and news come from saved files, and every step
already cuts its data to the run's time: bars to sessions that had closed,
news to what was published, lessons to trades that had closed. For the
results blackout (R9), pass `--results-dates` with each company's results
meetings over the run (`symbol,date`, several lines per company is fine);
without it R9 only sees the NSE notices in the events file, if any. The run keeps
its own ledger, broker book, kill switch, memory and alerts under its folder
(default `runs/walkforward/`), so it never touches the real paper account.

News published before 1 July 2026 is left out by the briefs, because the
swarm's model may remember what happened next; start the run after that.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

from .daily.hooks import HOOKS, OK, Step
from .daily.loop import (
    DailyConfig,
    Day,
    brief,
    finish,
    load_state,
    start,
    window_events,
)
from .events import DEFAULT_LLM_CUTOFF
from .paper.cli import main as paper_main
from .paper.ledger import Ledger
from .risk.calendar import NSECalendar
from .risk.results import load_manual
from .swarm.recorded import RecordedSwarm

DEFAULT_DIR = Path("runs/walkforward")
START_AT = "08:30"
FINISH_AT = "08:50"


def refresh(day) -> Step:
    """The walk-forward refresh: nothing. The saved files are the data, cut by time at each step."""
    return Step("data refresh", OK, "walk-forward replay: saved files only, cut to this run's time")


def sessions(start: date, end: date, calendar: NSECalendar) -> list[date]:
    out, d = [], start
    while d <= end:
        if calendar.is_session(d):
            out.append(d)
        d += timedelta(days=1)
    return out


class Run:
    def __init__(self, folder: Path = DEFAULT_DIR):
        self.dir = Path(folder)
        self.state_path = self.dir / "run.json"

    def state(self) -> dict:
        if not self.state_path.exists():
            raise FileNotFoundError(f"no walk-forward run in {self.dir}; start one with `swingbot-walkforward init`")
        return json.loads(self.state_path.read_text())

    def save(self, st: dict) -> None:
        self.state_path.write_text(json.dumps(st, indent=2) + "\n")

    def config(self, st: dict) -> DailyConfig:
        # The real steps, as config/daily.toml names them, with the refresh swapped out.
        hooks = {name: f"swingbot.daily.adapters:{name}" for name in HOOKS}
        hooks["refresh"] = "swingbot.walkforward:refresh"
        return DailyConfig(hooks=hooks, runs_dir=self.dir / "days", ledger=self.dir / "ledger.jsonl",
                           state_dir=self.dir / "state", memory_db=self.dir / "memory.db",
                           holidays=Path(st["holidays"]) if st.get("holidays") else None,
                           risk_config=st.get("risk_config"), data_config=str(self.dir / "data.toml"),
                           news_cutoff=DEFAULT_LLM_CUTOFF)

    def day(self, st: dict, when: str) -> Day:
        return Day(f"{st['days'][st['index']]} {when}", self.config(st))

    # --- init ------------------------------------------------------------------

    def init(self, start: date, end: date, cash: float, prices: Path, events: Path,
             holidays: str | None = None, risk_config: str | None = None,
             results_dates: Path | None = None) -> dict:
        if self.state_path.exists():
            raise ValueError(f"{self.dir} already holds a run; pick another --dir or delete it by hand")
        if not prices.is_dir():
            raise FileNotFoundError(f"no prices folder at {prices}")
        if not events.exists():
            raise FileNotFoundError(f"no events file at {events}")
        if results_dates is not None:
            if not results_dates.exists():
                raise FileNotFoundError(f"no results dates file at {results_dates}")
            load_manual(results_dates)  # a bad line fails here, not on day one of the run
        calendar = NSECalendar.load(holidays)
        missing = sorted({y for y in range(start.year, end.year + 1) if not calendar.knows(y)})
        if missing:
            raise ValueError(f"no NSE holiday list for {missing}; add them to config/nse_holidays.toml first")
        days = sessions(start, end, calendar)
        if not days:
            raise ValueError(f"no NSE sessions between {start} and {end}")
        self.dir.mkdir(parents=True, exist_ok=True)
        if results_dates is not None:
            # R9 reads `<state_dir>/results_dates.csv`; each date counts until it has passed.
            (self.dir / "state").mkdir(exist_ok=True)
            shutil.copyfile(results_dates, self.dir / "state" / "results_dates.csv")
        (self.dir / "data.toml").write_text(
            "# Written by swingbot-walkforward: saved files only, nothing fetched.\n"
            '[sources]\nprices = "csv"\nnews = "events"\nfilings = "events"\nfundamentals = "nse_results"\n'
            f'[paths]\nprices_dir = "{prices.resolve()}"\nevents_file = "{events.resolve()}"\n'
            "[options]\ncache_prices = false\n")
        Ledger(self.dir / "ledger.jsonl").append(
            {"kind": "open", "at": f"{days[0] - timedelta(days=1)} 18:00", "cash": float(cash)})
        st = {"from": start.isoformat(), "to": end.isoformat(), "cash": cash, "days": [d.isoformat() for d in days],
              "index": 0, "stage": "start", "holidays": holidays, "risk_config": risk_config}
        self.save(st)
        return st

    # --- next ------------------------------------------------------------------

    def next(self, out=print) -> str:
        """Run days until one needs the swarm (returns "brief"), or the run ends ("done")."""
        st = self.state()
        while st["index"] < len(st["days"]):
            if st["stage"] == "start":
                day = self.day(st, START_AT)
                state = start(day)
                st["stage"] = "views"
                self.save(st)
                if state["entries"] and self._waiting(day, state):
                    out(f"{day.as_of:%a %d %b %Y}: play the swarm on {day.path('brief.md')}, appending to "
                        f"{day.path('views.jsonl')}, then run `swingbot-walkforward next`.")
                    return "brief"
            day = self.day(st, FINISH_AT)
            state = load_state(day)
            if state["entries"] and self._waiting(day, state):
                step = brief(day, state)
                out(f"{day.as_of:%a %d %b %Y}: {step.detail}; play the swarm on {day.path('brief.md')}, "
                    "then `swingbot-walkforward next`.")
                return "brief"
            result = finish(day)
            out(f"{day.as_of:%a %d %b %Y}: {len(result['signals'])} signal(s), {len(result['tickets'])} ticket(s)"
                + ("" if result["entries"] else f"; no entries: {'; '.join(result['why_not'])}"))
            st.update(index=st["index"] + 1, stage="start")
            self.save(st)
        out(f"walk-forward run done: {st['days'][0]} to {st['days'][-1]}. `swingbot-walkforward report` next.")
        return "done"

    @staticmethod
    def _waiting(day: Day, state: dict) -> bool:
        """Events in the day's window that have no views yet (the brief holds the next batch)."""
        events = window_events(day, state)
        views = day.path("views.jsonl")
        done = set(RecordedSwarm(views).views) if views.exists() else set()
        return any(e.event_id not in done for e in events)

    # --- report ----------------------------------------------------------------

    def report(self) -> str:
        st = self.state()
        last = st["days"][min(st["index"], len(st["days"]) - 1)]
        cfg = self.config(st)
        argv = ["--ledger", str(cfg.ledger), "--state-dir", str(cfg.state_dir), "--data-config", cfg.data_config]
        if cfg.risk_config:
            argv += ["--config", cfg.risk_config]
        import io
        from contextlib import redirect_stderr, redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            paper_main([*argv, "report", "--as-of", f"{last} 18:00", "--out", str(self.dir / "report")])
        lines = [f"# Walk-forward run {st['days'][0]} to {st['days'][-1]}", "",
                 f"Sessions run: {st['index']} of {len(st['days'])}.", "", "```", buf.getvalue().strip(), "```"]
        lines += ["", *self._calls()]
        text = "\n".join(lines) + "\n"
        (self.dir / "report.md").write_text(text)
        return text

    def _calls(self) -> list[str]:
        """Every day's signals and what happened to the tickets, from the days' finish.json files."""
        signals = tickets = 0
        for f in sorted((self.dir / "days").glob("*/finish.json")):
            r = json.loads(f.read_text())
            signals += len(r["signals"])
            tickets += len(r["tickets"])
        return [f"Swarm signals: {signals}. Tickets past the gate: {tickets}.",
                ("The paper report above has the fills, closed trades, R multiples and rule checks; "
                 "`swingbot-scorecard` grades the personas once enough calls have closed.")]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="swingbot-walkforward", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dir", default=str(DEFAULT_DIR), help="the run's folder (default runs/walkforward)")
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init", help="set up a run over saved prices and news")
    i.add_argument("--from", dest="start", required=True)
    i.add_argument("--to", dest="end", required=True)
    i.add_argument("--cash", type=float, required=True)
    i.add_argument("--prices", default="data/raw/prices")
    i.add_argument("--events", default="data/raw/news/events.csv")
    i.add_argument("--holidays", help="NSE holiday TOML (default config/nse_holidays.toml)")
    i.add_argument("--risk-config", help="risk TOML (default config/risk.toml)")
    i.add_argument("--results-dates", help="symbol,date CSV of results meetings over the run, for the "
                                           "R9 blackout (the news file's NSE notices are read as well)")
    sub.add_parser("next", help="run days until one needs the swarm")
    sub.add_parser("status")
    sub.add_parser("report")
    args = p.parse_args(argv)
    run = Run(Path(args.dir))
    try:
        if args.cmd == "init":
            start_day, end_day = date.fromisoformat(args.start), date.fromisoformat(args.end)
            if start_day < date.fromisoformat(str(DEFAULT_LLM_CUTOFF)[:10]):
                print(f"note: news before {DEFAULT_LLM_CUTOFF} is left out of the briefs (the model may "
                      "remember it), so those days will trade nothing")
            st = run.init(start_day, end_day, args.cash, Path(args.prices), Path(args.events),
                          args.holidays, args.risk_config,
                          Path(args.results_dates) if args.results_dates else None)
            print(f"walk-forward run in {run.dir}: {len(st['days'])} sessions from {st['days'][0]} to "
                  f"{st['days'][-1]}, cash {args.cash:,.0f}. Next: `swingbot-walkforward next`.")
            return 0
        if args.cmd == "next":
            run.next()
            return 0
        if args.cmd == "status":
            st = run.state()
            done = st["index"]
            print(f"{done} of {len(st['days'])} sessions done"
                  + (f"; on {st['days'][done]} ({st['stage']})" if done < len(st["days"]) else ""))
            return 0
        if args.cmd == "report":
            print(run.report())
            return 0
    except (FileNotFoundError, ValueError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
