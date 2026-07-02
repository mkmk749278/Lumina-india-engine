"""Docker healthcheck — verifies the engine scan loop is alive.

Checks that the heartbeat file written by the scan loop is fresh (within
120s). Returns exit code 0 (healthy) or 1 (unhealthy). During initial
startup the heartbeat may not exist yet — that's healthy (start_period
covers it).
"""

import sys
import time
from pathlib import Path

_HEARTBEAT = Path("/tmp/india_engine_heartbeat")
_MAX_AGE_SEC = 120

if not _HEARTBEAT.exists():
    sys.exit(0)

try:
    ts = float(_HEARTBEAT.read_text().strip())
    age = time.time() - ts
    if age > _MAX_AGE_SEC:
        print(f"heartbeat stale: {age:.0f}s old")
        sys.exit(1)
except (ValueError, OSError):
    sys.exit(1)

sys.exit(0)
