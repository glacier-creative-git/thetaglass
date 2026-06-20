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
