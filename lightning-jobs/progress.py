#!/usr/bin/env python3
"""Live progress, ETA and setback tracking for Lightning AI jobs.

    progress.py watch JOB [--run RUN] [--teamspace OWNER/TS] [--note TEXT]
    progress.py watch --studio STUDIO --log PATH [--run RUN] [--teamspace OWNER/TS]
    progress.py abandon RUN [--note TEXT]
    progress.py events [--run RUN] [--from-start] [--all]
    progress.py statusline [--config]

`watch` is the background poller. It follows the job's logs, reads `PROGRESS <step>/<total>`
lines (tqdm bars as a fallback) and `PROGRESS_PHASE <name>` stage markers, and tracks a *run*:
a chain of attempts that survives a failed job being relaunched under a new name. Running
`watch NEW_JOB --run RUN` while a poller for RUN is alive hands the new job to that poller
instead of starting a second one.

`events` prints the events that need the agent (stalls, setbacks, failures, final state) and is
meant as a Claude Code Monitor command. `statusline` draws the bars for the Claude Code status
line. Both only read files and make no network calls, so they work inside an agent sandbox.

State lives in `--dir`, else $LIGHTNING_PROGRESS_DIR, else ~/.local/state/lightning-progress:
one place per user, so the poller, the Monitor and the status line agree whatever directory each
runs in. Only `watch` writes it.
Only `watch` needs lightning_sdk; everything else is standard library. The code lives in the
job_progress package next to this file; each command loads only its own module.
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys

from job_progress.core import STUDIO_HOME


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--dir",
        help="state folder, shared by every command (default: $LIGHTNING_PROGRESS_DIR, "
        "else ~/.local/state/lightning-progress)",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("watch", help="follow a job's or Studio log's progress (run in the background)")
    w.add_argument("job", nargs="?", help="job name (or use --studio and --log)")
    w.add_argument("--studio", help="Studio whose log file to tail instead of a job")
    w.add_argument("--log", help=f"log file inside the Studio; relative paths start at {STUDIO_HOME}")
    w.add_argument("--run", help="run this belongs to (default: the job name, or the log file's stem)")
    w.add_argument("--teamspace", help="owner/teamspace (default: the CLI's configured teamspace)")
    w.add_argument("--note", help="why this job was (re)launched, shown in the run history")
    w.add_argument(
        "--query",
        help="server-side log filter, e.g. PROGRESS for very chatty jobs (hides error lines from stall causes)",
    )
    w.add_argument(
        "--relaunch-wait",
        type=float,
        default=1800.0,
        help="seconds a failed run waits for a relaunch before it is final (default 1800)",
    )
    w.set_defaults(module="watch", fn="cmd_watch")

    a = sub.add_parser("abandon", help="end a failed run's wait for a relaunch")
    a.add_argument("run")
    a.add_argument("--note")
    a.set_defaults(module="watch", fn="cmd_abandon")

    e = sub.add_parser("events", help="print notable events as they happen (for a Monitor)")
    e.add_argument("--run", help="only this run; exit once it reaches a final state")
    e.add_argument("--from-start", action="store_true", help="replay earlier events first")
    e.add_argument(
        "--all",
        action="store_true",
        help="also print routine progress (milestones, stage changes); for sessions "
        "without the status-line bar, such as the desktop app",
    )
    e.set_defaults(module="events", fn="cmd_events")

    s = sub.add_parser("statusline", help="render progress bars for the Claude Code status line")
    s.add_argument(
        "--config",
        action="store_true",
        help="print the statusLine setting to add (run from the directory Claude Code was started in)",
    )
    s.set_defaults(module="statusline", fn="cmd_statusline")

    args = ap.parse_args(argv)
    if args.dir:
        os.environ["LIGHTNING_PROGRESS_DIR"] = args.dir
    # import only the command's module, so the status line never loads the poller or the SDK
    return getattr(importlib.import_module(f"job_progress.{args.module}"), args.fn)(args)


if __name__ == "__main__":
    sys.exit(main())
