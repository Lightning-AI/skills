"""The Monitor feed: prints the events that need the agent's attention."""

from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any

from .core import FINAL_PHASES
from .store import progress_dir

# progress the status-line bar already shows; passing these on only wakes the agent to repeat it
ROUTINE_KINDS = ("milestone", "stage", "started", "recovered")


def wanted(e: dict[str, Any], run: str | None, all_kinds: bool) -> bool:
    if run and e.get("run") != run:
        return False
    return all_kinds or e.get("kind") not in ROUTINE_KINDS


def cmd_events(args: argparse.Namespace) -> int:
    # read-only, so it runs inside an agent sandbox that can't write the state folder; the poller
    # (run outside the sandbox) creates the file
    path = progress_dir() / "events.jsonl"
    existed = path.exists()
    while not path.exists():
        time.sleep(0.5)
    with open(path) as f:
        # a file that appeared after this Monitor started holds only new events
        if existed and not args.from_start:
            # hints are for the agent: pass on ones written before this Monitor started
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("kind") == "hint" and (not args.run or e.get("run") == args.run):
                    print(e["msg"], flush=True)
            f.seek(0, os.SEEK_END)
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.5)
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not wanted(e, args.run, args.all):
                continue
            print(e["msg"], flush=True)
            if args.run and e.get("kind") in FINAL_PHASES:
                return 0
