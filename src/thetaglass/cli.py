"""Thetaglass CLI. v0: just enough to authenticate and inspect raw broker data.

State machine, status tables, and alerts come later — right now this exists to prove
the Robinhood connection and let us look at the real shape of a live spread.
"""
from __future__ import annotations

import json

import typer
from rich import print as rprint

from thetaglass.broker.robinhood.auth import AuthStore
from thetaglass.broker.robinhood.client import RobinhoodBroker
from thetaglass.timekeeper import supervisor

app = typer.Typer(no_args_is_help=True, add_completion=False,
                  help="Thetaglass — theta-decay watchdog for sold options.")
auth_app = typer.Typer(no_args_is_help=True, help="Robinhood authentication.")
app.add_typer(auth_app, name="auth")
tk_app = typer.Typer(no_args_is_help=True,
                     help="Timekeeper (Clock 1): the sync heartbeat and its PM2 supervision.")
app.add_typer(tk_app, name="timekeeper")


@auth_app.command("login")
def auth_login():
    """Step 1: print the Robinhood authorization URL. Approve it on your phone."""
    url = AuthStore().begin_login()
    rprint("\n[bold]Open this on your phone and approve:[/bold]\n")
    rprint(f"[cyan]{url}[/cyan]\n")
    rprint("After approving, you'll be redirected to a localhost URL that won't load.")
    rprint("Copy the [bold]code[/bold] value (or the whole redirect URL) and run:\n")
    rprint("  [green]tg auth complete '<code-or-url>'[/green]\n")


@auth_app.command("complete")
def auth_complete(code_or_url: str = typer.Argument(..., help="The pasted code or full redirect URL")):
    """Step 2: exchange the pasted code for tokens."""
    tok = AuthStore().complete_login(code_or_url)
    rprint(f"[green]Authenticated.[/green] scope={tok.get('scope')!r}")


@auth_app.command("status")
def auth_status():
    """Show whether we hold a valid token."""
    rprint(AuthStore().status())


@app.command("dump")
def dump(
    raw_out: str = typer.Option("", "--out", help="Also write the full raw payload to this file"),
):
    """Pull accounts + option positions + live quotes and print the RAW JSON.

    This is the inspection tool: we use it to see exactly how Robinhood reports a
    multi-leg spread (legs, ids, fields) before designing the state machine.
    """
    broker = RobinhoodBroker()

    accounts = broker.get_accounts()
    rprint(f"[bold]accounts:[/bold] {len(accounts)}")

    bundle: dict = {"accounts": accounts, "by_account": {}}

    for acct in accounts:
        acct_no = acct.get("account_number") or acct.get("account_id") or acct.get("id")
        if not acct_no:
            rprint(f"[yellow]skip account with no number:[/yellow] {acct}")
            continue
        positions = broker.get_option_positions(acct_no)
        rprint(f"[bold]account[/bold] {acct_no}: [cyan]{len(positions)}[/cyan] option leg(s)")

        # Collect every instrument id we can find on the legs, then quote them.
        inst_ids = _instrument_ids(positions)
        quotes = broker.get_option_quotes(inst_ids) if inst_ids else []

        bundle["by_account"][acct_no] = {
            "option_positions": positions,
            "instrument_ids": inst_ids,
            "quotes": quotes,
        }

    blob = json.dumps(bundle, indent=2, default=str)
    print(blob)
    if raw_out:
        with open(raw_out, "w") as f:
            f.write(blob)
        rprint(f"\n[green]wrote raw payload to {raw_out}[/green]")


@app.command("inspect")
def inspect():
    """Run the state-machine pipeline live and print each canonical Position.

    No persistence yet — this validates Layers A–D against real broker data.
    """
    from thetaglass.state.assemble import assemble_positions

    positions = assemble_positions(RobinhoodBroker())
    if not positions:
        rprint("[yellow]No open option positions found.[/yellow]")
        return

    for p in positions:
        legs = " / ".join(
            f"{l.side[0].upper()} {l.strike:g}{l.option_type[0].upper()}" for l in p.legs)
        rprint(f"\n[bold cyan]{p.underlying} {p.strategy_type}[/bold cyan]  ({legs})")
        rprint(f"  position_id      {p.position_id[:12]}…  acct {p.account_number}")
        rprint(f"  opened           {p.opened_at}   DTE {p.dte_remaining}/{p.dte_at_open}")
        rprint(f"  credit / maxloss ${p.credit_received}  /  ${p.max_loss}")
        rprint(f"  current value    ${p.current_value}  →  P/L [bold]${p.pl_dollars}[/bold] "
               f"({_pct(p.pl_pct_of_max_profit)} of max profit)")
        rprint(f"  expected by now  {_pct(p.expected_pl_pct)}   "
               f"→ {'AHEAD' if (p.pl_pct_of_max_profit or 0) >= (p.expected_pl_pct or 0) else 'BEHIND'}")
        rprint(f"  underlying       ${p.underlying_price}   "
               f"dist to short strike {_pct(p.distance_to_short_strike_pct)}")
        rprint(f"  net greeks       Δ{p.net_delta} Γ{p.net_gamma} Θ{p.net_theta} V{p.net_vega}")
        rprint(f"  IV now / entry   {p.iv_now} / {p.iv_at_entry}  (Δ {_pct(p.iv_regime_delta_pct)})")
        rprint(f"  [bold]health {p.health_score}[/bold]  axes={p.health_axes}")


