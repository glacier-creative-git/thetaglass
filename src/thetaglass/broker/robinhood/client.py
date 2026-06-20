"""Deterministic Robinhood MCP client — hits the MCP endpoint like an API, no LLM.

MCP is just JSON-RPC over authenticated HTTP; the LLM is the *usual* client, not a
required one. This connects with a bearer token (from AuthStore), runs the standard
initialize handshake, calls a tool with fixed arguments, and parses the JSON back.
Nothing here wakes any agent — the Timekeeper uses this directly on its own clock.

Read-only: only data accessors are exposed. Order tools are deliberately absent.

Ported from Chronotether's proven broker; the read surface is reused as-is.
"""
from __future__ import annotations

import asyncio
import json

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from thetaglass.broker.base import Broker
from thetaglass.broker.robinhood.auth import AuthStore

RH_MCP_URL = "https://agent.robinhood.com/mcp/trading"
QUOTE_BATCH = 20  # RH caps get_option_quotes at 20 instruments per call


def _extract(result) -> dict:
    """Pull the JSON payload out of an MCP CallToolResult's text content."""
    parts = []
    for c in getattr(result, "content", None) or []:
        text = getattr(c, "text", None)
        if text is not None:
            parts.append(text)
    blob = "".join(parts)
    if not blob:
        return {}
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        return {"raw": blob}


def _positions(d: dict) -> list[dict]:
    return (d.get("data") or {}).get("positions", []) if isinstance(d, dict) else []


class RobinhoodBroker(Broker):
    def __init__(self, auth: AuthStore | None = None):
        self.auth = auth or AuthStore()

    async def _acall(self, name: str, args: dict | None):
        token = self.auth.get_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        # terminate_on_close=False: RH's MCP server doesn't implement the optional
        # session-DELETE, which otherwise logs a benign "Session termination failed: 400".
        async with streamablehttp_client(
            RH_MCP_URL, headers=headers, terminate_on_close=False
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(name, args or {})

    def call(self, name: str, args: dict | None = None) -> dict:
        """Synchronous, deterministic tool call. Returns parsed JSON."""
        return _extract(asyncio.run(self._acall(name, args)))

    # ---- Broker interface (read-only) ----
    def get_accounts(self) -> list[dict]:
        d = self.call("get_accounts", {})
        return (d.get("data") or {}).get("accounts", []) if isinstance(d, dict) else []

    def get_option_positions(self, account_number: str) -> list[dict]:
        return _positions(self.call(
            "get_option_positions", {"account_number": account_number, "nonzero": True}))

    def get_option_quotes(self, instrument_ids: list[str]) -> list[dict]:
        """Live bid/ask/IV/Greeks for option instruments. Auto-batched at 20/call."""
        ids = list(instrument_ids)
        out: list[dict] = []
        for i in range(0, len(ids), QUOTE_BATCH):
            batch = ids[i:i + QUOTE_BATCH]
            d = self.call("get_option_quotes", {"instrument_ids": batch})
            data = d.get("data") or {} if isinstance(d, dict) else {}
            out.extend(r.get("quote") or {} for r in (data.get("results") or []))
        return out

    def get_option_instruments(self, instrument_ids: list[str]) -> list[dict]:
        """Resolve strike/type/expiration for specific instrument UUIDs (cache these).

        RH's `ids` param looks them up directly — no need to page a whole chain.
        """
        ids = list(instrument_ids)
        if not ids:
            return []
        d = self.call("get_option_instruments", {"ids": ",".join(ids)})
        data = d.get("data") or {} if isinstance(d, dict) else {}
        return data.get("instruments") or data.get("results") or []

    def get_equity_quotes(self, symbols: list[str]) -> list[dict]:
        """Underlying spot quotes (for distance-to-short-strike)."""
        syms = list(symbols)
        if not syms:
            return []
        d = self.call("get_equity_quotes", {"symbols": syms})
        data = d.get("data") or {} if isinstance(d, dict) else {}
        return data.get("results") or data.get("quotes") or []

    # ---- extra read helpers (not on the Broker interface; RH-specific niceties) ----
    def get_portfolio(self, account_number: str) -> dict:
        return self.call("get_portfolio", {"account_number": account_number})

    def get_equity_positions(self, account_number: str) -> list[dict]:
        return _positions(self.call("get_equity_positions", {"account_number": account_number}))
