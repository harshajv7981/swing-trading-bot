"""The risk engine: exit plans, position sizing and the PASS/BLOCK gate.

Plain, tested Python that the swarm cannot override (build plan, risk rules
R1 to R9). Limits live in config/risk.toml. Nothing here places an order: a ticket is paper only.
"""

from .calendar import NSECalendar
from .config import RiskConfig
from .exits import ExitPlan, PlanRefused, plan_exit, trail_stop
from .gate import Check, KillSwitch, Verdict, check_order
from .portfolio import Order, Portfolio, Position, load_sectors
from .signals import Call, Decision, SignalRules, decide, read_calls
from .sizing import Sizing, SizingRefused, size_position
from .ticket import TicketRefused, make_ticket

__all__ = [
    "Call", "Check", "Decision", "ExitPlan", "KillSwitch", "NSECalendar", "Order", "PlanRefused", "Portfolio", "Position",
    "RiskConfig", "SignalRules", "Sizing", "SizingRefused", "TicketRefused", "Verdict", "check_order", "decide", "load_sectors", "plan_exit",
    "make_ticket", "read_calls", "size_position", "trail_stop",
]
