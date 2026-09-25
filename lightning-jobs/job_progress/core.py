"""Shared constants and formatting for the progress tools."""

from __future__ import annotations

import os
from typing import Any

# the command-line entry point; the status-line setting and the SDK re-exec both run it
ENTRY_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "progress.py")

BAR_WIDTH = 20
FINAL_PHASES = ("done", "failed", "stopped", "abandoned")
STUDIO_HOME = "/teamspace/studios/this_studio"
STATE_STALE_AFTER = 60.0
FINAL_VISIBLE_FOR = 600.0


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "…"
    s = max(0, round(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


def pct(step: int | None, total: int | None) -> int | None:
    if step is None or not total:
        return None
    return max(0, min(100, int(100 * step / total)))


def bar(step: int, peak: int, total: int, width: int = BAR_WIDTH) -> str:
    """`▓` done now, `▒` ground lost to a setback (current → peak), `░` still to do."""
    cur = max(0, min(width, round(width * step / total)))
    top = max(cur, min(width, round(width * peak / total)))
    return "▓" * cur + "▒" * (top - cur) + "░" * (width - top)


def make_event(kind: str, run: str, msg: str, at: float) -> dict[str, Any]:
    return {"ts": at, "run": run, "kind": kind, "msg": f"{run}: {msg}"}
