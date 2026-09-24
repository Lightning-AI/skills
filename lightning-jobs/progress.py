#!/usr/bin/env python3
"""Live progress, ETA and setback tracking for Lightning AI jobs.

    progress.py watch JOB [--run RUN] [--teamspace OWNER/TS] [--note TEXT]
    progress.py watch --studio STUDIO --log PATH [--run RUN] [--teamspace OWNER/TS]
    progress.py abandon RUN [--note TEXT]
    progress.py events [--run RUN] [--from-start]
    progress.py statusline [--config]

`watch` is the background poller. It follows the job's logs, reads `PROGRESS <step>/<total>`
lines (tqdm bars as a fallback) and `PROGRESS_PHASE <name>` stage markers, and tracks a *run*: a chain of attempts that survives a
failed job being relaunched under a new name. Running `watch NEW_JOB --run RUN` while a
poller for RUN is alive hands the new job to that poller instead of starting a second one.

`events` prints one line per notable event (milestone, stall, setback, final state) and is
meant as a Claude Code Monitor command. `statusline` draws one bar per run for the Claude
Code status line and makes no network calls.

State lives in $LIGHTNING_PROGRESS_DIR, default ~/.local/state/lightning-progress: one place
per user, so the poller, the Monitor and the status line agree whatever directory each runs in.
Only `watch` needs lightning_sdk; everything else is standard library.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shlex
import shutil
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

BAR_WIDTH = 20
FINAL_PHASES = ("done", "failed", "stopped", "abandoned")
TERMINAL_STATUSES = ("Completed", "Failed", "Stopped")
SUPERVISE_INTERVAL = 5.0
STUDIO_INTERVAL = 10.0
STUDIO_READ_LIMIT = 4_000_000
STUDIO_HOME = "/teamspace/studios/this_studio"
COST_INTERVAL = 30.0
STATE_STALE_AFTER = 60.0
FINAL_VISIBLE_FOR = 600.0
RECOVERY_SAMPLES = 3
RATE_WINDOW = 10

PROGRESS_RE = re.compile(r"\bPROGRESS\s+(\d+)\s*/\s*(\d+)(?:.*?\battempt=(\d+))?")
TQDM_RE = re.compile(r"(\d{1,3})%\|[^|]*\|\s*(\d+)/(\d+)")
EPOCH_RE = re.compile(r"\bEpoch\s+(\d+)")
TQDM_SKIP_RE = re.compile(r"Validat|Sanity|Testing|Predict", re.I)
EXIT_RE = re.compile(r"\bPROGRESS_EXIT\s+(-?\d+)")
STAGE_RE = re.compile(r"\bPROGRESS_PHASE\s+(\S+)(?:\s+(\d+)\s*/\s*(\d+))?")
# warnings that mention an error word, e.g. PyTorch's `[W924 14:51:32 CUDACachingAllocator.cpp] ... OOM`
WARN_RE = re.compile(r"^\s*\[?[WI]\d{3,4}\s|\b\w*Warning\b|^\s*\[?(WARNING|WARN|INFO)\b", re.I)
PROGRESS_KEYS = ("step", "total", "epoch", "source", "peak", "peak_epoch", "milestone")
ERROR_RE = re.compile(
    r"\w+(Error|Exception)\b|out of memory|\bOOM\b|\bKilled\b|Segmentation fault|NCCL.*(error|timeout)",
    re.I,
)


# ---------------------------------------------------------------------------------- formatting


def fmt_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "…"
    s = max(0, int(round(seconds)))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


def pct(step: Optional[int], total: Optional[int]) -> Optional[int]:
    if step is None or not total:
        return None
    return max(0, min(100, int(100 * step / total)))


def bar(step: int, peak: int, total: int, width: int = BAR_WIDTH) -> str:
    """`▓` done now, `▒` ground lost to a setback (current → peak), `░` still to do."""
    cur = max(0, min(width, round(width * step / total)))
    top = max(cur, min(width, round(width * peak / total)))
    return "▓" * cur + "▒" * (top - cur) + "░" * (width - top)


# ------------------------------------------------------------------------------------- parsing


def split_timestamp(text: str) -> Tuple[Optional[float], str]:
    """Strip the ISO-8601 prefix `job.logs(timestamps=True)` adds; return (epoch seconds, rest)."""
    head, _, rest = text.partition(" ")
    if head[:1].isdigit() and "T" in head:
        try:
            return datetime.fromisoformat(head.replace("Z", "+00:00")).timestamp(), rest
        except ValueError:
            pass
    return None, text


def parse_progress(message: str) -> Optional[Dict[str, Any]]:
    """Return {step, total, attempt, epoch, source} from the last progress reading in a line.

    tqdm redraws with carriage returns, so one log line can hold many readings: the last wins.
    """
    for seg in reversed(message.split("\r")):
        m = PROGRESS_RE.search(seg)
        if m and int(m.group(2)) > 0:
            return {
                "step": int(m.group(1)),
                "total": int(m.group(2)),
                "attempt": int(m.group(3)) if m.group(3) else None,
                "epoch": None,
                "source": "progress",
            }
        m = TQDM_RE.search(seg)
        if m and int(m.group(3)) > 0 and not TQDM_SKIP_RE.search(seg[: m.start()]):
            e = EPOCH_RE.search(seg[: m.start()])
            return {
                "step": int(m.group(2)),
                "total": int(m.group(3)),
                "attempt": None,
                "epoch": int(e.group(1)) if e else None,
                "source": "tqdm",
            }
    return None


def parse_error(message: str) -> Optional[str]:
    if "Traceback (most recent call last)" in message:
        return None  # the line after it names the actual error
    if WARN_RE.search(message):
        return None
    m = ERROR_RE.search(message)
    return message.strip()[:120] if m else None


# ------------------------------------------------------------------------------------- tracker


def new_state(run: str) -> Dict[str, Any]:
    return {
        "run": run,
        "job": None,
        "status": None,
        "phase": "pending",
        "source": None,
        "step": None,
        "total": None,
        "epoch": None,
        "peak": None,
        "peak_epoch": None,
        "attempt": None,
        "platform_attempt": None,
        "rate": None,
        "eta_s": None,
        "samples": [],
        "gaps": [],
        "since_reset": 0,
        "last_sample_at": None,
        "issue_since": None,
        "pending_since": None,
        "pending_note": None,
        "stage": None,
        "stage_since": None,
        "stage_index": None,
        "stage_count": None,
        "stages": [],
        "stage_snap": {},
        "job_running_since": None,
        "started_at": None,
        "finished_at": None,
        "setbacks": [],
        "lost_s": 0.0,
        "milestone": 0,
        "last_error": None,
        "last_error_at": None,
        "no_progress_warned": False,
        "cost": None,
        "last_ts": {},
        "updated_at": None,
    }


def _event(kind: str, run: str, msg: str, at: float) -> Dict[str, Any]:
    return {"ts": at, "run": run, "kind": kind, "msg": f"{run}: {msg}"}


def _progress_text(s: Dict[str, Any]) -> str:
    p = pct(s["step"], s["total"])
    parts = [f"{p}%" if p is not None else "starting"]
    if s.get("stage"):
        parts.insert(0, s["stage"])
    if s["eta_s"] is not None:
        parts.append(f"ETA {fmt_duration(s['eta_s'])}")
    if s["setbacks"]:
        parts.append(f"↺{len(s['setbacks'])}")
    return " · ".join(parts)


def _recent_cause(s: Dict[str, Any], since: Optional[float]) -> Optional[str]:
    if s["last_error"] and s["last_error_at"] is not None:
        if since is None or s["last_error_at"] >= since - 60:
            return s["last_error"]
    return None


def on_line(s: Dict[str, Any], job: str, text: str, wall_now: float, dedupe: bool = True) -> List[Dict[str, Any]]:
    """Feed one log line. With `dedupe`, replayed history (a follow reconnect) is dropped by timestamp."""
    ts, message = split_timestamp(text) if dedupe else (None, text)
    if ts is not None:
        last = s["last_ts"].get(job)
        if last is not None and ts <= last:
            return []
        s["last_ts"][job] = ts
    at = ts if ts is not None else wall_now
    err = parse_error(message)
    if err:
        s["last_error"], s["last_error_at"] = err, at
    m = STAGE_RE.search(message)
    if m:
        return on_stage(s, m.group(1), at, *(int(g) if g else None for g in m.group(2, 3)))
    reading = parse_progress(message)
    if reading is None:
        return []
    if s["source"] == "progress" and reading["source"] == "tqdm":
        return []  # an explicit PROGRESS line outranks any tqdm bar in the same log
    return on_sample(s, reading, at)


def stage_summary(s: Dict[str, Any], until: float) -> str:
    parts = []
    for i, st in enumerate(s.get("stages") or []):
        end = st["end"] if st["end"] is not None else until
        parts.append(f"{st['name']} {fmt_duration(end - st['start'])}")
    return ", ".join(parts)


def close_stage(s: Dict[str, Any], at: float) -> None:
    """End the current stage without starting another, e.g. when an attempt ends."""
    if s["stage"] is not None:
        s["stage_snap"][s["stage"]] = {k: s[k] for k in PROGRESS_KEYS}
        s["stage"] = None
    if s["stages"] and s["stages"][-1]["end"] is None:
        s["stages"][-1]["end"] = at


def on_stage(s: Dict[str, Any], name: str, at: float,
             index: Optional[int] = None, count: Optional[int] = None) -> List[Dict[str, Any]]:
    """Switch to a named stage. Each stage keeps its own bar; re-entering one resumes its bar,
    so a relaunched attempt that trains again is compared with the old training progress.
    `PROGRESS_PHASE eval 3/4` also gives the stage's place in the job, for a whole-job bar."""
    cur = s["stage"]
    if count:
        s["stage_index"], s["stage_count"] = index, count
    if name == cur:
        return []
    if cur is not None:
        s["stage_snap"][cur] = {k: s[k] for k in PROGRESS_KEYS}
    if s["stages"] and s["stages"][-1]["end"] is None:
        s["stages"][-1]["end"] = at
    snap = s["stage_snap"].get(name)
    s.update({k: None for k in PROGRESS_KEYS})
    s["milestone"] = 0
    if snap:
        s.update(snap)
    s.update(samples=[], since_reset=0, rate=None, eta_s=None, stage=name, stage_since=at)
    prev = s["stages"][-1] if s["stages"] else None
    s["stages"].append({"name": name, "start": at, "end": None})
    msg = f"stage {name}"
    if snap is not None:
        msg += " again"
    if prev is not None:
        msg += f" ({prev['name']} took {fmt_duration(prev['end'] - prev['start'])})"
    return [_event("stage", s["run"], msg, at)]


