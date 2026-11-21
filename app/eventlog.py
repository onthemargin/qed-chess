"""One JSON line per game event, for debugging games and chat in Cloud Logging.

Every record is `{"ev": "ask"|"play"|"engine"|"replay", ...}` on a single line on stdout,
tagged with the browser's session id (`X-QED-Session`) and game id (`X-QED-Game`) so one
game can be followed end to end. Client addresses are logged as a short hash, never raw.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import time

log = logging.getLogger("qed.events")
_ID = re.compile(r"^[A-Za-z0-9_-]{4,40}$")
_configured = False


def configure(level: str | None = None) -> None:
    """Send qed.* logs to stdout as bare lines (Cloud Run turns each into a log entry)."""
    global _configured
    if _configured:
        return
    _configured = True
    root = logging.getLogger("qed")
    root.setLevel(getattr(logging, (level or os.environ.get("QED_LOG_LEVEL", "INFO")).upper(), logging.INFO))
    if not any(getattr(h, "_qed", False) for h in root.handlers):
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("%(message)s"))
        h._qed = True  # type: ignore[attr-defined]
        root.addHandler(h)


def ids(request) -> dict:
    """Validated trace ids from the request headers; missing or malformed ones are dropped."""
    out = {}
    for key, header in (("sid", "x-qed-session"), ("gid", "x-qed-game")):
        v = request.headers.get(header, "") if request is not None else ""
        if v and _ID.match(v):
            out[key] = v
    return out


def ip_hash(ip: str) -> str:
    return hashlib.sha256(ip.encode("utf-8", "replace")).hexdigest()[:10]


def event(ev: str, **fields) -> None:
    rec = {"ev": ev}
    rec.update({k: v for k, v in fields.items() if v is not None})
    log.info(json.dumps(rec, ensure_ascii=False, default=str, separators=(",", ":")))


class Timer:
    def __init__(self):
        self.t0 = time.monotonic()

    def ms(self) -> int:
        return int((time.monotonic() - self.t0) * 1000)
