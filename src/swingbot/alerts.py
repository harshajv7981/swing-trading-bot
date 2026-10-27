"""Phone alerts from the daily loop: fills, stops, a stop that lapsed, the kill switch.

The alerts are read back from what the loop already writes, never from a
hook's word for it: fills from the paper ledger, live orders and lapsed stops
from the paper broker's book, the latch from `kill_switch.json`, and failed
steps from the day's step list. Each alert has a key, and the keys already
sent are kept in `<state_dir>/alerts_sent.json`, so running a half twice, or
replaying a day, does not send the same message again.

The first run with alerts on sends nothing old: it marks what is already in
the ledger and the book as seen and sends one "alerts are on" message.

Channels, both free and with no account for the first:

    ntfy      install the ntfy app, subscribe to a topic name only you know,
              and put it in SWINGBOT_NTFY_TOPIC (it works like a password)
    telegram  make a bot with @BotFather, then SWINGBOT_TELEGRAM_TOKEN and
              SWINGBOT_TELEGRAM_CHAT_ID

Secrets come from the environment only, so they never land in git. With
`channel = "none"` (the default) every alert still goes to
`<state_dir>/alerts.jsonl` and the day's summary, and nothing leaves the
machine. An alert that could not be sent stays unsent and is tried again on
the next run.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

CHANNELS = ("none", "ntfy", "telegram")
SENT_FILE = "alerts_sent_{channel}.json"  # one per channel, so turning a channel on starts it fresh
LOG_FILE = "alerts.jsonl"

URGENT = "urgent"  # a person should look now: a stop lapsed, the kill switch latched, a stop gapped
NORMAL = "normal"


@dataclass(frozen=True)
class AlertConfig:
    channel: str = "none"
    ntfy_server: str = "https://ntfy.sh"
    fills: bool = True        # every buy and sell
    orders: bool = True       # each paper entry placed for the next session
    failures: bool = True     # a loop step that failed

    @classmethod
    def from_toml(cls, raw: dict) -> AlertConfig:
        unknown = set(raw) - {"channel", "ntfy_server", "fills", "orders", "failures"}
        if unknown:
            raise ValueError(f"[alerts]: unknown keys {sorted(unknown)}")
        channel = str(raw.get("channel", "none")).lower()
        if channel not in CHANNELS:
            raise ValueError(f"[alerts] channel must be one of {', '.join(CHANNELS)}, not {channel!r}")
        return cls(channel, str(raw.get("ntfy_server", cls.ntfy_server)).rstrip("/"),
                   **{k: bool(raw.get(k, True)) for k in ("fills", "orders", "failures")})


@dataclass(frozen=True)
class Alert:
    key: str
    title: str
    text: str
    level: str = NORMAL

    def line(self) -> str:
        return f"{'URGENT ' if self.level == URGENT else ''}{self.title}: {self.text}"


# --- what to say ---------------------------------------------------------

SELL_TITLES = {
    "stop": "Stop hit", "stop-limit": "Stop hit", "stop-gap": "Stop gapped through",
    "target": "Target hit", "target-gap": "Target gapped through", "time": "Time exit", "close": "Closed",
}


def _money(x) -> str:
    return f"{float(x):,.2f}"


def fill_alert(r: dict) -> Alert:
    key = f"fill:{r['order_id']}:{r['side']}:{r['at']}"
    if r["side"] == "buy":
        text = f"bought {r['qty']} {r['ticker']} @ {_money(r['price'])} on {r['at']}"
        if r.get("stop") is not None:
            text += f"; stop {_money(r['stop'])}, target {_money(r['target'])}"
        if r["reason"] == "entry-gap":
            text += " (opened under the limit)"
        return Alert(key, f"Bought {r['ticker']}", text)
    reason = r["reason"]
    title = f"{SELL_TITLES.get(reason, 'Sold')}: {r['ticker']}"
    text = f"sold {r['qty']} {r['ticker']} @ {_money(r['price'])} on {r['at']} ({reason})"
    if reason == "stop-gap":
        text += "; it opened under the stop, so the loss is bigger than planned"
    return Alert(key, title, text, URGENT if reason == "stop-gap" else NORMAL)


def lapse_alert(o: dict) -> Alert:
    return Alert(f"lapse:{o['id']}:{o['stop_lapsed']}", f"NO STOP on {o['ticker']}",
                 f"{o['ticker']} opened under its stop-limit on {o['stop_lapsed']} and never came back, so the "
                 f"stop lapsed unfilled. {o['qty']} shares are held with no stop: decide by hand whether to sell.",
                 URGENT)


def order_alert(o: dict) -> Alert:
    return Alert(f"order:{o['id']}", f"Paper order: {o['ticker']}",
                 f"buy {o['qty']} {o['ticker']} limit {_money(o['limit'])} for {o['session']}; "
                 f"stop {_money(o['stop'])}, target {_money(o['target'])}")


def kill_alert(st: dict) -> Alert:
    return Alert(f"kill:{st.get('at', '')}:{st.get('week', '')}", "KILL SWITCH LATCHED",
                 f"{st.get('reason', 'no reason recorded')}. No new entries until you reset it by hand "
                 "(swingbot-risk kill-switch --reset --by Harsha).", URGENT)


def failure_alert(date: str, step: dict) -> Alert:
    return Alert(f"failed:{date}:{step['name']}", f"Daily loop: {step['name']} failed", step["detail"])


# --- what happened -------------------------------------------------------


def _read_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def collect(ledger: Path, state_dir: Path, config: AlertConfig, date: str = "",
            steps: list[dict] = ()) -> list[Alert]:
    """Every alert the files support right now, sent or not, oldest first."""
    out: list[Alert] = []
    state_dir = Path(state_dir)
    if config.fills:
        out += [fill_alert(r) for r in _read_lines(Path(ledger))
                if r.get("kind") == "fill" and r.get("order_id")]
    book = state_dir / "paper_broker.json"
    orders = json.loads(book.read_text()).get("orders", []) if book.exists() else []
    out += [lapse_alert(o) for o in orders if o.get("type") == "oco" and o.get("stop_lapsed")
            and o.get("status") == "open"]
    if config.orders:
        out += [order_alert(o) for o in orders if o.get("type") == "entry" and o.get("status") == "open"]
    switch = state_dir / "kill_switch.json"
    if switch.exists():
        st = json.loads(switch.read_text())
        if st.get("latched"):
            out.append(kill_alert(st))
    if config.failures:
        out += [failure_alert(date, s) for s in steps if s.get("status") == "failed" and s.get("name") != "alerts"]
    return out


# --- sending -------------------------------------------------------------

Sender = Callable[[Alert], None]


def _post(url: str, data: bytes, headers: dict) -> None:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        if resp.status >= 300:
            raise OSError(f"{url.split('/')[2]} answered {resp.status}")


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is not set; alerts cannot be sent")
    return value


def sender(config: AlertConfig) -> Sender | None:
    """The function that delivers one alert on the configured channel, or None for no channel."""
    if config.channel == "ntfy":
        topic = _env("SWINGBOT_NTFY_TOPIC")

        def send(a: Alert) -> None:
            _post(f"{config.ntfy_server}/{urllib.parse.quote(topic)}", a.text.encode(),
                  {"Title": a.title.encode("ascii", "replace").decode(),
                   "Priority": "urgent" if a.level == URGENT else "default", "Tags": "chart_with_upwards_trend"})
        return send
    if config.channel == "telegram":
        token, chat = _env("SWINGBOT_TELEGRAM_TOKEN"), _env("SWINGBOT_TELEGRAM_CHAT_ID")

        def send(a: Alert) -> None:
            body = urllib.parse.urlencode({"chat_id": chat, "text": f"{a.title}\n{a.text}",
                                           "disable_notification": "false" if a.level == URGENT else "true"})
            _post(f"https://api.telegram.org/bot{token}/sendMessage", body.encode(),
                  {"Content-Type": "application/x-www-form-urlencoded"})
        return send
    return None


@dataclass
class Dispatch:
    sent: list[Alert]
    failed: list[tuple[Alert, str]]
    seeded: int = 0

    def detail(self) -> str:
        if self.seeded:
            return f"alerts are on; {self.seeded} earlier event(s) marked as seen, not sent"
        parts = [f"{len(self.sent)} sent"]
        if self.failed:
            parts.append(f"{len(self.failed)} not sent ({self.failed[0][1]}); tried again next run")
        return ", ".join(parts) + ("".join(f"; {a.line()}" for a in self.sent[:5]))


def dispatch(ledger: Path, state_dir: Path, config: AlertConfig, date: str = "", steps: list[dict] = (),
             send: Sender | None = None) -> Dispatch:
    """Send the alerts not sent before. `send` replaces the channel (tests, a dry run)."""
    state_dir = Path(state_dir)
    sent_path, log_path = state_dir / SENT_FILE.format(channel=config.channel), state_dir / LOG_FILE
    alerts = collect(ledger, state_dir, config, date, steps)
    broken = ""
    if send is None:
        try:
            send = sender(config)
        except ValueError as e:
            broken = str(e)
    first = not sent_path.exists()
    seen: set[str] = set(json.loads(sent_path.read_text())) if not first else set()

    result = Dispatch([], [])
    todo = [a for a in alerts if a.key not in seen]
    if first:
        # Nothing old goes out on the first run: only the welcome.
        seen |= {a.key for a in todo}
        result.seeded = len(todo)
        todo = [Alert("hello", "Swing bot alerts are on",
                      "You will get a message for each fill, stop, lapsed stop, kill switch latch and failed step.")]
    for a in todo:
        try:
            if broken:
                raise ValueError(broken)
            if send is not None:
                send(a)
        except Exception as e:  # noqa: BLE001  the alert stays unsent and is retried next run
            result.failed.append((a, f"{type(e).__name__}: {e}"))
            continue
        seen.add(a.key)
        result.sent.append(a)
        state_dir.mkdir(parents=True, exist_ok=True)
        with log_path.open("a") as fh:
            fh.write(json.dumps({**asdict(a), "date": date, "channel": config.channel}) + "\n")
    state_dir.mkdir(parents=True, exist_ok=True)
    tmp = sent_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(seen), indent=0) + "\n")
    os.replace(tmp, sent_path)
    return result
