"""Role source-of-truth loader, split out of server.py (Phase 5 modularization,
pure move, no behavior change).

Role definitions are the single source of truth in roles.json (also served to
the browser as /roles.js, see server.py's roles_js() route). This module
derives the seed role maps from it once at import time so server.py and every
other _server module can share the exact same data without re-reading the
file or risking drift.
"""

import json
import os

_ROLES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "roles.json")
with open(_ROLES_PATH, encoding="utf-8") as _f:
    ROLES = json.load(_f)

# role -> preferred project (string or list, mirroring the client).
ROLE_PROJECT = {role: d["preferredProject"] for role, d in ROLES.items()}
