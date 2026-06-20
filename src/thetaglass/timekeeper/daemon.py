"""The Timekeeper — Clock 1, the supervised heartbeat (launched by PM2).

Deliberately dumb, like its Chronotether ancestor: while the market is open, beat every
TICK_SECONDS — assemble the live book, write one snapshot, repeat. All state lives in the
SQLite store, so PM2 can kill and restart this process at any moment with zero loss.

Two clocks, by design (STATE_MACHINE.md): this one always writes; agents read on their
own cron and never write. The watchdog is NOT a second process — it runs INLINE at the
end of each tick, because a position's state only changes when we sync it. (Alert logic
lands in the next slice; the seam is marked below.)
"""
from __future__ import annotations

import logging
import time

from thetaglass.broker.robinhood.client import RobinhoodBroker
from thetaglass.settings import CONFIG
from thetaglass.state.assemble import assemble_positions
from thetaglass.store import Store
from thetaglass.timekeeper.clock import MarketClock

log = logging.getLogger("thetaglass.timekeeper")


def tick(broker, store: Store, is_daily_close: bool = False) -> int:
    """One sync: assemble the live book, persist it, run the inline watchdog.

    Returns the number of positions seen. Shared by the loop and `tg run --once`.
    """
    positions = assemble_positions(broker, store=store)
    store.record_tick(positions, is_daily_close=is_daily_close)
    # TODO(watchdog): evaluate alert thresholds on `positions` here, inline, and insert
    # any transitions into the alerts table before we return. Next slice.
    log.info("tick: %d position(s)%s", len(positions),
             " [daily close]" if is_daily_close else "")
    return len(positions)


def run(once: bool = False) -> None:
    _setup_logging()
    clock = MarketClock()
    store = Store()
    broker = RobinhoodBroker()
    interval = CONFIG.TICK_SECONDS

    if once:
        # Manual single beat (testing / cron fallback): always fires, market or not.
        log.info("single tick (--once)")
        tick(broker, store, is_daily_close=clock.is_session_close_tick(clock.now(), interval))
        store.close()
        return

    log.info("timekeeper start: tick=%ds session=%s-%s %s",
             interval, CONFIG.MARKET_OPEN, CONFIG.MARKET_CLOSE, CONFIG.MARKET_TZ)
    while True:
        now = clock.now()
        if not clock.is_open(now):
            nap = min(clock.seconds_until_open(now), CONFIG.CLOSED_SLEEP_CAP)
            log.info("market closed; sleeping %.0f min", nap / 60)
            time.sleep(nap)
            continue
        try:
            tick(broker, store, is_daily_close=clock.is_session_close_tick(now, interval))
        except Exception:
            # Never let a transient broker/network error kill the heartbeat; PM2 would
            # restart us anyway, but retrying next beat keeps history continuous.
            log.exception("tick failed; retrying next beat")
        time.sleep(interval)


def _setup_logging() -> None:
    # Log to stdout; PM2 captures it to var/logs/. Idempotent across restarts.
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
    # The MCP/httpx clients are chatty at INFO; let our heartbeat stand out.
    for noisy in ("httpx", "mcp", "mcp.client", "mcp.client.streamable_http"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


if __name__ == "__main__":
    run()