@app.command("sync")
def sync(
    daily_close: bool = typer.Option(
        False, "--daily-close", help="Mark this tick as the official end-of-day record "
                                     "(stores the full Position snapshot)."),
):
    """Run one sync tick and persist it: resolve, assemble, and write to the store.

    This is the unit the Timekeeper will call on a clock. Run it twice and the history
    starts accumulating — the same data the Rich view and MCP server read back.
    """
    from thetaglass.state.assemble import assemble_positions
    from thetaglass.store import Store

    with Store() as store:
        positions = assemble_positions(RobinhoodBroker(), store=store)
        seen = store.record_tick(positions, is_daily_close=daily_close)
    tag = " [dim](daily close)[/dim]" if daily_close else ""
    rprint(f"[green]synced[/green] {len(seen)} position(s){tag}")
    for p in positions:
        rprint(f"  {p.underlying} {p.strategy_type}  health [bold]{p.health_score}[/bold]")


@tk_app.command("run")
def tk_run(
    once: bool = typer.Option(False, "--once", help="Run a single tick and exit "
                              "(fires regardless of market hours; for testing/cron)."),
):
    """The raw worker loop — what PM2 (and Docker, as PID 1) actually execute.

    You usually want `tg timekeeper start` instead, which runs this under PM2 in the
    background. While the market is open it syncs every TICK_SECONDS and appends
    history; when closed it sleeps until the next session. State lives in the store, so
    it's safe to restart at any time.
    """
    from thetaglass.timekeeper import run as run_timekeeper

    run_timekeeper(once=once)


@tk_app.command("start")
def tk_start():
    """Launch the heartbeat under PM2 in the background (idempotent)."""
    _render_supervisor(supervisor.start())


@tk_app.command("stop")
def tk_stop():
    """Halt the heartbeat (stays registered with PM2 so `start` can revive it)."""
    _render_supervisor(supervisor.stop())


@tk_app.command("restart")
def tk_restart():
    """Restart the heartbeat — use after a code change."""
    _render_supervisor(supervisor.restart())


@tk_app.command("status")
def tk_status():
    """Is the daemon up, and when did it LAST actually sync (from the store)?"""
    st = supervisor.status()
    proc = st["process"]
    pstatus = proc.get("status", "unknown")
    color = "green" if pstatus == "online" else "red" if pstatus == "not_registered" else "yellow"
    rprint(f"process     [{color}]{pstatus}[/{color}]"
           + (f"  pid {proc['pid']}" if proc.get("pid") else "")
           + (f"  up {_dur(proc['uptime_seconds'])}" if proc.get("uptime_seconds") else "")
           + (f"  ↺ {proc['restarts']}" if proc.get("restarts") is not None else ""))
    rprint(f"last tick   {st['last_tick_at'] or '[dim]never[/dim]'}")
    rprint(f"open pos.   {st['open_positions'] if st['open_positions'] is not None else '—'}")
    if not st["pm2_available"]:
        rprint("[yellow]PM2 not on PATH — `npm install -g pm2` to supervise.[/yellow]")


@tk_app.command("logs")
def tk_logs(
    follow: bool = typer.Option(True, "--follow/--no-follow", help="Stream (Ctrl-C to exit)."),
    lines: int = typer.Option(40, "--lines", help="Lines of history to show first."),
):
    """Tail the Timekeeper's PM2 logs."""
    import shutil
    import subprocess

    if not shutil.which("pm2"):
        rprint("[red]PM2 not found on PATH.[/red] Install with: npm install -g pm2")
        raise typer.Exit(1)
    args = ["pm2", "logs", supervisor.APP_NAME, "--lines", str(lines)]
    if not follow:
        args.append("--nostream")
    raise typer.Exit(subprocess.run(args).returncode)


