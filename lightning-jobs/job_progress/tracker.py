"""Turns log lines and job status into a run's progress, ETA, stages and setbacks.

Pure logic: no network and no files, so every rule here is unit-testable.
"""

from __future__ import annotations

import re
from datetime import datetime
from statistics import median
from typing import Any

from .core import fmt_duration, make_event, pct

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


def split_timestamp(text: str) -> tuple[float | None, str]:
    """Strip the ISO-8601 prefix `job.logs(timestamps=True)` adds; return (epoch seconds, rest)."""
    head, _, rest = text.partition(" ")
    if head[:1].isdigit() and "T" in head:
        try:
            return datetime.fromisoformat(head.replace("Z", "+00:00")).timestamp(), rest
        except ValueError:
            pass
    return None, text


def parse_progress(message: str) -> dict[str, Any] | None:
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


def parse_error(message: str) -> str | None:
    if "Traceback (most recent call last)" in message:
        return None  # the line after it names the actual error
    if WARN_RE.search(message):
        return None
    m = ERROR_RE.search(message)
    return message.strip()[:120] if m else None


def new_state(run: str) -> dict[str, Any]:
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
        "attempt_no": 1,
        "fail_stage": None,
        "prev_error": None,
        "attempt_fresh": False,
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


def _progress_text(s: dict[str, Any]) -> str:
    p = pct(s["step"], s["total"])
    parts = [f"{p}%" if p is not None else "starting"]
    if s.get("stage"):
        parts.insert(0, s["stage"])
    if s["eta_s"] is not None:
        parts.append(f"ETA {fmt_duration(s['eta_s'])}")
    if s["setbacks"]:
        parts.append(f"↺{len(s['setbacks'])}")
    return " · ".join(parts)


def recent_cause(s: dict[str, Any], since: float | None) -> str | None:
    if s["last_error"] and s["last_error_at"] is not None and (since is None or s["last_error_at"] >= since - 60):
        return s["last_error"]
    return None


def on_line(s: dict[str, Any], job: str, text: str, wall_now: float, dedupe: bool = True) -> list[dict[str, Any]]:
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


def stage_summary(s: dict[str, Any], until: float) -> str:
    parts = []
    for st in s.get("stages") or []:
        end = st["end"] if st["end"] is not None else until
        parts.append(f"{st['name']} {fmt_duration(end - st['start'])}")
    return ", ".join(parts)


def close_stage(s: dict[str, Any], at: float) -> None:
    """End the current stage without starting another."""
    if s["stage"] is not None:
        s["stage_snap"][s["stage"]] = {k: s[k] for k in PROGRESS_KEYS}
        s["stage"] = None
    if s["stages"] and s["stages"][-1]["end"] is None:
        s["stages"][-1]["end"] = at


def end_attempt(s: dict[str, Any], at: float) -> None:
    """Close the attempt. Remember the stage it broke in: that stage is the only one a relaunch is
    compared with, since only there was ground lost. The attempt's last error moves aside, so it
    explains the setback but is never blamed for anything the next attempt does."""
    s["fail_stage"] = s["stage"]
    close_stage(s, at)
    s["prev_error"] = s["last_error"]
    s["last_error"] = s["last_error_at"] = None
    s["attempt_no"] = s.get("attempt_no", 1) + 1
    s["attempt_fresh"] = True  # the next reading is compared with this attempt's


def on_stage(
    s: dict[str, Any], name: str, at: float, index: int | None = None, count: int | None = None
) -> list[dict[str, Any]]:
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
    # only the stage the last attempt broke in resumes its old bar; every other stage starts fresh
    snap = s["stage_snap"].get(name) if name == s.get("fail_stage") else None
    if snap is not None:
        s["fail_stage"] = None
        s["attempt_fresh"] = True  # this stage's first reading shows how much ground was lost
    s.update(dict.fromkeys(PROGRESS_KEYS))
    s["milestone"] = 0
    if snap:
        s.update(snap)
    s.update(samples=[], since_reset=0, rate=None, eta_s=None, stage=name, stage_since=at)
    prev = s["stages"][-1] if s["stages"] else None
    s["stages"].append({"name": name, "start": at, "end": None, "attempt": s.get("attempt_no", 1)})
    msg = f"stage {name}"
    if snap is not None:
        msg += " again"
    if prev is not None:
        msg += f" ({prev['name']} took {fmt_duration(prev['end'] - prev['start'])})"
    return [make_event("stage", s["run"], msg, at)]


def on_partial(s: dict[str, Any], text: str, now: float) -> list[dict[str, Any]]:
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

    def feed(self, chunk: bytes) -> tuple[list[str], str]:
        *lines, self.buf = (self.buf + chunk).split(b"\n")
        if len(self.buf) > 65536:  # a tqdm bar that never ends its line: keep the latest redraws
            self.buf = self.buf[-65536:]
        return [ln.decode("utf-8", "replace") for ln in lines], self.buf.decode("utf-8", "replace")


def _classify(s: dict[str, Any], r: dict[str, Any]) -> str | None:
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


