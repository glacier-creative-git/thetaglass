"""Process control for the Timekeeper — a thin, structured wrapper over the PM2 CLI.

The daemon (`tg run`) is the worker; this is the supervisor that asks PM2 to keep it
alive in the background. PM2 is a separate Node program with no Python API, so we shell
out to its CLI.

Every function returns a plain dict (never prints, never raises on the expected failure
paths) so both the `tg timekeeper` CLI and a future MCP tool can call the same code and
render it their own way — one implementation, two front-ends. In Docker there's no PM2:
you run `tg run` directly as the container's process, and this module is simply unused.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time

from thetaglass.settings import REPO_ROOT
from thetaglass.store import Store

APP_NAME = "thetaglass-timekeeper"
ECOSYSTEM = REPO_ROOT / "ecosystem.config.js"
_PM2_MISSING = {
    "ok": False, "code": "pm2_missing",
    "error": "PM2 not found on PATH. Install it with:  npm install -g pm2",
}


def _pm2() -> str | None:
    return shutil.which("pm2")


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([_pm2(), *args], capture_output=True, text=True)


def app_state() -> dict | None:
    """PM2's view of our app, or None if it isn't registered."""
    cp = _run(["jlist"])
    if cp.returncode != 0:
        return None
    try:
        apps = json.loads(cp.stdout or "[]")
    except json.JSONDecodeError:
        return None
    for a in apps:
        if a.get("name") == APP_NAME:
            env = a.get("pm2_env", {})
            up = env.get("pm_uptime")
            online = env.get("status") == "online"
            uptime = round((time.time() * 1000 - up) / 1000) if (up and online) else None
            return {
                "status": env.get("status"),
                "restarts": env.get("restart_time"),
                "pid": a.get("pid"),
                "uptime_seconds": uptime,
            }
    return None


def start() -> dict:
    """Launch under PM2. Idempotent: no-op if already online, (re)start otherwise."""
    if not _pm2():
        return _PM2_MISSING
    st = app_state()
    if st and st["status"] == "online":
        return {"ok": True, "action": "already_running", "app": st}
    if st:                                  # registered but stopped/errored → revive
        cp = _run(["restart", APP_NAME])
    else:                                   # first registration → load the ecosystem
        cp = _run(["start", str(ECOSYSTEM)])
    return _result(cp, "started")


def stop() -> dict:
    """Halt the process but leave it registered with PM2 (so `start` can revive it)."""
    if not _pm2():
        return _PM2_MISSING
    if app_state() is None:
        return {"ok": True, "action": "not_running"}
    return _result(_run(["stop", APP_NAME]), "stopped")


def restart() -> dict:
    """Restart to pick up a code change; starts it fresh if not registered."""
    if not _pm2():
        return _PM2_MISSING
    if app_state() is None:
        return start()
    return _result(_run(["restart", APP_NAME]), "restarted")


def status() -> dict:
    """Blend PM2's process view with the store's truth: when did we LAST actually sync.

    The process can be 'online' yet stalled; the last tick time from the store is the
    honest liveness signal, and it's exactly what an agent would want over MCP too.
    """
    proc = app_state() or {"status": "not_registered"}
    last_tick = open_positions = None
    try:
        with Store() as s:
            last_tick = s.last_tick_at()
            open_positions = len(s.current_positions())
    except Exception as e:                  # store not created yet, etc. — non-fatal
        proc.setdefault("store_error", str(e))
    return {
        "ok": True,
        "pm2_available": _pm2() is not None,
        "process": proc,
        "last_tick_at": last_tick,
        "open_positions": open_positions,
    }


def _result(cp: subprocess.CompletedProcess, action: str) -> dict:
    ok = cp.returncode == 0
    out = {"ok": ok, "action": action if ok else "failed", "app": app_state()}
    if not ok:
        out["error"] = (cp.stderr or cp.stdout or "").strip()
    return out
