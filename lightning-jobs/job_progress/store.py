"""The on-disk state: one per-user folder of run state, run history and the events log."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .core import ENTRY_SCRIPT


def progress_dir() -> Path:
    env = os.environ.get("LIGHTNING_PROGRESS_DIR")
    if env:
        return Path(env)
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(Path.home(), ".local", "state")
    return Path(base) / "lightning-progress"


def ensure_dirs(d: Path) -> None:
    (d / "state").mkdir(parents=True, exist_ok=True)
    (d / "runs").mkdir(parents=True, exist_ok=True)


# The status-line setting points here rather than at progress.py: plugin files live in a folder
# named after the plugin version, which disappears on update. This launcher never changes; it
# runs whichever progress.py last recorded itself in ENTRY_FILE, and prints nothing if that's gone.
LAUNCHER = "lightning-progress-statusline.py"
ENTRY_FILE = "entry.txt"
LAUNCHER_CODE = '''\
"""Runs the newest lightning-jobs progress.py for the Claude Code status line."""
import os
import runpy
import sys

here = os.path.dirname(os.path.abspath(__file__))
try:
    with open(os.path.join(here, "entry.txt")) as f:
        entry = f.read().strip()
except OSError:
    sys.exit(0)
if not os.path.isfile(entry):
    sys.exit(0)
sys.argv = [entry, "--dir", here, "statusline"]
sys.path.insert(0, os.path.dirname(entry))
runpy.run_path(entry, run_name="__main__")
'''


def install_launcher(d: Path) -> Path:
    """Write the launcher and point it at this copy of progress.py; returns the launcher's path."""
    d.mkdir(parents=True, exist_ok=True)
    launcher = d / LAUNCHER
    if not launcher.exists() or launcher.read_text() != LAUNCHER_CODE:
        launcher.write_text(LAUNCHER_CODE)
    (d / ENTRY_FILE).write_text(ENTRY_SCRIPT + "\n")
    return launcher


def session_dir() -> str:
    """The directory Claude Code runs in, whose .claude/ settings the session reads."""
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def write_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, path)


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def append_events(d: Path, events: list[dict[str, Any]]) -> None:
    if not events:
        return
    with open(d / "events.jsonl", "a") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
