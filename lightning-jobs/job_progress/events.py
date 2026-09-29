"""The Monitor feed: prints the events that need the agent's attention."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from .core import FINAL_PHASES
from .store import progress_dir, read_json, session_id

# progress the status-line bar already shows; passing these on only wakes the agent to repeat it
ROUTINE_KINDS = ("milestone", "stage", "started", "recovered", "watching")


def wanted(e: dict[str, Any], run: str | None, all_kinds: bool) -> bool:
    if run and e.get("run") != run:
        return False
    return all_kinds or e.get("kind") not in ROUTINE_KINDS


def cursor_path(run: str | None) -> Path:
    """Where this session's Monitor for `run` keeps its place in the events log.

    In the temp folder, which an agent sandbox can write, unlike the state folder. One per
    session, run and state folder, so a re-armed Monitor picks up where the last one stopped.
    """
    key = f"{progress_dir()}|{session_id()}|{run}"
    return Path(tempfile.gettempdir()) / "lightning-progress-cursors" / hashlib.sha1(key.encode()).hexdigest()


def read_cursor(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def write_cursor(path: Path, offset: int) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(offset))
    except OSError:
        pass  # without a cursor, the next Monitor starts from the run's latest poller instead


def parse(line: bytes) -> dict[str, Any] | None:
    try:
        e = json.loads(line.decode("utf-8", "replace"))
    except ValueError:
        return None
    return e if isinstance(e, dict) else None


def latest_watch(f: Any, run: str | None) -> int:
    """Offset of the run's latest `watching` event: where its current poller started. A run name
    can be reused, so an end before it belongs to an earlier run and doesn't end this Monitor."""
    latest = -1
    if run:
        f.seek(0)
        while True:
            at = f.tell()
            line = f.readline()
            if not line:
                break
            e = parse(line)
            if e and e.get("run") == run and e.get("kind") == "watching":
                latest = at
        f.seek(0)
    return latest


def start_offset(f: Any, run: str | None) -> int:
    """For a Monitor with no cursor: where the run's latest poller started, so nothing it
    reported is missed. Failing that, the end of the log, passing on the hints before it, which
    are for the agent."""
    start, hints = None, []
    while True:
        at = f.tell()
        line = f.readline()
        if not line:
            break
        e = parse(line)
        if e is None or (run and e.get("run") != run):
            continue
        if run and e.get("kind") == "watching":
            start = at
        elif e.get("kind") == "hint":
            hints.append(e["msg"])
    if start is not None:
        return start
    for h in hints:
        print(h, flush=True)
    return f.tell()


def cmd_events(args: argparse.Namespace) -> int:
    # read-only in the state folder, so it runs inside an agent sandbox that can't write there;
    # the poller (run outside the sandbox) creates the file
    path = progress_dir() / "events.jsonl"
    existed = path.exists()
    while not path.exists():
        time.sleep(0.5)
    cursor = cursor_path(args.run)
    with open(path, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        saved = read_cursor(cursor)
        current = latest_watch(f, args.run)
        if args.from_start or not existed:
            pass  # a file that appeared after this Monitor started holds only new events
        elif saved is not None and saved <= size:
            f.seek(saved)  # carry on where this session's last Monitor stopped
        else:
            f.seek(start_offset(f, args.run))
        caught_up = False
        while True:
            at = f.tell()
            line = f.readline()
            if not line.endswith(b"\n"):  # nothing new, or a line still being written
                f.seek(at)
                if args.run and not caught_up:
                    caught_up = True
                    # this session already reported the run's end; a new poller would add events
                    state = read_json(progress_dir() / "state" / f"{args.run}.json") or {}
                    if state.get("phase") in FINAL_PHASES and saved is not None:
                        print(f"{args.run}: {state['phase']} (already reported)", flush=True)
                        return 0
                time.sleep(0.5)
                continue
            write_cursor(cursor, f.tell())
            e = parse(line)
            if e is None or not wanted(e, args.run, args.all):
                continue
            if args.run and e.get("kind") in FINAL_PHASES:
                if at < current:
                    print(f"{e['msg']} (an earlier run under this name)", flush=True)
                    continue
                print(e["msg"], flush=True)
                return 0
            print(e["msg"], flush=True)
