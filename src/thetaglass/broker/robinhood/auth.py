"""Robinhood OAuth — a standalone, deterministic token manager (no LLM, no Claude).

This is what lets the Timekeeper hit the Robinhood MCP "like an API". It implements
the standard OAuth 2.1 + PKCE public-client flow with dynamic client registration,
exactly as advertised by Robinhood's discovery metadata:

    authorize : https://robinhood.com/oauth
    token     : https://api.robinhood.com/oauth2/token/
    register  : https://agent.robinhood.com/oauth/trading/register   (DCR, public client)
    scope     : internal   ·   PKCE S256   ·   no client secret

Tokens are stored in var/credentials/robinhood.json (gitignored, chmod 600). After a
one-time phone approval, the refresh token silently mints access tokens forever.

The login is split into two steps so it works over SSH / from chat:
    tg auth login              -> prints the URL (approve on your phone)
    tg auth complete '<code>'  -> exchanges the pasted code for tokens

Ported from the proven Chronotether implementation; this connection path is the one
piece we know works, so it is reused as-is rather than rebuilt.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse

import httpx

from thetaglass.settings import CRED_DIR

CRED_FILE = CRED_DIR / "robinhood.json"
PENDING_FILE = CRED_DIR / "robinhood_pending.json"

ASM_URL = "https://agent.robinhood.com/.well-known/oauth-authorization-server"
REDIRECT_URI = "http://localhost:9876/callback"   # loopback (RFC 8252); we paste the code back
SCOPE = "internal"


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class AuthStore:
    def __init__(self):
        CRED_DIR.mkdir(parents=True, exist_ok=True)
        self.creds = self._load(CRED_FILE)

    # ---- tiny json helpers ----
    @staticmethod
    def _load(p):
        return json.load(open(p)) if os.path.exists(p) else {}

    @staticmethod
    def _save(p, d):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = str(p) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, indent=2)
        os.replace(tmp, p)
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass

    # ---- discovery + dynamic client registration ----
    def _discover(self):
        with httpx.Client(timeout=20) as c:
            return c.get(ASM_URL).json()

    def _register_client(self, asm):
        body = {
            "client_name": "Thetaglass",
            "redirect_uris": [REDIRECT_URI],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": SCOPE,
        }
        with httpx.Client(timeout=20) as c:
            r = c.post(asm["registration_endpoint"], json=body)
            r.raise_for_status()
            return r.json()["client_id"]

    # ---- step 1: build the authorization URL ----
    def begin_login(self) -> str:
        asm = self._discover()
        client_id = self._register_client(asm)
        verifier = _b64url(secrets.token_bytes(40))
        challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
        state = secrets.token_urlsafe(16)
        params = {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPE,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        url = asm["authorization_endpoint"] + "?" + urllib.parse.urlencode(params)
        self._save(PENDING_FILE, {
            "verifier": verifier, "state": state, "client_id": client_id,
            "token_endpoint": asm["token_endpoint"],
        })
        return url

    # ---- step 2: exchange the pasted code (or full redirect URL) for tokens ----
    def complete_login(self, code_or_url: str) -> dict:
        pend = self._load(PENDING_FILE)
        if not pend:
            raise RuntimeError("no pending login — run `tg auth login` first")
        code = code_or_url.strip().strip("'\"")
        if "code=" in code:                       # accept a full redirect URL or query string
            code = urllib.parse.unquote(code.split("code=", 1)[1].split("&", 1)[0])
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": pend["client_id"],
            "code_verifier": pend["verifier"],
        }
        with httpx.Client(timeout=30) as c:
            r = c.post(pend["token_endpoint"], data=data)
            if r.status_code >= 400:
                raise RuntimeError(f"token exchange failed [{r.status_code}]: {r.text[:300]}")
            tok = r.json()
        self._store(tok, pend["client_id"], pend["token_endpoint"])
        try:
            os.remove(PENDING_FILE)
        except OSError:
            pass
        return tok

    def _store(self, tok, client_id, token_endpoint):
        self.creds = {
            "client_id": client_id,
            "token_endpoint": token_endpoint,
            "access_token": tok["access_token"],
            "refresh_token": tok.get("refresh_token"),
            "scope": tok.get("scope", SCOPE),
            "expires_at": time.time() + int(tok.get("expires_in", 3600)),
        }
        self._save(CRED_FILE, self.creds)

    # ---- refresh + accessor (the only thing the broker calls) ----
    def _refresh(self):
        c = self.creds
        data = {"grant_type": "refresh_token",
                "refresh_token": c["refresh_token"], "client_id": c["client_id"]}
        with httpx.Client(timeout=30) as cl:
            r = cl.post(c["token_endpoint"], data=data)
            if r.status_code >= 400:
                raise RuntimeError(f"token refresh failed [{r.status_code}] — re-run `tg auth login`")
            tok = r.json()
        c["access_token"] = tok["access_token"]
        if tok.get("refresh_token"):              # RH rotates refresh tokens
            c["refresh_token"] = tok["refresh_token"]
        c["expires_at"] = time.time() + int(tok.get("expires_in", 3600))
        self._save(CRED_FILE, c)

    def get_access_token(self) -> str:
        if not self.creds:
            raise RuntimeError("not authenticated — run `tg auth login`")
        if time.time() > self.creds.get("expires_at", 0) - 60:
            self._refresh()
        return self.creds["access_token"]

    def status(self) -> dict:
        if not self.creds:
            return {"authenticated": False}
        return {
            "authenticated": True,
            "scope": self.creds.get("scope"),
            "expires_in_sec": int(self.creds.get("expires_at", 0) - time.time()),
        }
