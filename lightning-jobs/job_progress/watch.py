"""The background poller: follows a job's logs, or a log file in a Studio, into the state files.

The only module that talks to Lightning, so the only one that must run outside an agent sandbox.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import re
import shlex
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .core import ATTEMPT_FAILED, ENTRY_SCRIPT, FINAL_PHASES, STUDIO_HOME, fmt_duration, make_event, pct
from .settings import statusline_hint
from .store import (
    append_events,
    ensure_dirs,
    install_launcher,
    pid_alive,
    progress_dir,
    read_json,
    replaced_entry,
    session_dir,
    session_id,
    write_json,
)
from .tracker import (
    EXIT_RE,
    LogTail,
    end_attempt,
    finish,
    new_state,
    on_line,
    on_partial,
    on_tick,
    open_attempt,
    parse_error,
    recent_cause,
)

TERMINAL_STATUSES = ("Completed", "Failed", "Stopped")
SUPERVISE_INTERVAL = 5.0
STUDIO_INTERVAL = 10.0
STUDIO_READ_LIMIT = 4_000_000
SEAM_BYTES = 1024
COST_INTERVAL = 30.0
DRAIN_TIMEOUT = 30.0  # how long a finished job's follower gets to read its last lines


def cli_python() -> str | None:
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

    def __init__(self, name: str, teamspace: str | None, query: str | None, sink) -> None:
        super().__init__(daemon=True)
        self.name_, self.teamspace, self.query, self.sink = name, teamspace, query, sink
        self.abandoned = False
        self.error: str | None = None

    def run(self) -> None:
        from lightning_sdk import Job

        try:
            job = Job(self.name_, teamspace=self.teamspace)
            for line in job.logs(follow=True, timestamps=True, query=self.query):
                if self.abandoned:
                    return
                self.sink(self.name_, line, self)
        except Exception as ex:  # a dropped stream is restarted by the supervisor
            self.error = f"{type(ex).__name__}: {ex}"


def teamspace_of(workload: Any) -> tuple[str | None, str | None]:
    """(owner/name, id) of the teamspace a Job or Studio resolved to."""
    ts = getattr(workload, "teamspace", None)
    if ts is None:
        return None, None
    owner = getattr(getattr(ts, "owner", None), "name", None)
    name = getattr(ts, "name", None)
    return (f"{owner}/{name}" if owner and name else name), getattr(ts, "id", None)


def preflight(job: str | None, studio: str | None, teamspace: str | None) -> tuple[str | None, str | None]:
    """Fail fast, with the fix, when the control plane is unreachable (the agent-sandbox case).
    Returns the (owner/name, id) of the teamspace the workload is in, so a run never depends on
    which teamspace happens to be the configured default (`teamspace` None)."""
    from lightning_sdk import Job, Studio

    try:
        # never create a Studio by accident
        found = Studio(studio, teamspace=teamspace, create_ok=False) if studio else Job(job or "", teamspace=teamspace)
        name, ts_id = teamspace_of(found)
        return name or teamspace, ts_id
    except Exception as ex:
        text = f"{type(ex).__name__}: {ex}"
        if re.search(r"NameResolution|resolve|ConnectionError|Max retries|timed out|ProxyError", text, re.I):
            sys.exit(
                f"progress.py watch can't reach the Lightning control plane ({text[:220]}). "
                "Inside an agent sandbox the SDK's requests fail even with lightning.ai allowed, "
                "so run `watch` outside the sandbox. `events` and `statusline` read local files only."
            )
        raise


def same_workload(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if (a.get("kind"), a["name"]) != (b.get("kind"), b["name"]):
        return False
    if a.get("teamspace_id") and b.get("teamspace_id"):
        return a["teamspace_id"] == b["teamspace_id"]
    return a.get("teamspace") == b.get("teamspace")


def taken_by(d: Path, run: str) -> str | None:
    """The other Claude Code session whose run this name already is, if any. A run started outside
    Claude Code, or by this session, is free to continue."""
    me = session_id()
    owner = (read_json(d / "runs" / f"{run}.json") or {}).get("session")
    return owner if me and owner and owner != me else None


def default_run(d: Path, base: str, entry: dict[str, Any]) -> tuple[str, bool]:
    """(run name, start fresh) for a watch without --run: `base` unless another workload holds it.

    The same job name in two teamspaces, or `train.log` in two Studios, are different runs, so a
    name held by another workload gets a qualifier. A finished run of the same workload starts
    over; only an explicit --run continues one.
    """
    ident = f"{entry.get('kind')}|{entry['name']}|{entry.get('teamspace')}"
    qualifier = entry.get("studio") or (entry.get("teamspace") or "default").replace("/", "-")
    digest = hashlib.sha1(ident.encode()).hexdigest()[:8]
    for run in (base, f"{base}@{qualifier}", f"{base}@{digest}"):
        runfile = read_json(d / "runs" / f"{run}.json")
        if not runfile or not runfile.get("jobs"):
            return run, True
        if same_workload(runfile["jobs"][0], entry):
            state = read_json(d / "state" / f"{run}.json") or {}
            return run, state.get("phase") in FINAL_PHASES and not pid_alive(runfile.get("pid"))
    return f"{base}@{digest}", True


def cmd_watch(args: argparse.Namespace) -> int:
    if bool(args.studio) != bool(args.log) or bool(args.job) == bool(args.studio):
        sys.exit("watch takes either a JOB name, or --studio NAME together with --log PATH")
    ensure_sdk()
    teamspace, teamspace_id = preflight(args.job, args.studio, args.teamspace)
    d = progress_dir()
    ensure_dirs(d)
    replaced = replaced_entry(d)
    install_launcher(d)  # keeps a registered status line on this, the newest, copy of the script
    if args.studio:
        name, base = f"{args.studio}:{args.log}", Path(args.log).stem
        entry = {"kind": "studio", "name": name, "studio": args.studio, "log": args.log}
    else:
        name, base, entry = args.job, args.job, {"kind": "job", "name": args.job}
    # the resolved teamspace, not the flag: the configured default can change while this runs
    entry.update(teamspace=teamspace, teamspace_id=teamspace_id, added_at=time.time())
    run, fresh = (args.run, False) if args.run else default_run(d, base, entry)
    owner = taken_by(d, run)
    if args.run and owner:
        # run names are shared by every session on the machine: continuing another session's run
        # merges two unrelated workloads into one bar, history and setbacks
        sys.exit(
            f"run {run} belongs to another Claude Code session ({owner}); "
            "pick a new --run name, or leave --run out to get one"
        )
    run_path, state_path = d / "runs" / f"{run}.json", d / "state" / f"{run}.json"
    runfile = (None if fresh else read_json(run_path)) or {
        "run": run,
        "jobs": [],
        "notes": [],
        "abandoned": False,
        "pid": None,
    }
    if not any(same_workload(j, entry) for j in runfile["jobs"]):
        runfile["jobs"].append(entry)
    if args.note:
        runfile["notes"].append({"at": time.time(), "job": name, "note": args.note})
    runfile["abandoned"] = False
    runfile["session"] = session_id() or runfile.get("session")

    if pid_alive(runfile.get("pid")) and runfile.get("pid") != os.getpid():
        write_json(run_path, runfile)
        append_events(
            d,
            [
                make_event(
                    "relaunch", run, f"continuing with {name}" + (f" · {args.note}" if args.note else ""), time.time()
                )
            ],
        )
        print(f"handed {name} to the running poller for {run} (pid {runfile['pid']})")
        return 0
    runfile["pid"] = os.getpid()
    write_json(run_path, runfile)
    state = (None if fresh else read_json(state_path)) or new_state(run)
    if state["phase"] in FINAL_PHASES or state["phase"] == "waiting":
        state["phase"] = "pending"
        state["finished_at"] = None
    state["updated_at"] = time.time()
    write_json(state_path, state)
    # marks where this poller's part of the run starts, for a Monitor that starts after it
    append_events(d, [make_event("watching", run, f"watching {name}", time.time())])
    print(f"run {run}: watching {name}", flush=True)
    hint = statusline_hint(run, session_dir())
    if hint:
        append_events(d, [make_event("hint", run, hint, time.time())])
        print(f"{run}: {hint}", flush=True)
    if replaced:
        msg = (
            f"the status line now runs {ENTRY_SCRIPT} instead of {replaced}. If that one is the copy "
            "you meant to use, restart this watcher from it"
        )
        append_events(d, [make_event("hint", run, msg, time.time())])
        print(f"{run}: {msg}", flush=True)

    lock = threading.Lock()

    def save(events: list[dict[str, Any]]) -> None:
        state["updated_at"] = time.time()
        write_json(state_path, state)
        append_events(d, events)
        for e in events:
            print(e["msg"], flush=True)

    sink = make_sink(state, lock, save)
    idx = next(i for i, j in enumerate(runfile["jobs"]) if same_workload(j, entry))
    while True:
        runfile = read_json(run_path) or runfile
        entry = runfile["jobs"][idx]
        with lock:
            if state["job"] and state["job"] != entry["name"]:
                state["issue_since"] = state["issue_since"] or state["last_sample_at"]
                state["job_running_since"] = None
                end_attempt(state, time.time())
                # a new workload is the next attempt, and its status and attempt numbers start over
                open_attempt(state)
                state.update(status=None, platform_attempt=None, closed_attempt=None)
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
            save(
                [
                    make_event(
                        ATTEMPT_FAILED,
                        run,
                        f"{entry['name']} failed at {pct(state['step'], state['total']) or 0}%"
                        + (f" · {cause}" if cause else "")
                        + f" · waiting {fmt_duration(args.relaunch_wait)} for a relaunch",
                        time.time(),
                    )
                ]
            )
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


def make_sink(state: dict[str, Any], lock: threading.Lock, save):
    """The callback log lines reach the state through. It drops lines from a follower that was let
    go of, so a stream that ends late can't write into the attempt that came after it."""

    def sink(job_name: str, line: str, follower: Follower | None = None) -> None:
        with lock:
            if follower is not None and follower.abandoned:
                return
            events = on_line(state, job_name, line, time.time())
            if events or time.time() - (state["updated_at"] or 0) > 1:
                save(events)

    return sink


