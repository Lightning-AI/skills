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


FEED = 'printf %s "$in" | '
CHAIN_HEAD = "in=$(cat); " + FEED


def users_part(cmd: str) -> str | None:
    """The user's own command out of one we set up earlier, or the whole command if not ours."""
    if not is_ours(cmd):
        return cmd
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return None
    if len(parts) == 3 and parts[:2] == ["sh", "-c"] and parts[2].startswith(CHAIN_HEAD):
        body = parts[2][len(CHAIN_HEAD) :]
        cut = body.rfind("; " + FEED)
        if cut > 0:
            return users_part(body[:cut])
    return None


def statusline_snippet(existing: str | None) -> dict[str, Any]:
    # the launcher lives in the state folder, so a custom folder needs no flag or env var
    ours = f"python3 {shlex.quote(os.path.abspath(progress_dir() / LAUNCHER))}"
    theirs = users_part(existing) if existing else None
    if theirs:
        # keep the user's own status line, also when setting up again: feed the same input to
        # both, theirs first
        both = CHAIN_HEAD + theirs + "; " + FEED + ours
        ours = f"sh -c {shlex.quote(both)}"
    return {"statusLine": {"type": "command", "command": ours, "refreshInterval": 3}}


def statusline_hint(run: str, project_dir: str) -> str | None:
    path, cmd = statusline_setting(project_dir)
    if cmd and is_ours(cmd):
        return None
    return (
        "status-line bar is not set up. Ask the user once whether to add it for all projects "
        f"(`progress.py statusline --config --user`, for {user_settings()}; no question again in later "
        "sessions) or just this project (`progress.py statusline --config --project-dir <the directory "
        "Claude Code was started in>`, for its .claude/settings.local.json)"
        + (f". Either keeps their current status line from {path}" if cmd else "")
    )
