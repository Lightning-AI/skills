"""The on-disk state: one per-user folder of run state, run history and the events log."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

def progress_dir() -> Path:
    env = os.environ.get("LIGHTNING_PROGRESS_DIR")
    if env:
        return Path(env)
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(Path.home(), ".local", "state")
    return Path(base) / "lightning-progress"


def ensure_dirs(d: Path) -> None:
    (d / "state").mkdir(parents=True, exist_ok=True)
    (d / "runs").mkdir(parents=True, exist_ok=True)


def session_dir() -> str:
    """The directory Claude Code runs in, whose .claude/ settings the session reads."""
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def write_json(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, path)


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def append_events(d: Path, events: List[Dict[str, Any]]) -> None:
    if not events:
        return
    with open(d / "events.jsonl", "a") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