def supervise(entry, state, lock, save, sink, run_path, idx, args) -> str:
    """Poll one job's status every few seconds and keep a log follower attached while it runs."""
    from lightning_sdk import Job

    job = Job(entry["name"], teamspace=entry.get("teamspace") or args.teamspace)
    follower: Follower | None = None
    last_cost, terminal_since, failures = 0.0, None, 0
    try:
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
                return "superseded"

            if status == "Running" and (follower is None or not follower.is_alive()):
                if follower and follower.error:
                    print(f"log stream dropped, reattaching: {follower.error}", file=sys.stderr, flush=True)
                follower = Follower(entry["name"], entry.get("teamspace") or args.teamspace, args.query, sink)
                follower.start()

            if status in TERMINAL_STATUSES:
                terminal_since = terminal_since or now
                # let the follower drain the last lines (its stream ends once the job is finished)
                if follower is None or not follower.is_alive() or now - terminal_since > DRAIN_TIMEOUT:
                    if follower is None:
                        drain_saved_logs(entry, args, sink)
                    return status
            time.sleep(SUPERVISE_INTERVAL)
    finally:
        # a follower that outlives this attempt, e.g. past the drain timeout, must not write into the next one
        if follower is not None:
            with lock:
                follower.abandoned = True


def studio_read_command(path: str, offset: int, seam: int = 0) -> str:
    """Report the log's size, inode, whether any process still has it open, the `seam` bytes that
    end at `offset`, and the bytes after `offset`, both as base64.

    The seam and inode tell a log that was replaced from one that only grew: a relaunch that
    overwrites the log can outgrow the old offset between two reads. `Studio.run_with_exit_code`
    strips its output, so raw log bytes would lose their trailing newline; base64 keeps them exact.
    The open-file check walks /proc, so it needs no extra tools.
    """
    if path.startswith("~/"):  # the Studio's home, not the local one
        path = STUDIO_HOME + path[1:]
    q = shlex.quote(path)
    seam = min(seam, offset)
    return (
        f"cd {STUDIO_HOME} 2>/dev/null; f={q}; "
        'if [ ! -f "$f" ]; then echo NOFILE; exit 0; fi; '
        'a=$(readlink -f "$f"); w=0; '
        'for p in /proc/[0-9]*/fd/*; do [ "$(readlink "$p" 2>/dev/null)" = "$a" ] && { w=1; break; }; done; '
        's=$(wc -c < "$f" | tr -d " "); i=$(ls -Ldi "$f" | awk \'{print $1}\'); '
        'echo "SIZE $s WRITER $w INODE $i"; '
        "printf 'SEAM '; "
        f'if [ "$s" -ge {offset} ]; then tail -c +{offset - seam + 1} "$f" | head -c {seam} '
        '| base64 | tr -d "\\n"; fi; '
        "printf '\\nDATA '; "
        f'if [ "$s" -gt {offset} ]; then tail -c +{offset + 1} "$f" | head -c {STUDIO_READ_LIMIT} '
        '| base64 | tr -d "\\n"; fi'
    )


