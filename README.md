# Thetaglass

> An hourglass for theta decay. A self-hosted watchdog for **sold** options that
> tracks each credit spread against its own decay curve — computing how far along
> it *should* be vs. where it actually is, scoring position health, and alerting
> when something breaches a threshold. Robinhood gives you the state (Greeks, IV);
> Thetaglass gives you the **progress** and a watchdog that watches on its own clock.

Read-only. Thetaglass never places, modifies, or closes orders.

## Status

Early scaffold. Working today:

- Robinhood OAuth (PKCE public client, phone-approved once, auto-refresh).
- Deterministic read-only Robinhood MCP client (no LLM in the data path).
- `tg dump` — raw inspection of accounts / option positions / live quotes.

Next: leg→strategy state machine, decay baseline + health scoring, the Timekeeper
+ watchdog, alert store, Telegram emitter, MCP server.

## Quickstart (dev)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e .

tg auth login                 # approve the printed URL on your phone
tg auth complete '<code>'     # paste the code from the redirect
tg dump --out var/dump.json   # inspect a real position payload
```

Secrets live in `var/credentials/` (gitignored, chmod 600). Nothing sensitive is
ever committed.
