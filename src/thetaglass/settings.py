"""Process-wide paths and config. Deliberately tiny for now."""
from __future__ import annotations

import os
from pathlib import Path

# Repo root = two levels up from this file (src/thetaglass/settings.py -> repo).
# Overridable via THETAGLASS_HOME for Docker/alt deployments later.
REPO_ROOT = Path(__file__).resolve().parents[2]
HOME = Path(os.environ.get("THETAGLASS_HOME", REPO_ROOT))

# All mutable local state lives under var/ (gitignored): credentials, the state DB, logs.
VAR_DIR = HOME / "var"
CRED_DIR = VAR_DIR / "credentials"
DB_PATH = VAR_DIR / "thetaglass.db"


class Config:
    """The tunable knobs (ratified defaults from docs/STATE_MACHINE.md §5).

    These encode trading judgment, not structure — change them here and the health
    math shifts; nothing else does. v1 keeps them as constants; a later pass can move
    them to per-position rules (configure_threshold_rule).
    """
    # --- decay baseline (Layer D1) ---
    DECAY_EXPONENT = 0.5            # √time. <1 = decay accelerates toward expiry.

    # --- health score (Layer D2) ---
    W_THETA = 0.4                  # weight: are we on the expected decay track
    W_STRIKE = 0.4                 # weight: price cushion to the short strike
    W_IV = 0.2                     # weight: IV stability vs entry
    CRIT = 0.34                    # any axis below this floors the whole score

    # --- thresholds (also drive alerts later) ---
    BREACH_THRESHOLD_PCT = 0.03    # price cushion that counts as "safe"
    IV_ALERT_THRESHOLD_PCT = 0.15  # IV jump from entry that's alarming

    # --- lifecycle ---
    CLOSE_GRACE_TICKS = 2          # consecutive missing ticks before marking closed


CONFIG = Config()
