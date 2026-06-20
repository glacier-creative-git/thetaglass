"""Thetaglass CLI. v0: just enough to authenticate and inspect raw broker data.

State machine, status tables, and alerts come later — right now this exists to prove
the Robinhood connection and let us look at the real shape of a live spread.
"""
from __future__ import annotations

import json

import typer
from rich import print as rprint
from rich.table import Table

from thetaglass.broker.robinhood.auth import AuthStore
from thetaglass.broker.robinhood.client import RobinhoodBroker

app = typer.Typer(no_args_is_help=True, add_completion=False,
                  help="Thetaglass — theta-decay watchdog for sold options.")
auth_app = typer.Typer(no_args_is_help=True, help="Robinhood authentication.")
app.add_typer(auth_app, name="auth")


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


@app.command("status")
def status():
    """Show the latest persisted state of every open position (reads the store).

    Minimal table for now — the Rich Gantt overview (Layer E1) is the next slice.
    """
    from thetaglass.store import Store

    with Store() as store:
        rows = store.current_positions()
    if not rows:
        rprint("[yellow]No open positions in the store. Run `tg sync` first.[/yellow]")
        return

    table = Table(title="Thetaglass — open positions")
    for col in ("underlying", "strategy", "P/L", "captured", "expected", "dist→K", "health"):
        table.add_column(col)
    for p in rows:
        health = p.get("health_score")
        color = "green" if (health or 0) >= 0.7 else "yellow" if (health or 0) >= 0.4 else "red"
        table.add_row(
            p.get("underlying", "?"),
            p.get("strategy_type", "?"),
            f"${p.get('pl_dollars')}",
            _pct(p.get("pl_pct_of_max_profit")),
            _pct(p.get("expected_pl_pct")),
            _pct(p.get("distance_to_short_strike_pct")),
            f"[{color}]{health}[/{color}]",
        )
    rprint(table)


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