def parse_studio_read(out: str) -> tuple[int, bool, str, bytes, bytes] | None:
    """(size, writer, inode, seam, new bytes) from the read command's output; None if no log yet."""
    m = re.search(r"^SIZE (\d+) WRITER ([01]) INODE (\S*)$", out, re.M)
    if not m:
        return None
    seam = re.search(r"^SEAM ?(\S*)$", out, re.M)
    data = re.search(r"^DATA ?(\S*)$", out, re.M)
    return (
        int(m.group(1)),
        m.group(2) == "1",
        m.group(3),
        base64.b64decode(seam.group(1)) if seam else b"",
        base64.b64decode(data.group(1)) if data else b"",
    )


def supervise_studio(entry, state, lock, save, run_path, idx, args) -> str:
    """Tail a log file inside a Studio. A relaunch into the same file is the run's next attempt."""
    from lightning_sdk import Studio

    studio = Studio(entry["studio"], teamspace=entry.get("teamspace") or args.teamspace, create_ok=False)
    tail, offset, first, failures = LogTail(), 0, True, 0
    seam, inode = b"", None  # the last bytes read, and the log's inode: both change when it is replaced
    recent: list[str] = []  # last lines, to judge an exit that printed no PROGRESS_EXIT
    exit_code: int | None = None  # the attempt's exit, until a relaunch writes after it
    exited_at: float | None = None  # when a failed exit was reported; cleared by a relaunch
    down_since: float | None = None
    name = entry["name"]

    def new_attempt(now: float, why: str) -> dict[str, Any]:
        nonlocal exit_code, exited_at, recent
        exit_code = exited_at = None
        recent = []
        state["issue_since"] = state["issue_since"] or state["last_sample_at"] or now
        state["job_running_since"] = None
        state["phase"] = "pending"
        end_attempt(state, now)
        return make_event("relaunch", state["run"], f"{why}: new attempt", now)

    def relaunched_after_exit(text: str, now: float) -> list[dict[str, Any]]:
        """Output after an exit is a relaunch appended to the log, in the same read or a later one."""
        if exit_code is None or not text.strip():
            return []
        event = new_attempt(now, f"log grew after exit ({exit_code})")
        open_attempt(state)
        return [event]

    while True:
        now = time.time()
        try:
            status = str(studio.status)
            read = None
            if status == "Running":
                out, _ = studio.run_with_exit_code(studio_read_command(entry["log"], offset, len(seam)))
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
                size, writer, ino, their_seam, chunk = read
                # truncated, recreated or overwritten: the agent relaunched into the same log
                replaced = size < offset or their_seam != seam or (inode is not None and ino != inode)
                if replaced and offset > 0:
                    tail, offset, seam, inode = LogTail(), 0, b"", None
                    save([new_attempt(now, "log restarted")])
                    continue
                inode = ino
                offset += len(chunk)
                seam = (seam + chunk)[-SEAM_BYTES:]
                if chunk:  # the log is read directly, so new bytes are the running attempt's
                    open_attempt(state)
                lines, partial = tail.feed(chunk)
                events = on_tick(state, "Running", None, now)
                for line in lines:  # in order: an exit only counts if nothing follows it
                    events += relaunched_after_exit(line, now)
                    events += on_line(state, name, line, now, dedupe=False)
                    m = EXIT_RE.search(line)
                    if m:
                        exit_code = int(m.group(1))
                    recent = [*recent, line][-20:]
                events += relaunched_after_exit(partial, now)
                events += on_partial(state, partial, now)
                if first:  # the history read at attach time: don't replay every old milestone
                    events = [e for e in events if e["kind"] not in ("milestone", "stage")]
                    first = False
                save(events)

                if exit_code is None and not writer and offset == size:
                    failed = any(parse_error(ln) or "Traceback" in ln for ln in recent)
                    exit_code = 1 if failed else 0
                if exit_code == 0:
                    return "Completed"
                if exit_code is not None and exited_at is None:
                    exited_at = now
                    state["phase"] = "waiting"
                    state["issue_since"] = state["issue_since"] or state["last_sample_at"] or now
                    cause = recent_cause(state, None)
                    save(
                        [
                            make_event(
                                ATTEMPT_FAILED,
                                state["run"],
                                f"{name} exited ({exit_code}) at "
                                f"{pct(state['step'], state['total']) or 0}%"
                                + (f" · {cause}" if cause else "")
                                + f" · waiting {fmt_duration(args.relaunch_wait)} for a relaunch",
                                now,
                            )
                        ]
                    )

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
