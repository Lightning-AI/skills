"""The background poller: follows a job's logs, or a log file in a Studio, into the state files.

The only module that talks to Lightning, so the only one that must run outside an agent sandbox.
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import shlex
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .core import ENTRY_SCRIPT, FINAL_PHASES, STUDIO_HOME, fmt_duration, make_event, pct
from .settings import statusline_hint
from .store import append_events, ensure_dirs, pid_alive, progress_dir, read_json, session_dir, write_json
from .tracker import (
    EXIT_RE,
    LogTail,
    end_attempt,
    finish,
    new_state,
    on_line,
    on_partial,
    on_tick,
    parse_error,
    recent_cause,
)

TERMINAL_STATUSES = ("Completed", "Failed", "Stopped")
SUPERVISE_INTERVAL = 5.0
STUDIO_INTERVAL = 10.0
STUDIO_READ_LIMIT = 4_000_000
COST_INTERVAL = 30.0


def cli_python() -> Optional[str]:
    """The interpreter named on the `lightning` CLI script's first line, e.g. its uv tool env."""
    cli = shutil.which("lightning")
    if not cli:
        return None
    try:
        with open(cli, "rb") as f:
            first = f.readline().decode(errors="replace").strip()
    except OSError:
        return None
    if not first.startswith("#!"):
        return None
    cand = first[2:].strip().split()[0]
    if not os.path.isabs(cand) or os.path.basename(cand) == "env" or not os.access(cand, os.X_OK):
        return None
    return cand


def ensure_sdk() -> None:
    """Re-run under the CLI's own Python when this one can't import lightning_sdk.

    With a `uv tool` or `pipx` install, the SDK lives only in the CLI's private env, so a plain
    `python3 progress.py watch` would fail on the import.
    """
    try:
        import lightning_sdk  # noqa: F401

        return
    except ImportError:
        pass
    py = cli_python()
    retried = os.environ.get("_LIGHTNING_PROGRESS_REEXEC") == "1"
    # compare unresolved paths: a venv's python is a symlink to the base interpreter, and only the
    # symlink's location selects the venv
    if py and not retried and os.path.abspath(py) != os.path.abspath(sys.executable):
        os.environ["_LIGHTNING_PROGRESS_REEXEC"] = "1"
        os.execv(py, [py, ENTRY_SCRIPT, *sys.argv[1:]])
    sys.exit(
        f"progress.py watch needs lightning_sdk, which {sys.executable} can't import"
        + (" (it is the `lightning` CLI's own Python)" if retried else "")
        + ". Run it with a Python that has the SDK, e.g. "
        "`uv run --with lightning-sdk python progress.py watch ...`."
    )


class Follower(threading.Thread):
    """Consumes one job's log stream into the shared state until the stream ends."""

    def __init__(self, name: str, teamspace: Optional[str], query: Optional[str], sink) -> None:
        super().__init__(daemon=True)
        self.name_, self.teamspace, self.query, self.sink = name, teamspace, query, sink
        self.abandoned = False
        self.error: Optional[str] = None

    def run(self) -> None:
        from lightning_sdk import Job

        try:
            job = Job(self.name_, teamspace=self.teamspace)
            for line in job.logs(follow=True, timestamps=True, query=self.query):
                if self.abandoned:
                    return
                self.sink(self.name_, line)
        except Exception as ex:  # a dropped stream is restarted by the supervisor
            self.error = f"{type(ex).__name__}: {ex}"


def preflight(job: Optional[str], studio: Optional[str], teamspace: Optional[str]) -> None:
    """Fail fast, with the fix, when the control plane is unreachable (the agent-sandbox case)."""
    from lightning_sdk import Job, Studio

    try:
        if studio:
            Studio(studio, teamspace=teamspace, create_ok=False)  # never create a Studio by accident
        else:
            Job(job, teamspace=teamspace)
    except Exception as ex:
        text = f"{type(ex).__name__}: {ex}"
        if re.search(r"NameResolution|resolve|ConnectionError|Max retries|timed out|ProxyError", text, re.I):
            sys.exit(
                f"progress.py watch can't reach the Lightning control plane ({text[:220]}). "
                "Inside an agent sandbox the SDK's requests fail even with lightning.ai allowed, "
                "so run `watch` outside the sandbox. `events` and `statusline` read local files only."
            )
        raise


