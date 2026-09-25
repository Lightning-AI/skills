"""The Claude Code `statusLine` setting: finding it, and the block that adds the bars."""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .core import ENTRY_SCRIPT
from .store import read_json

SETTINGS_FILES = (".claude/settings.local.json", ".claude/settings.json")


def statusline_setting(project_dir: str) -> Tuple[Optional[str], Optional[str]]:
    """(settings file, command) of the first status line configured for this project, if any."""
    home = str(Path.home() / ".claude" / "settings.json")
    for path in [os.path.join(project_dir, f) for f in SETTINGS_FILES] + [home]:
        data = read_json(Path(path))
        cmd = ((data or {}).get("statusLine") or {}).get("command")
        if cmd:
            return path, cmd
    return None, None


def statusline_snippet(existing: Optional[str]) -> Dict[str, Any]:
    ours = f"python3 {shlex.quote(ENTRY_SCRIPT)} statusline"
    if existing and "progress.py" not in existing:
        # keep the user's own status line: feed the same input to both, theirs first
        ours = f"sh -c {shlex.quote('in=$(cat); printf %s "$in" | ' + existing + '; printf %s "$in" | ' + ours)}"
    return {"statusLine": {"type": "command", "command": ours, "refreshInterval": 3}}


def statusline_hint(run: str, project_dir: str) -> Optional[str]:
    path, cmd = statusline_setting(project_dir)
    if cmd and "progress.py" in cmd:
        return None
    where = os.path.join(project_dir, SETTINGS_FILES[0])
    return (f"status-line bar is not set up. Ask the user whether to add it; `progress.py statusline --config` "
            f"prints the setting for {where}" + (f" (it keeps their current status line from {path})" if cmd else ""))
