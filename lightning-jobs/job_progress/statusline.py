"""Draws the progress bars for the Claude Code status line from the state files."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from .core import FINAL_PHASES, FINAL_VISIBLE_FOR, STATE_STALE_AFTER, bar, fmt_duration, pct
from .settings import SETTINGS_FILES, is_ours, statusline_setting, statusline_snippet, user_settings
from .store import install_launcher, pid_alive, progress_dir, read_json, session_dir

MAX_STATUS_ROWS = 10


def stage_track(s: dict[str, Any], now: float, keep: int = 3) -> str:
    """`setup ✔ 1m05s · train ✔ 9m40s · ▸ eval_ft 4m32s`: the last few stages and the current one."""
    stages = s.get("stages") or []
    parts = []
    for st in stages[-(keep + 1) :]:
        if st["end"] is None:
            parts.append(f"▸ {st['name']} {fmt_duration(now - st['start'])}")
        else:
            parts.append(f"{st['name']} ✔ {fmt_duration(st['end'] - st['start'])}")
    if len(stages) > keep + 1:
        parts.insert(0, "…")
    return " · ".join(parts)


def job_fraction(s: dict[str, Any]) -> float | None:
    """Finished stages plus the current stage's own fraction, over the declared stage count."""
    count = s.get("stage_count")
    if not count:
        return None
    frac = s["step"] / s["total"] if s.get("total") and s.get("step") is not None else 0.0
    return max(0.0, min(1.0, ((s.get("stage_index") or 1) - 1 + min(frac, 1.0)) / count))


def render_rows(s: dict[str, Any], now: float, expand: bool = True) -> list[str]:
    """A running run with stages: a header row with the whole-job bar, then one row per stage of
    the current attempt. Anything else, or `expand=False`, is the single summary line."""
    stages = [st for st in s.get("stages") or [] if st.get("attempt", 1) == s.get("attempt_no", 1)]
    if not expand or not stages or s["phase"] in FINAL_PHASES:
        return [render_line(s, now)]
    since = lambda t: fmt_duration(now - t) if t else "…"  # noqa: E731
    phase, attempt = s["phase"], s.get("attempt_no", 1)
    icon = {"pending": "⏳", "starting": "▶", "running": "▶", "recovering": "⟳", "stalled": "⚠", "waiting": "✖"}.get(
        phase, "?"
    )
    head = f"{icon} {s['run']}"
    frac = job_fraction(s)
    if frac is not None:
        head += f"  {bar(round(frac * 1000), round(frac * 1000), 1000)}  {int(frac * 100):3d}%"
    info = []
    if s.get("stage_count"):
        info.append(f"stage {s.get('stage_index')}/{s['stage_count']}")
    if attempt > 1:
        info.append(f"attempt {attempt}")
    if s["setbacks"]:
        info.append(f"↺{len(s['setbacks'])}" + (f" (+{fmt_duration(s['lost_s'])})" if s["lost_s"] >= 1 else ""))
    cause = s["last_error"]
    if phase == "pending":
        info.append(f"{s.get('pending_note') or 'waiting for machine'} {since(s['pending_since'])}")
    elif phase == "stalled":
        info.append(f"stalled {since(s['last_sample_at'])}" + (f" · {cause[:50]}" if cause else ""))
    elif phase == "waiting":
        info.append(f"failed · waiting for relaunch {since(s['issue_since'])}" + (f" · {cause[:40]}" if cause else ""))
    if s["cost"] is not None:
        info.append(f"${s['cost']:.2f}")
    rows = [head + ("  " + " · ".join(info) if info else "")]

    width = max(len(st["name"]) for st in stages)
    for st in stages:
        name = st["name"].ljust(width)
        if st["end"] is not None:
            rows.append(f"   ✔ {name}  {fmt_duration(st['end'] - st['start'])}")
            continue
        row = f"   ▸ {name}"
        step, total = s["step"], s["total"]
        if total and st["name"] == s.get("stage"):
            peak = s["peak"] if s.get("peak_epoch") == s["epoch"] else step
            row += f"  {bar(step or 0, peak or step or 0, total)}  {pct(step, total):3d}%"
            if step is not None and step >= total:
                row += f"  finished · {since(s['last_sample_at'])} ago"
            else:
                row += f"  ETA {fmt_duration(s['eta_s'])}"
                if peak and step is not None and peak > step:
                    row += f" · peak {pct(peak, total)}%"
        else:
            # no bar until the stage's first reading; say so, so an empty row doesn't look broken
            row += f"  no progress reported yet · {since(st['start'])}"
        rows.append(row)
    if s.get("stage_count") and len(stages) < s["stage_count"]:
        left = s["stage_count"] - max(len(stages), s.get("stage_index") or 0)
        if left > 0:
            rows.append(f"   · {left} more stage{'s' if left > 1 else ''}")
    stale = s["updated_at"] and now - s["updated_at"] > STATE_STALE_AFTER
    return [f"\033[2m{r} · stale (poller not running?)\033[0m" if stale and i == 0 else r for i, r in enumerate(rows)]