def on_sample(s: dict[str, Any], r: dict[str, Any], at: float) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    run = s["run"]
    if s["source"] == "tqdm" and r["source"] == "progress":
        s.update(step=None, total=None, peak=None, epoch=None, samples=[], since_reset=0)
    kind = _classify(s, r)
    if kind and r["source"] == "tqdm" and r["epoch"] == s["epoch"] and not s.get("attempt_fresh"):
        # scripts draw a fresh tqdm bar per pass (lm-eval: one per few-shot setting), so a drop
        # within an attempt is a new bar; only the first reading after a relaunch shows ground
        # lost, and so does an epoch number going backwards
        s.update(samples=[], since_reset=0, eta_s=None, rate=None, peak=r["step"], peak_epoch=r["epoch"])
        s["milestone"] = (pct(r["step"], r["total"]) or 0) // 10
        kind = None

    if kind:
        issue_start = s["issue_since"] or s["last_sample_at"] or at
        lost = max(0.0, at - issue_start)
        cause = recent_cause(s, s["issue_since"]) or s.get("prev_error")
        s["prev_error"] = None
        s["setbacks"].append(
            {
                "at": at,
                "kind": kind,
                "from": s["step"],
                "to": r["step"],
                "peak": s["peak"],
                "cause": cause,
                "lost_s": lost,
                "job": s["job"],
            }
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
        events.append(
            make_event(
                "setback", run, f"{what} · {fmt_duration(lost)} lost" + (f" · cause: {cause}" if cause else ""), at
            )
        )
    elif s["phase"] == "stalled" and s["issue_since"] is not None:
        stalled = max(0.0, at - s["issue_since"])
        s["lost_s"] += stalled
        events.append(make_event("recovered", run, f"progress again after {fmt_duration(stalled)} stalled", at))

    if s["last_sample_at"] is not None and not kind and r["step"] != s["step"]:
        s["gaps"] = (s["gaps"] + [at - s["last_sample_at"]])[-20:]
    if r["epoch"] != s["epoch"]:
        s["samples"] = []  # a new epoch restarts a per-epoch bar; that is not a setback
    s.update(
        step=r["step"], total=r["total"], epoch=r["epoch"], source=r["source"], last_sample_at=at, issue_since=None
    )
    if r["attempt"] is not None:
        s["attempt"] = r["attempt"]
    if s["peak"] is None or (r["epoch"] or 0, r["step"]) > (s["peak_epoch"] or 0, s["peak"]):
        s["peak"], s["peak_epoch"] = r["step"], r["epoch"]
    s["samples"] = (s["samples"] + [[at, r["step"]]])[-RATE_WINDOW:]
    s["since_reset"] += 1
    s["attempt_fresh"] = False

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
        events.append(make_event("milestone", run, _progress_text(s), at))
    return events


def stall_threshold(s: dict[str, Any]) -> float:
    return max(120.0, 4 * median(s["gaps"])) if s["gaps"] else 300.0


def on_tick(s: dict[str, Any], status: str, platform_attempt: int | None, now: float) -> list[dict[str, Any]]:
    """Periodic check from the supervisor: status changes, platform retries and stalls."""
    events: list[dict[str, Any]] = []
    run, prev = s["run"], s["status"]
    s["status"] = status

    if platform_attempt and s["platform_attempt"] and platform_attempt > s["platform_attempt"]:
        s["issue_since"] = s["issue_since"] or s["last_sample_at"] or now
        end_attempt(s, now)
        events.append(make_event("retry", run, f"platform retry: attempt {platform_attempt}", now))
    if platform_attempt:
        s["platform_attempt"] = platform_attempt

    if status == "Pending":
        if prev == "Running":
            s["issue_since"] = s["issue_since"] or s["last_sample_at"] or now
            end_attempt(s, now)
            events.append(make_event("requeued", run, "back to Pending (requeued or retried)", now))
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
                events.append(make_event("started", run, "running", now))
        if s["phase"] == "starting" and s["started_at"] and not s["no_progress_warned"] and now - s["started_at"] > 600:
            s["no_progress_warned"] = True
            events.append(make_event("no-progress", run, "running 10m with no PROGRESS or tqdm line yet", now))
        bar_open = s["step"] is not None and s["total"] and s["step"] < s["total"]
        if s["phase"] in ("running", "recovering") and s["last_sample_at"] is not None and bar_open:
            # after a relaunch or requeue, give the new process time to start before calling it stalled
            restarting = s["last_sample_at"] < s["job_running_since"]
            quiet = now - max(s["last_sample_at"], s["job_running_since"], s.get("stage_since") or 0)
            if quiet > (600.0 if restarting else stall_threshold(s)):
                s["phase"] = "stalled"
                s["issue_since"] = s["issue_since"] or s["last_sample_at"]
                cause = recent_cause(s, s["last_sample_at"])
                events.append(
                    make_event(
                        "stall",
                        run,
                        f"stalled {fmt_duration(quiet)} at "
                        f"{pct(s['step'], s['total'])}%" + (f" · {cause}" if cause else ""),
                        now,
                    )
                )
    return events


def finish(s: dict[str, Any], phase: str, now: float) -> dict[str, Any]:
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
        cause = recent_cause(s, None)
        msg = f"{phase} at {p if p is not None else 0}%" + (f" · {cause}" if cause and phase != "stopped" else "")
    if s["cost"] is not None:
        msg += f" · ${s['cost']:.2f}"
    return make_event(phase, run, msg, now)
