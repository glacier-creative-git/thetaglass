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