def on_partial(s: Dict[str, Any], text: str, now: float) -> List[Dict[str, Any]]:
    """Read progress from a log's unfinished last line (tqdm redraws with \\r and no newline)."""
    reading = parse_progress(text)
    if reading is None or (s["source"] == "progress" and reading["source"] == "tqdm"):
        return []
    if (reading["step"], reading["total"], reading["epoch"]) == (s["step"], s["total"], s["epoch"]):
        return []
    return on_sample(s, reading, now)


class LogTail:
    """Turns successive byte chunks of a growing log into complete lines plus the unfinished last one."""

    def __init__(self) -> None:
        self.buf = b""

    def feed(self, chunk: bytes) -> Tuple[List[str], str]:
        *lines, self.buf = (self.buf + chunk).split(b"\n")
        if len(self.buf) > 65536:  # a tqdm bar that never ends its line: keep the latest redraws
            self.buf = self.buf[-65536:]
        return [ln.decode("utf-8", "replace") for ln in lines], self.buf.decode("utf-8", "replace")


def _classify(s: Dict[str, Any], r: Dict[str, Any]) -> Optional[str]:
    if s["step"] is None:
        return None
    if r["source"] != s["source"]:
        return None
    if r["total"] != s["total"] and r["epoch"] == s["epoch"]:
        # a script re-estimating its total as it goes is not a new setup; a drop in step is
        return "new-setup" if r["step"] < s["step"] else None
    prev_key = (s["epoch"] or 0, s["step"])
    key = (r["epoch"] or 0, r["step"])
    if key < prev_key:
        restart = (r["epoch"] or 0) == 0 and r["step"] <= max(1, r["total"] // 100)
        return "restart" if restart else "resume"
    if r["attempt"] is not None and s["attempt"] is not None and r["attempt"] > s["attempt"]:
        return "resume"
    return None


def on_sample(s: Dict[str, Any], r: Dict[str, Any], at: float) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    run = s["run"]
    if s["source"] == "tqdm" and r["source"] == "progress":
        s.update(step=None, total=None, peak=None, epoch=None, samples=[], since_reset=0)
    kind = _classify(s, r)

    if kind:
        issue_start = s["issue_since"] or s["last_sample_at"] or at
        lost = max(0.0, at - issue_start)
        cause = _recent_cause(s, s["issue_since"])
        s["setbacks"].append(
            {"at": at, "kind": kind, "from": s["step"], "to": r["step"], "peak": s["peak"],
             "cause": cause, "lost_s": lost, "job": s["job"]}
        )
        s["lost_s"] += lost
        s["samples"], s["since_reset"], s["eta_s"], s["rate"] = [], 0, None, None
        if kind == "new-setup":
            s["peak"], s["peak_epoch"] = r["step"], r["epoch"]
        s["phase"] = "recovering"
        s["milestone"] = (pct(r["step"], r["total"]) or 0) // 10
        what = {
            "resume": f"setback: resumed at {pct(r['step'], r['total'])}% (peak {pct(s['peak'], r['total'])}%)",
            "restart": "setback: restarted from scratch",
            "new-setup": f"restarted with a new setup ({r['total']} steps)",
        }[kind]
        events.append(_event("setback", run, f"{what} · {fmt_duration(lost)} lost"
                             + (f" · cause: {cause}" if cause else ""), at))
    elif s["phase"] == "stalled" and s["issue_since"] is not None:
        stalled = max(0.0, at - s["issue_since"])
        s["lost_s"] += stalled
        events.append(_event("recovered", run, f"progress again after {fmt_duration(stalled)} stalled", at))

    if s["last_sample_at"] is not None and not kind and r["step"] != s["step"]:
        s["gaps"] = (s["gaps"] + [at - s["last_sample_at"]])[-20:]
    if r["epoch"] != s["epoch"]:
        s["samples"] = []  # a new epoch restarts a per-epoch bar; that is not a setback
    s.update(step=r["step"], total=r["total"], epoch=r["epoch"], source=r["source"],
             last_sample_at=at, issue_since=None)
    if r["attempt"] is not None:
        s["attempt"] = r["attempt"]
    if s["peak"] is None or (r["epoch"] or 0, r["step"]) > (s["peak_epoch"] or 0, s["peak"]):
        s["peak"], s["peak_epoch"] = r["step"], r["epoch"]
    s["samples"] = (s["samples"] + [[at, r["step"]]])[-RATE_WINDOW:]
    s["since_reset"] += 1

    if len(s["samples"]) >= RECOVERY_SAMPLES:
        (t0, s0), (t1, s1) = s["samples"][0], s["samples"][-1]
        if t1 > t0 and s1 > s0:
            s["rate"] = (s1 - s0) / (t1 - t0)
            s["eta_s"] = (r["total"] - r["step"]) / s["rate"]
    if s["phase"] in ("pending", "stalled", "running", "starting") or (
        s["phase"] == "recovering" and s["since_reset"] >= RECOVERY_SAMPLES
    ):
        s["phase"] = "running"

    decile = (pct(r["step"], r["total"]) or 0) // 10
    if r["epoch"] is None and 0 < decile < 10 and decile > s["milestone"]:
        s["milestone"] = decile
        events.append(_event("milestone", run, _progress_text(s), at))
    return events


def stall_threshold(s: Dict[str, Any]) -> float:
    return max(120.0, 4 * median(s["gaps"])) if s["gaps"] else 300.0


def on_tick(s: Dict[str, Any], status: str, platform_attempt: Optional[int], now: float) -> List[Dict[str, Any]]:
    """Periodic check from the supervisor: status changes, platform retries and stalls."""
    events: List[Dict[str, Any]] = []
    run, prev = s["run"], s["status"]
    s["status"] = status

    if platform_attempt and s["platform_attempt"] and platform_attempt > s["platform_attempt"]:
        s["issue_since"] = s["issue_since"] or s["last_sample_at"] or now
        events.append(_event("retry", run, f"platform retry: attempt {platform_attempt}", now))
    if platform_attempt:
        s["platform_attempt"] = platform_attempt

    if status == "Pending":
        if prev == "Running":
            s["issue_since"] = s["issue_since"] or s["last_sample_at"] or now
            events.append(_event("requeued", run, "back to Pending (requeued or retried)", now))
        s["pending_since"] = s["pending_since"] or now
        if s["phase"] not in ("waiting",):
            s["phase"] = "pending"
    elif status == "Running":
        s["pending_since"] = None
        s["started_at"] = s["started_at"] or now
        if prev != "Running" or not s["job_running_since"]:
            s["job_running_since"] = now
        if s["phase"] == "pending":
            s["phase"] = "starting" if s["step"] is None else "recovering"
            if s["step"] is None:
                events.append(_event("started", run, "running", now))
        if s["phase"] == "starting" and s["started_at"] and not s["no_progress_warned"] \
                and now - s["started_at"] > 600:
            s["no_progress_warned"] = True
            events.append(_event("no-progress", run,
                                 "running 10m with no PROGRESS or tqdm line yet", now))
        bar_open = s["step"] is not None and s["total"] and s["step"] < s["total"]
        if s["phase"] in ("running", "recovering") and s["last_sample_at"] is not None and bar_open:
            # after a relaunch or requeue, give the new process time to start before calling it stalled
            restarting = s["last_sample_at"] < s["job_running_since"]
            quiet = now - max(s["last_sample_at"], s["job_running_since"], s.get("stage_since") or 0)
            if quiet > (600.0 if restarting else stall_threshold(s)):
                s["phase"] = "stalled"
                s["issue_since"] = s["issue_since"] or s["last_sample_at"]
                cause = _recent_cause(s, s["last_sample_at"])
                events.append(_event("stall", run, f"stalled {fmt_duration(quiet)} at "
                                     f"{pct(s['step'], s['total'])}%" + (f" · {cause}" if cause else ""), now))
    return events


def finish(s: Dict[str, Any], phase: str, now: float) -> Dict[str, Any]:
    s["phase"], s["finished_at"], s["eta_s"] = phase, now, None
    run, p = s["run"], pct(s["step"], s["total"])
    start = s["started_at"] or now
    if s.get("stages") and s["stages"][-1]["end"] is None:
        s["stages"][-1]["end"] = now
    if phase == "done":
        msg = f"done in {fmt_duration(now - start)}"
        if s.get("stages"):
            msg += f" ({stage_summary(s, now)})"
        if s["setbacks"]:
            msg += f" · {len(s['setbacks'])} setback(s) cost {fmt_duration(s['lost_s'])}"
    else:
        cause = _recent_cause(s, None)
        msg = f"{phase} at {p if p is not None else 0}%" + (f" · {cause}" if cause and phase != "stopped" else "")
    if s["cost"] is not None:
        msg += f" · ${s['cost']:.2f}"
    return _event(phase, run, msg, now)


# ---------------------------------------------------------------------------------- statusline


def stage_track(s: Dict[str, Any], now: float, keep: int = 3) -> str:
    """`setup ✔ 1m05s · train ✔ 9m40s · ▸ eval_ft 4m32s`: the last few stages and the current one."""
    stages = s.get("stages") or []
    parts = []
    for st in stages[-(keep + 1):]:
        if st["end"] is None:
            parts.append(f"▸ {st['name']} {fmt_duration(now - st['start'])}")
        else:
            parts.append(f"{st['name']} ✔ {fmt_duration(st['end'] - st['start'])}")
    if len(stages) > keep + 1:
        parts.insert(0, "…")
    return " · ".join(parts)


def render_line(s: Dict[str, Any], now: float) -> str:
    phase, run = s["phase"], s["run"]
    step, total = s["step"], s["total"]
    peak = s["peak"] if s.get("peak_epoch") == s["epoch"] else step
    icon = {"pending": "⏳", "starting": "▶", "running": "▶", "recovering": "⟳", "stalled": "⚠",
            "waiting": "✖", "done": "✔", "failed": "✖", "stopped": "■", "abandoned": "✖"}.get(phase, "?")
    stage = s.get("stage")
    live = phase not in FINAL_PHASES
    stage_only = bool(stage) and not total and phase in ("starting", "running", "recovering")
    head = f"{icon} {run}" + (f" [{stage}]" if stage and live and not stage_only else "")
    if stage_only and s.get("stage_count"):
        done = max(0, min((s.get("stage_index") or 1) - 1, s["stage_count"]))
        head += f"  {bar(done, done, s['stage_count'])}  stage {s.get('stage_index')}/{s['stage_count']}"
    if total:
        head += f"  {bar(step or 0, peak or step or 0, total)}  {pct(step, total):3d}%"
        if s["epoch"] is not None:
            head += f" ep{s['epoch']}"
        if s["setbacks"]:
            head += f" ↺{len(s['setbacks'])}"
    since = lambda t: fmt_duration(now - t) if t else "…"  # noqa: E731
    cause = s["last_error"]
    if phase == "pending":
        tail = f"{s.get('pending_note') or 'waiting for machine'} · {since(s['pending_since'])}"
    elif stage_only:
        tail = stage_track(s, now)
    elif phase == "starting" or (phase in ("running", "recovering") and not total):
        tail = f"{stage or 'starting'} · {since(s.get('stage_since') or s['started_at'])}"
    elif phase in ("running", "recovering") and step is not None and step >= total:
        tail = f"{stage + ' ' if stage else ''}finished · {since(s['last_sample_at'])} ago"
    elif phase in ("running", "recovering"):
        tail = f"ETA {fmt_duration(s['eta_s'])}"
        if s["lost_s"] >= 1:
            tail += f" (+{fmt_duration(s['lost_s'])})"
        if phase == "recovering" and peak and step is not None and peak > step:
            tail += f" · peak {pct(peak, total)}%"
    elif phase == "stalled":
        tail = f"stalled {since(s['last_sample_at'])}" + (f" · {cause[:50]}" if cause else "")
    elif phase == "waiting":
        tail = f"failed · waiting for relaunch {since(s['issue_since'])}" + (f" · {cause[:40]}" if cause else "")
    elif phase == "done":
        tail = f"done in {fmt_duration((s['finished_at'] or now) - (s['started_at'] or now))}"
        if s["setbacks"]:
            tail += f" · setbacks cost {fmt_duration(s['lost_s'])}"
    else:
        tail = phase
    if s["cost"] is not None and phase not in ("pending",):
        tail += f" · ${s['cost']:.2f}"
    line = f"{head}  {tail}"
    if phase not in FINAL_PHASES and s["updated_at"] and now - s["updated_at"] > STATE_STALE_AFTER:
        line = f"\033[2m{line} · stale (poller not running?)\033[0m"
    return line


# ------------------------------------------------------------------------------------- storage


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


# --------------------------------------------------------------------------------------- watch


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
        os.execv(py, [py, os.path.abspath(__file__), *sys.argv[1:]])
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
    ours = f"python3 {shlex.quote(os.path.abspath(__file__))} statusline"
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
        append_events(d, [_event("relaunch", run, f"continuing with {name}"
                                 + (f" · {args.note}" if args.note else ""), time.time())])
        print(f"handed {name} to the running poller for {run} (pid {runfile['pid']})")
        return 0
    runfile["pid"] = os.getpid()
    write_json(run_path, runfile)
    hint = statusline_hint(run, session_dir())
    if hint:
        append_events(d, [_event("hint", run, hint, time.time())])
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
                close_stage(state, time.time())
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
            cause = _recent_cause(state, None)
            save([_event("failed", run, f"{entry['name']} failed at {pct(state['step'], state['total']) or 0}%"
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
        close_stage(state, now)
        save([_event("relaunch", state["run"], f"{why}: new attempt", now)])

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
                    cause = _recent_cause(state, None)
                    save([_event("failed", state["run"], f"{name} exited ({exit_code}) at "
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


# --------------------------------------------------------------------------- abandon / events


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


def cmd_events(args: argparse.Namespace) -> int:
    d = progress_dir()
    ensure_dirs(d)
    path = d / "events.jsonl"
    path.touch()
    with open(path) as f:
        if not args.from_start:
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
            if args.run and e.get("run") != args.run:
                continue
            print(e["msg"], flush=True)
            if args.run and e.get("kind") in FINAL_PHASES:
                return 0


# ---------------------------------------------------------------------------------- statusline


def cmd_statusline(args: argparse.Namespace) -> int:
    if args.config:
        project_dir = session_dir()
        path, cmd = statusline_setting(project_dir)
        print(json.dumps(statusline_snippet(cmd), indent=2))
        print(f"# merge into {os.path.join(project_dir, SETTINGS_FILES[0])}"
              + (f"; replaces the status line set in {path}, and still runs it" if cmd and "progress.py" not in cmd
                 else "; already set up" if cmd else ""), file=sys.stderr)
        return 0
    if not sys.stdin.isatty():
        sys.stdin.read()  # Claude Code sends session JSON; the bars don't depend on it
    d = progress_dir()
    now = time.time()
    states = [s for s in (read_json(p) for p in sorted((d / "state").glob("*.json"))) if s]
    visible = [s for s in states
               if s["phase"] not in FINAL_PHASES or now - (s.get("finished_at") or 0) < FINAL_VISIBLE_FOR]
    visible.sort(key=lambda s: (s["phase"] in FINAL_PHASES, -(s.get("updated_at") or 0)))
    for s in visible[:5]:
        print(render_line(s, now))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("watch", help="follow a job's or Studio log's progress (run in the background)")
    w.add_argument("job", nargs="?", help="job name (or use --studio and --log)")
    w.add_argument("--studio", help="Studio whose log file to tail instead of a job")
    w.add_argument("--log", help=f"log file inside the Studio; relative paths start at {STUDIO_HOME}")
    w.add_argument("--run", help="run this belongs to (default: the job name, or the log file's stem)")
    w.add_argument("--teamspace", help="owner/teamspace (default: the CLI's configured teamspace)")
    w.add_argument("--note", help="why this job was (re)launched, shown in the run history")
    w.add_argument("--query", help="server-side log filter, e.g. PROGRESS for very chatty jobs "
                                   "(hides error lines from stall causes)")
    w.add_argument("--relaunch-wait", type=float, default=1800.0,
                   help="seconds a failed run waits for a relaunch before it is final (default 1800)")
    w.set_defaults(fn=cmd_watch)

    a = sub.add_parser("abandon", help="end a failed run's wait for a relaunch")
    a.add_argument("run")
    a.add_argument("--note")
    a.set_defaults(fn=cmd_abandon)

    e = sub.add_parser("events", help="print notable events as they happen (for a Monitor)")
    e.add_argument("--run", help="only this run; exit once it reaches a final state")
    e.add_argument("--from-start", action="store_true", help="replay earlier events first")
    e.set_defaults(fn=cmd_events)

    s = sub.add_parser("statusline", help="render progress bars for the Claude Code status line")
    s.add_argument("--config", action="store_true",
                   help="print the statusLine setting to add (run from the directory Claude Code was started in)")
    s.set_defaults(fn=cmd_statusline)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