def cmd_watch(args: argparse.Namespace) -> int:
    if bool(args.studio) != bool(args.log) or bool(args.job) == bool(args.studio):
        sys.exit("watch takes either a JOB name, or --studio NAME together with --log PATH")
    ensure_sdk()
    preflight(args.job, args.studio, args.teamspace)
    d = progress_dir()
    ensure_dirs(d)
    if args.studio:
        name = f"{args.studio}:{args.log}"
        entry = {"kind": "studio", "name": name, "studio": args.studio, "log": args.log}
        run = args.run or Path(args.log).stem
    else:
        name, entry, run = args.job, {"kind": "job", "name": args.job}, args.run or args.job
    entry.update(teamspace=args.teamspace, added_at=time.time())
    run_path, state_path = d / "runs" / f"{run}.json", d / "state" / f"{run}.json"
    runfile = read_json(run_path) or {"run": run, "jobs": [], "notes": [], "abandoned": False, "pid": None}
    if not any(j["name"] == name for j in runfile["jobs"]):
        runfile["jobs"].append(entry)
    if args.note:
        runfile["notes"].append({"at": time.time(), "job": name, "note": args.note})
    runfile["abandoned"] = False

    if pid_alive(runfile.get("pid")) and runfile.get("pid") != os.getpid():
        write_json(run_path, runfile)
        append_events(d, [make_event("relaunch", run, f"continuing with {name}"
                                 + (f" · {args.note}" if args.note else ""), time.time())])
        print(f"handed {name} to the running poller for {run} (pid {runfile['pid']})")
        return 0
    runfile["pid"] = os.getpid()
    write_json(run_path, runfile)
    hint = statusline_hint(run, session_dir())
    if hint:
        append_events(d, [make_event("hint", run, hint, time.time())])
        print(f"{run}: {hint}", flush=True)

    state = read_json(state_path) or new_state(run)
    if state["phase"] in FINAL_PHASES or state["phase"] == "waiting":
        state["phase"] = "pending"
        state["finished_at"] = None
    lock = threading.Lock()

    def save(events: List[Dict[str, Any]]) -> None:
        state["updated_at"] = time.time()
        write_json(state_path, state)
        append_events(d, events)
        for e in events:
            print(e["msg"], flush=True)

    def sink(job_name: str, line: str) -> None:
        with lock:
            events = on_line(state, job_name, line, time.time())
            if events or time.time() - (state["updated_at"] or 0) > 1:
                save(events)

    idx = next(i for i, j in enumerate(runfile["jobs"]) if j["name"] == name)
    while True:
        runfile = read_json(run_path) or runfile
        entry = runfile["jobs"][idx]
        with lock:
            if state["job"] and state["job"] != entry["name"]:
                state["issue_since"] = state["issue_since"] or state["last_sample_at"]
                state["job_running_since"] = None
                end_attempt(state, time.time())
            if state["phase"] == "waiting":
                state["phase"] = "pending"
            state["job"] = entry["name"]
            save([])
        if entry.get("kind") == "studio":
            outcome = supervise_studio(entry, state, lock, save, run_path, idx, args)
        else:
            outcome = supervise(entry, state, lock, save, sink, run_path, idx, args)
        runfile = read_json(run_path) or runfile
        newer = len(runfile["jobs"]) > idx + 1

        if outcome == "superseded" or (newer and outcome != "Completed"):
            idx += 1
            continue
        if outcome in ("FailedFinal", "Abandoned"):  # a Studio log already waited for its relaunch
            with lock:
                save([finish(state, "failed" if outcome == "FailedFinal" else "abandoned", time.time())])
            break
        if outcome == "Completed":
            with lock:
                save([finish(state, "done", time.time())])
            break
        if outcome == "Stopped":
            with lock:
                save([finish(state, "stopped", time.time())])
            break

        # Failed: keep the run open until a relaunch arrives, the agent abandons it, or time runs out.
        with lock:
            state["phase"] = "waiting"
            state["issue_since"] = state["issue_since"] or state["last_sample_at"] or time.time()
            cause = recent_cause(state, None)
            save([make_event("failed", run, f"{entry['name']} failed at {pct(state['step'], state['total']) or 0}%"
                         + (f" · {cause}" if cause else "")
                         + f" · waiting {fmt_duration(args.relaunch_wait)} for a relaunch", time.time())])
        deadline = time.time() + args.relaunch_wait
        outcome = "timeout"
        while time.time() < deadline:
            time.sleep(SUPERVISE_INTERVAL)
            runfile = read_json(run_path) or runfile
            if runfile.get("abandoned"):
                outcome = "abandoned"
                break
            if len(runfile["jobs"]) > idx + 1:
                outcome = "relaunched"
                break
            with lock:
                save([])
        if outcome == "relaunched":
            idx += 1
            continue
        with lock:
            save([finish(state, "abandoned" if outcome == "abandoned" else "failed", time.time())])
        break

    runfile = read_json(run_path) or runfile
    if runfile.get("pid") == os.getpid():
        runfile["pid"] = None
        write_json(run_path, runfile)
    return 0