def render_line(s: dict[str, Any], now: float) -> str:
    phase, run = s["phase"], s["run"]
    step, total = s["step"], s["total"]
    peak = s["peak"] if s.get("peak_epoch") == s["epoch"] else step
    icon = {
        "pending": "⏳",
        "starting": "▶",
        "running": "▶",
        "recovering": "⟳",
        "stalled": "⚠",
        "waiting": "✖",
        "done": "✔",
        "failed": "✖",
        "stopped": "■",
        "abandoned": "✖",
    }.get(phase, "?")
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


def shown(d: Path, s: dict[str, Any], now: float) -> bool:
    """Finished runs linger for a while. So do orphans, runs whose poller died before they
    finished: flagged as stale at first, then dropped, since nothing will ever finish them."""
    if s["phase"] in FINAL_PHASES:
        return now - (s.get("finished_at") or 0) < FINAL_VISIBLE_FOR
    if now - (s.get("updated_at") or 0) < FINAL_VISIBLE_FOR:
        return True
    return pid_alive((read_json(d / "runs" / f"{s['run']}.json") or {}).get("pid"))


def print_config(user: bool) -> int:
    """Print the statusLine block, and where to merge it, on stderr."""
    project_dir = session_dir()
    target = user_settings() if user else os.path.join(project_dir, SETTINGS_FILES[0])
    path, cmd = statusline_setting(project_dir, user=user)
    print(json.dumps(statusline_snippet(cmd), indent=2))
    notes = [f"# merge into {target}"]
    if cmd and is_ours(cmd):
        notes.append(f"# already set up in {path}")
    elif cmd:
        notes.append(f"# replaces the status line set in {path}, and still runs it")
    if user:
        own, own_cmd = statusline_setting(project_dir)
        if own_cmd and own != user_settings() and not is_ours(own_cmd):
            notes.append(f"# note: {own} sets its own status line, which hides this one in that project")
    try:
        install_launcher(progress_dir())
    except OSError:  # e.g. inside an agent sandbox; the poller writes it when it starts
        notes.append("# the launcher it runs is written by `progress.py watch` when it starts")
    print("\n".join(notes), file=sys.stderr)
    return 0


def cmd_statusline(args: argparse.Namespace) -> int:
    if args.config:
        return print_config(args.user)
    if not sys.stdin.isatty():
        sys.stdin.read()  # Claude Code sends session JSON; the bars don't depend on it
    d = progress_dir()
    now = time.time()
    states = [s for s in (read_json(p) for p in sorted((d / "state").glob("*.json"))) if s]
    visible = [s for s in states if shown(d, s, now)]
    visible.sort(key=lambda s: (s["phase"] in FINAL_PHASES, -(s.get("updated_at") or 0)))
    rows: list[str] = []
    for i, s in enumerate(visible[:5]):
        # expand the two most recently active runs; the rest stay one line each
        out = render_rows(s, now, expand=i < 2)
        if rows and len(rows) + len(out) > MAX_STATUS_ROWS:
            out = [render_line(s, now)]
        rows += out
    print("\n".join(rows[:MAX_STATUS_ROWS]))
    return 0
