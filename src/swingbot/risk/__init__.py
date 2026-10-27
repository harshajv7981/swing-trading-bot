"""The risk engine: exit plans, position sizing and the PASS/BLOCK gate.

Plain, tested Python that the swarm cannot override (build plan, risk rules
R1 to R9). Limits live in config/risk.toml. Nothing here places an order: a ticket is paper only.
"""

from .calendar import NSECalendar
from .config import RiskConfig
from .exits import ExitPlan, PlanRefused, plan_exit, trail_stop
from .gate import Check, KillSwitch, ResetRefused, Verdict, check_order
from .portfolio import Order, Portfolio, Position, load_sectors
from .sizing import Sizing, SizingRefused, size_position
from .ticket import TicketRefused, make_ticket

__all__ = [
    "Check", "ExitPlan", "KillSwitch", "NSECalendar", "Order", "PlanRefused", "Portfolio", "Position",
    "ResetRefused", "RiskConfig", "Sizing", "SizingRefused", "TicketRefused", "Verdict", "check_order",
    "load_sectors", "plan_exit",
    "make_ticket", "size_position", "trail_stop",
]