def supervise(entry, state, lock, save, sink, run_path, idx, args) -> str:
    """Poll one job's status every few seconds and keep a log follower attached while it runs."""
    from lightning_sdk import Job

    job = Job(entry["name"], teamspace=entry.get("teamspace") or args.teamspace)
    follower: Optional[Follower] = None
    last_cost, terminal_since, failures = 0.0, None, 0
    while True:
        now = time.time()
        try:
            status = str(job.status)
            attempt = job.current_run_attempt
            if now - last_cost > COST_INTERVAL:
                last_cost = now
                cost = job.total_cost
            else:
                cost = None
            failures = 0
        except Exception as ex:
            failures += 1
            if failures >= 12:
                raise
            print(f"status check failed ({failures}): {ex}", file=sys.stderr, flush=True)
            time.sleep(SUPERVISE_INTERVAL)
            continue

        with lock:
            if cost is not None:
                state["cost"] = cost
            save(on_tick(state, status, attempt, now))

        runfile = read_json(run_path) or {}
        if len(runfile.get("jobs", [])) > idx + 1:  # the session handed this run a newer job
            if follower:
                follower.abandoned = True
            return "superseded"

        if status == "Running" and (follower is None or not follower.is_alive()):
            if follower and follower.error:
                print(f"log stream dropped, reattaching: {follower.error}", file=sys.stderr, flush=True)
            follower = Follower(entry["name"], entry.get("teamspace") or args.teamspace, args.query, sink)
            follower.start()

        if status in TERMINAL_STATUSES:
            terminal_since = terminal_since or now
            # let the follower drain the last lines (its stream ends once the job is finished)
            if follower is None or not follower.is_alive() or now - terminal_since > 30:
                if follower is None:
                    drain_saved_logs(entry, args, sink)
                return status
        time.sleep(SUPERVISE_INTERVAL)


def studio_read_command(path: str, offset: int) -> str:
    """Report the log's size, whether any process still has it open, and its new bytes as base64.

    `Studio.run_with_exit_code` strips its output, so raw log bytes would lose their trailing
    newline; base64 keeps them exact. The open-file check walks /proc, so it needs no extra tools.
    """
    if path.startswith("~/"):  # the Studio's home, not the local one
        path = STUDIO_HOME + path[1:]
    q = shlex.quote(path)
    return (
        f"cd {STUDIO_HOME} 2>/dev/null; f={q}; "
        'if [ ! -f "$f" ]; then echo NOFILE; exit 0; fi; '
        'a=$(readlink -f "$f"); w=0; '
        'for p in /proc/[0-9]*/fd/*; do [ "$(readlink "$p" 2>/dev/null)" = "$a" ] && { w=1; break; }; done; '
        's=$(wc -c < "$f" | tr -d " "); echo "SIZE $s WRITER $w"; '
        f'if [ "$s" -gt {offset} ]; then tail -c +{offset + 1} "$f" | head -c {STUDIO_READ_LIMIT} '
        '| base64 | tr -d "\\n"; fi'
    )


def parse_studio_read(out: str) -> Optional[Tuple[int, bool, bytes]]:
    m = re.search(r"^SIZE (\d+) WRITER ([01])$", out, re.M)
    if not m:
        return None
    b64 = out[m.end():].strip()
    return int(m.group(1)), m.group(2) == "1", base64.b64decode(b64) if b64 else b""


