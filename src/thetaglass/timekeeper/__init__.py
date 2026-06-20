"""Clock 1: the always-running sync heartbeat (the Timekeeper)."""
from thetaglass.timekeeper.daemon import run, tick

__all__ = ["run", "tick"]
