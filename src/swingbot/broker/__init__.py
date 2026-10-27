"""Brokers behind one interface: the paper broker that fills tickets from daily bars,
and the Dhan adapter for Dhan's sandbox. Nothing here places a real-money order."""

from .base import Broker, Fill, OrderRefused, StopMove, record_fills
from .paper import PaperBroker

__all__ = ["Broker", "Fill", "OrderRefused", "PaperBroker", "StopMove", "record_fills"]
