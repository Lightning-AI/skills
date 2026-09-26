"""The Claude Code `statusLine` setting: finding it, and the block that adds the bars."""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Any

from .store import LAUNCHER, progress_dir, read_json

SETTINGS_FILES = (".claude/settings.local.json", ".claude/settings.json")


def user_settings() -> str:
    return str(Path.home() / ".claude" / "settings.json")


def is_ours(cmd: str) -> bool:
    return LAUNCHER in cmd or "progress.py" in cmd  # progress.py: set up before the launcher existed


def statusline_setting(project_dir: str, user: bool = False) -> tuple[str | None, str | None]:
    """(settings file, command) of the status line that applies: the project's first, then the
    user's, as Claude Code resolves it. With `user`, only the user-level one."""
    files = [] if user else [os.path.join(project_dir, f) for f in SETTINGS_FILES]
    for path in [*files, user_settings()]:
        data = read_json(Path(path))
        cmd = ((data or {}).get("statusLine") or {}).get("command")
        if cmd:
            return path, cmd
    return None, None


def statusline_snippet(existing: str | None) -> dict[str, Any]:
    # the launcher lives in the state folder, so a custom folder needs no flag or env var
    ours = f"python3 {shlex.quote(os.path.abspath(progress_dir() / LAUNCHER))}"
    if existing and not is_ours(existing):
        # keep the user's own status line: feed the same input to both, theirs first
        both = 'in=$(cat); printf %s "$in" | ' + existing + '; printf %s "$in" | ' + ours
        ours = f"sh -c {shlex.quote(both)}"
    return {"statusLine": {"type": "command", "command": ours, "refreshInterval": 3}}


def statusline_hint(run: str, project_dir: str) -> str | None:
    path, cmd = statusline_setting(project_dir)
    if cmd and is_ours(cmd):
        return None
    return (
        "status-line bar is not set up. Ask the user once whether to add it for all projects "
        f"(`progress.py statusline --config --user`, for {user_settings()}; no question again in later "
        "sessions) or just this one (`progress.py statusline --config`, for "
        f"{os.path.join(project_dir, SETTINGS_FILES[0])})"
        + (f". Either keeps their current status line from {path}" if cmd else "")
    )