def _render_supervisor(res: dict) -> None:
    if not res.get("ok"):
        rprint(f"[red]{res.get('action', 'error')}[/red]: {res.get('error', 'unknown error')}")
        raise typer.Exit(1)
    app_st = res.get("app") or {}
    extra = f"  ([dim]{app_st.get('status')}[/dim])" if app_st.get("status") else ""
    rprint(f"[green]{res['action']}[/green] {supervisor.APP_NAME}{extra}")


def _dur(seconds: int | None) -> str:
    if not seconds:
        return "—"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m}m" if h else f"{m}m{s}s" if m else f"{s}s"


@app.command("backfill")
def backfill():
    """Backfill real underlying price history (for the price line + realized volatility).

    Fetches daily bars for each open position's underlying, from before it opened.
    """
    from thetaglass.backfill import backfill_for_positions
    from thetaglass.state.assemble import assemble_positions
    from thetaglass.store import Store

    broker = RobinhoodBroker()
    with Store() as store:
        positions = assemble_positions(broker, store=store)
        res = backfill_for_positions(broker, store, positions)
    if not res:
        rprint("[yellow]No open positions to backfill.[/yellow]")
        return
    for sym, n in res.items():
        rprint(f"[green]backfilled[/green] {sym}: {n} daily bars")


@app.command("monitor")
def monitor(
    mock: bool = typer.Option(None, "--mock/--no-mock",
                              help="Include a synthetic position. Default: auto-add when "
                                   "fewer than two real positions exist."),
):
    """Interactive drill-down dashboard: ↑/↓ to select a position, chart updates live.

    Top: the selected position's underlying + P/L cone charts. Bottom: an arrow-navigable
    list of dense position cards.
    """
    from thetaglass.backfill import backfill_for_positions, entry_iv_for_position
    from thetaglass.mock import closes_from_history, make_mock_book
    from thetaglass.state.assemble import assemble_positions
    from thetaglass.store import Store
    from thetaglass.view.monitor import run_monitor

    broker = RobinhoodBroker()
    with Store() as store:
        # ensure real underlying history is present (for the price line + RV)
        try:
            backfill_for_positions(broker, store, assemble_positions(broker, store=store))
        except Exception as e:  # offline / auth issue — fall back to snapshot prices
            rprint(f"[yellow]Backfill skipped ({e}); charts use snapshot prices.[/yellow]")
        entries = []
        for p in store.current_positions():
            hist = store.history(p["position_id"])
            closes = store.equity_closes(p["underlying"]) or closes_from_history(hist)
            # reconstruct the true entry IV from the fill (so the IV cell reads vs where
            # we actually sold, and can backfill the pre-watch gap)
            try:
                iv0 = entry_iv_for_position(broker, p)
                if iv0:
                    p["iv_at_entry"] = round(iv0, 4)
            except Exception:
                pass
            entries.append((p, hist, closes))

    add_mock = mock if mock is not None else (len(entries) < 2)
    if add_mock:
        entries += make_mock_book(1)
        rprint("[dim]Including a MOCK position for demonstration.[/dim]")
    if not entries:
        rprint("[yellow]No positions to show. Run `tg sync` or use --mock.[/yellow]")
        raise typer.Exit()
    run_monitor(entries)


@app.command("status")
def status():
    """The overview: a Gantt timeline of every open position (reads the store).

    Each row spans [open → expiration] on a shared time axis — solid bar = elapsed
    (colored by health), dashed tail = the decay runway ahead, with one NOW marker.
    """
    from rich.console import Console

    from thetaglass.store import Store
    from thetaglass.view import render_overview

    with Store() as store:
        rows = store.current_positions()
    console = Console()
    console.print(render_overview(rows, width=console.width))


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def _instrument_ids(positions: list[dict]) -> list[str]:
    """Best-effort scrape of option instrument ids off raw position legs.

    We don't yet know RH's exact field name (option_id / instrument_id / option /
    url-with-id), so try the common ones. The dump output tells us the truth.
    """
    ids: list[str] = []
    keys = ("instrument_id", "option_id", "id", "option")
    for p in positions:
        for k in keys:
            v = p.get(k)
            if isinstance(v, str) and v:
                ids.append(v.rstrip("/").split("/")[-1])  # handle url-form ids
                break
    return ids


if __name__ == "__main__":
    app()
