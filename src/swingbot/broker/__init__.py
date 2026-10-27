"""Brokers behind one interface: the paper broker that fills tickets from daily bars,
and the shape of the Dhan adapter. Nothing here places a real order."""

from .base import Broker, Fill, OrderRefused, StopMove, record_fills
from .paper import PaperBroker

__all__ = ["Broker", "Fill", "OrderRefused", "PaperBroker", "StopMove", "record_fills"]