def supervise_studio(entry, state, lock, save, run_path, idx, args) -> str:
    """Tail a log file inside a Studio. A relaunch into the same file is the run's next attempt."""
    from lightning_sdk import Studio

    studio = Studio(entry["studio"], teamspace=entry.get("teamspace") or args.teamspace, create_ok=False)
    tail, offset, first, failures = LogTail(), 0, True, 0
    recent: List[str] = []  # last lines, to judge an exit that printed no PROGRESS_EXIT
    exited_at: Optional[float] = None  # set once the process is gone; cleared by a relaunch
    down_since: Optional[float] = None
    name = entry["name"]

    def new_attempt(now: float, why: str) -> None:
        nonlocal exited_at
        exited_at = None
        state["issue_since"] = state["issue_since"] or state["last_sample_at"] or now
        state["job_running_since"] = None
        state["phase"] = "pending"
        end_attempt(state, now)
        save([make_event("relaunch", state["run"], f"{why}: new attempt", now)])

    while True:
        now = time.time()
        try:
            status = str(studio.status)
            read = None
            if status == "Running":
                out, _ = studio.run_with_exit_code(studio_read_command(entry["log"], offset))
                read = parse_studio_read(out)
            failures = 0
        except Exception as ex:
            failures += 1
            if failures >= 12:
                raise
            print(f"studio check failed ({failures}): {ex}", file=sys.stderr, flush=True)
            time.sleep(STUDIO_INTERVAL)
            continue

        with lock:
            if status != "Running":
                down_since = down_since or now
                state["pending_note"] = f"Studio is {status}"
                save(on_tick(state, "Pending", None, now))
            elif read is None:
                down_since = None
                state["pending_note"] = f"waiting for {entry['log']}"
                save(on_tick(state, "Pending", None, now))
            else:
                down_since = None
                size, writer, chunk = read
                if size < offset:  # truncated or recreated: the agent relaunched into the same log
                    tail, offset, recent = LogTail(), 0, []
                    new_attempt(now, "log restarted")
                    continue
                offset += len(chunk)
                if chunk and exited_at is not None:
                    new_attempt(now, "log grew after exit")
                lines, partial = tail.feed(chunk)
                events = on_tick(state, "Running", None, now)
                exit_code = None
                for line in lines:
                    events += on_line(state, name, line, now, dedupe=False)
                    m = EXIT_RE.search(line)
                    if m:
                        exit_code = int(m.group(1))
                recent = (recent + lines)[-20:]
                events += on_partial(state, partial, now)
                if first:  # the history read at attach time: don't replay every old milestone
                    events = [e for e in events if e["kind"] not in ("milestone", "stage")]
                    first = False
                save(events)

                if exit_code is None and not writer and exited_at is None and offset == size:
                    failed = any(parse_error(ln) or "Traceback" in ln for ln in recent)
                    exit_code = 1 if failed else 0
                if exit_code == 0:
                    return "Completed"
                if exit_code is not None and exited_at is None:
                    exited_at = now
                    state["phase"] = "waiting"
                    state["issue_since"] = state["issue_since"] or state["last_sample_at"] or now
                    cause = recent_cause(state, None)
                    save([make_event("failed", state["run"], f"{name} exited ({exit_code}) at "
                                 f"{pct(state['step'], state['total']) or 0}%" + (f" · {cause}" if cause else "")
                                 + f" · waiting {fmt_duration(args.relaunch_wait)} for a relaunch", now)])

        runfile = read_json(run_path) or {}
        if len(runfile.get("jobs", [])) > idx + 1:
            return "superseded"
        if exited_at is not None and runfile.get("abandoned"):
            return "Abandoned"
        gone_since = exited_at or down_since
        if gone_since is not None and now - gone_since > args.relaunch_wait:
            return "FailedFinal"
        time.sleep(STUDIO_INTERVAL)


def drain_saved_logs(entry, args, sink) -> None:
    """A job that finished before a follower attached: read its saved lines once."""
    from lightning_sdk import Job

    try:
        job = Job(entry["name"], teamspace=entry.get("teamspace") or args.teamspace)
        for line in job.logs(follow=True, timestamps=True):
            sink(entry["name"], line)
    except Exception as ex:
        print(f"could not read saved logs: {ex}", file=sys.stderr, flush=True)


def cmd_abandon(args: argparse.Namespace) -> int:
    d = progress_dir()
    path = d / "runs" / f"{args.run}.json"
    runfile = read_json(path)
    if runfile is None:
        print(f"no run named {args.run} in {d}", file=sys.stderr)
        return 1
    runfile["abandoned"] = True
    if args.note:
        runfile["notes"].append({"at": time.time(), "job": None, "note": args.note})
    write_json(path, runfile)
    state = read_json(d / "state" / f"{args.run}.json")
    if state and state["phase"] not in FINAL_PHASES and not pid_alive(runfile.get("pid")):
        state["updated_at"] = time.time()
        append_events(d, [finish(state, "abandoned", time.time())])
        write_json(d / "state" / f"{args.run}.json", state)
    return 0
