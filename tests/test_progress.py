"""Unit tests for lightning-jobs/progress.py and its job_progress package: parsing, ETA, stalls and setbacks.

Run with: python3 -m unittest discover -s tests
"""

import os
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar, Optional, TypeVar
from unittest import mock

JOBS_DIR = Path(__file__).resolve().parent.parent / "lightning-jobs"
ENTRY = JOBS_DIR / "progress.py"
sys.path.insert(0, str(JOBS_DIR))

from job_progress import core, settings, statusline, store, tracker, watch  # noqa: E402
from job_progress import events as feed  # noqa: E402  (tests use `events` for event lists)

T0 = 1_800_000_000.0


def ts(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()


T = TypeVar("T")


def must(value: Optional[T]) -> T:
    """Fail the test on None, and tell the type checker the value is set from here on."""
    assert value is not None
    return value


class Run:
    """Drives a tracker the way the poller does, with explicit clock values."""

    def __init__(self, name="train-42"):
        self.s = tracker.new_state(name)
        self.events = []
        self.tick("Running", T0)

    def line(self, t, text, job="train-42"):
        self.events += tracker.on_line(self.s, job, f"{ts(t)} {text}", t)

    def tick(self, status, t, attempt=None):
        self.events += tracker.on_tick(self.s, status, attempt, t)

    def kinds(self):
        return [e["kind"] for e in self.events]


class Parsing(unittest.TestCase):
    def test_progress_line(self):
        r = must(tracker.parse_progress("PROGRESS 450/1000 attempt=2 loss=0.3"))
        self.assertEqual((r["step"], r["total"], r["attempt"], r["source"]), (450, 1000, 2, "progress"))

    def test_tqdm_last_redraw_wins(self):
        line = " 10%|█         | 100/1000 [00:10<01:30]\r 45%|████▌     | 450/1000 [00:45<00:55, 10it/s]"
        r = must(tracker.parse_progress(line))
        self.assertEqual((r["step"], r["total"], r["source"]), (450, 1000, "tqdm"))

    def test_tqdm_epoch_and_validation(self):
        self.assertEqual(must(tracker.parse_progress("Epoch 3:  40%|████      | 40/100 [00:04<00:06]"))["epoch"], 3)
        self.assertIsNone(tracker.parse_progress("Validation DataLoader 0:  50%|█████     | 5/10 [00:01<00:01]"))

    def test_error_lines(self):
        self.assertIsNone(tracker.parse_error("Traceback (most recent call last):"))
        self.assertIn("OutOfMemoryError", must(tracker.parse_error("torch.OutOfMemoryError: CUDA out of memory.")))
        self.assertIsNone(tracker.parse_error("step 10 loss 0.4"))

    def test_bar(self):
        self.assertEqual(core.bar(6, 9, 20, width=20), "▓" * 6 + "▒" * 3 + "░" * 11)
        self.assertEqual(core.fmt_duration(160), "2m40s")
        self.assertEqual(core.fmt_duration(3900), "1h05m")


class Progress(unittest.TestCase):
    def test_eta_and_milestones(self):
        r = Run()
        for i in range(0, 60):
            r.line(T0 + 10 * i, f"PROGRESS {10 * i}/1000")
        self.assertEqual(r.s["phase"], "running")
        self.assertAlmostEqual(r.s["rate"], 1.0)
        self.assertAlmostEqual(r.s["eta_s"], 410.0)
        self.assertEqual(
            [e["msg"].split(" · ")[0] for e in r.events if e["kind"] == "milestone"],
            [f"train-42: {p}%" for p in (10, 20, 30, 40, 50)],
        )

    def test_reconnect_replay_is_ignored(self):
        r = Run()
        for i in range(5):
            r.line(T0 + i, f"PROGRESS {i + 1}/10")
        for i in range(5):  # a reconnect replays history from the start
            r.line(T0 + i, f"PROGRESS {i + 1}/10")
        self.assertEqual(r.s["step"], 5)
        self.assertEqual(r.s["setbacks"], [])

    def test_stall_then_recovery(self):
        r = Run()
        for i in range(10):
            r.line(T0 + 10 * i, f"PROGRESS {i}/100")
        r.line(T0 + 95, "RuntimeError: NCCL watchdog timeout")
        r.tick("Running", T0 + 100)
        self.assertNotEqual(r.s["phase"], "stalled")
        r.tick("Running", T0 + 90 + 121)
        self.assertEqual(r.s["phase"], "stalled")
        self.assertIn("NCCL", r.events[-1]["msg"])
        r.line(T0 + 400, "PROGRESS 10/100")
        self.assertEqual(r.kinds()[-2:], ["recovered", "milestone"])
        self.assertEqual(r.s["setbacks"], [])  # a stall that resumes in place is not a setback
        self.assertAlmostEqual(r.s["lost_s"], 310.0)


class Setbacks(unittest.TestCase):
    def test_partial_regression_in_same_job(self):
        r = Run()
        for i in range(46):
            r.line(T0 + 10 * i, f"PROGRESS {10 * i}/1000")
        r.line(T0 + 455, "ValueError: loss is NaN, rolling back")
        r.line(T0 + 500, "PROGRESS 300/1000")  # the script reloaded an earlier checkpoint
        sb = r.s["setbacks"][-1]
        self.assertEqual((sb["kind"], sb["from"], sb["to"], sb["peak"]), ("resume", 450, 300, 450))
        self.assertIn("NaN", sb["cause"])
        self.assertEqual(r.s["phase"], "recovering")
        self.assertIsNone(r.s["eta_s"])  # old rate discarded
        self.assertIn("▒", statusline.render_line(r.s, T0 + 500))
        for i in range(1, 4):
            r.line(T0 + 500 + 10 * i, f"PROGRESS {300 + 10 * i}/1000")
        self.assertEqual(r.s["phase"], "running")
        self.assertIsNotNone(r.s["eta_s"])
        for i in range(4, 20):
            r.line(T0 + 500 + 10 * i, f"PROGRESS {300 + 10 * i}/1000")
        self.assertEqual(r.s["peak"], 490)  # passing the old peak clears the lost ground
        self.assertNotIn("▒", statusline.render_line(r.s, T0 + 700))
        self.assertIn("↺1", statusline.render_line(r.s, T0 + 700))

    def test_failure_then_resume_in_new_job(self):
        r = Run()
        for i in range(46):
            r.line(T0 + 10 * i, f"PROGRESS {10 * i}/1000")
        r.line(T0 + 455, "torch.OutOfMemoryError: CUDA out of memory")
        # poller: job Failed -> waiting; session relaunches as train-42-a2
        r.s["phase"], r.s["issue_since"] = "pending", T0 + 450
        r.s["job"], r.s["job_running_since"] = "train-42-a2", None
        r.tick("Pending", T0 + 600)
        r.tick("Running", T0 + 700)
        r.tick("Running", T0 + 900)
        self.assertNotEqual(r.s["phase"], "stalled")  # a relaunch gets time to start
        r.line(T0 + 950, "PROGRESS 400/1000 attempt=2", job="train-42-a2")
        sb = r.s["setbacks"][-1]
        self.assertEqual((sb["kind"], sb["to"]), ("resume", 400))
        self.assertAlmostEqual(sb["lost_s"], 500.0)
        self.assertIn("out of memory", sb["cause"])

    def test_failure_then_restart_from_scratch(self):
        r = Run()
        for i in range(46):
            r.line(T0 + 10 * i, f"PROGRESS {10 * i}/1000")
        r.line(T0 + 1000, "PROGRESS 0/1000", job="train-42-a2")
        self.assertEqual(r.s["setbacks"][-1]["kind"], "restart")
        self.assertTrue(statusline.render_line(r.s, T0 + 1000).split()[2].startswith("▒"))

    def test_new_setup(self):
        r = Run()
        for i in range(10):
            r.line(T0 + 10 * i, f"PROGRESS {10 * i}/1000")
        r.line(T0 + 200, "PROGRESS 5/500")
        self.assertEqual(r.s["setbacks"][-1]["kind"], "new-setup")
        self.assertEqual(r.s["peak"], 5)

    def test_requeue_and_platform_retry(self):
        r = Run()
        r.tick("Running", T0 + 1, attempt=1)
        r.line(T0 + 10, "PROGRESS 50/100")
        r.tick("Pending", T0 + 20, attempt=2)
        self.assertEqual(r.kinds()[-2:], ["retry", "requeued"])
        r.tick("Running", T0 + 60, attempt=2)
        r.line(T0 + 80, "PROGRESS 40/100")
        self.assertEqual(r.s["setbacks"][-1]["kind"], "resume")
        self.assertAlmostEqual(r.s["setbacks"][-1]["lost_s"], 70.0)

    def test_tqdm_epoch_rollover_is_not_a_setback(self):
        r = Run()
        for ep in range(3):
            for i in range(0, 101, 20):
                r.line(T0 + ep * 100 + i, f"Epoch {ep}: {i}%|███| {i}/100 [00:01<00:01]")
        self.assertEqual(r.s["setbacks"], [])
        r.line(T0 + 400, "Epoch 1: 60%|███| 60/100 [00:01<00:01]")
        self.assertEqual(r.s["setbacks"][-1]["kind"], "resume")

    def test_progress_lines_outrank_tqdm(self):
        r = Run()
        r.line(T0 + 1, " 90%|█████████ | 9/10 [00:01<00:00]")
        r.line(T0 + 2, "PROGRESS 100/1000")
        r.line(T0 + 3, " 10%|█         | 1/10 [00:01<00:00]")
        self.assertEqual((r.s["step"], r.s["source"], r.s["setbacks"]), (100, "progress", []))


class LiveRunFindings(unittest.TestCase):
    """Problems seen in the first live run (Qwen3.5-4B SFT job)."""

    def test_allocator_warning_is_not_an_error(self):
        warn = (
            "[W924 14:51:32.327618612 CUDACachingAllocator.cpp:3933] memory allocation failed "
            "with OOM on device 0 while trying to allocate"
        )
        self.assertIsNone(tracker.parse_error(warn))
        self.assertIsNone(tracker.parse_error("UserWarning: out of memory fallback in use"))
        self.assertIsNotNone(tracker.parse_error("torch.OutOfMemoryError: CUDA out of memory."))

    def test_reestimated_total_is_not_a_setback(self):
        r = Run()
        for i in range(1, 8):
            r.line(T0 + 10 * i, f"PROGRESS {10 * i}/{300 - 5 * i}")  # the script refines its total
        self.assertEqual(r.s["setbacks"], [])
        self.assertEqual((r.s["step"], r.s["total"]), (70, 265))

    def test_time_budget_units_give_the_right_eta(self):
        r = Run()
        for t in range(0, 101, 10):
            r.line(T0 + t, f"PROGRESS {t}/300")  # seconds of a 300 s budget
        self.assertAlmostEqual(r.s["eta_s"], 200, delta=1)

    def test_stages_keep_their_own_bars_and_quiet_stages_do_not_stall(self):
        r = Run()
        r.line(T0 + 1, "PROGRESS_PHASE setup")
        r.line(T0 + 60, "PROGRESS_PHASE train")
        for i in range(11):
            r.line(T0 + 60 + 10 * i, f"PROGRESS {10 * i}/100")
        r.line(T0 + 175, "PROGRESS_PHASE eval")
        self.assertIsNone(r.s["step"])
        r.tick("Running", T0 + 175 + 900)  # 15 quiet minutes of eval: not a stall
        self.assertNotIn("stall", r.kinds())
        # the run keeps a line after its bar's stage ends, instead of shrinking to a name
        self.assertIn("train ✔ 1m55s · ▸ eval 15m00s", statusline.render_line(r.s, T0 + 1075))
        stages = [e["msg"] for e in r.events if e["kind"] == "stage"]
        self.assertEqual(stages[-1], "train-42: stage eval (train took 1m55s)")
        done = tracker.finish(r.s, "done", T0 + 1200)
        self.assertIn("setup 59s, train 1m55s, eval 17m05s", done["msg"])

    def test_counted_stages_draw_a_whole_job_bar(self):
        r = Run()
        r.line(T0 + 1, "PROGRESS_PHASE setup 1/4")
        r.line(T0 + 60, "PROGRESS_PHASE train 2/4")
        r.line(T0 + 61, "PROGRESS 5/10")
        self.assertIn("[train]", statusline.render_line(r.s, T0 + 62))  # the stage's own bar wins
        r.line(T0 + 600, "PROGRESS_PHASE eval_ft 4/4")
        line = statusline.render_line(r.s, T0 + 700)
        self.assertIn("▓" * 15 + "░" * 5 + "  stage 4/4", line)
        self.assertIn("▸ eval_ft 1m40s", line)

    def test_relaunch_that_trains_again_is_compared_with_old_training(self):
        r = Run()
        r.line(T0 + 1, "PROGRESS_PHASE train")
        for i in range(6):
            r.line(T0 + 10 * i, f"PROGRESS {10 * i}/100")
        r.line(T0 + 60, "RuntimeError: NCCL timeout")
        tracker.end_attempt(r.s, T0 + 70)  # crashed in train
        r.line(T0 + 100, "PROGRESS_PHASE setup")  # relaunched attempt starts over
        r.line(T0 + 150, "PROGRESS_PHASE train")
        r.line(T0 + 160, "PROGRESS 30/100")  # resumed from a checkpoint
        self.assertEqual(r.s["setbacks"][-1]["kind"], "resume")
        self.assertIn("NCCL timeout", r.s["setbacks"][-1]["cause"])  # the old attempt's error explains it
        self.assertEqual(r.s["peak"], 50)
        self.assertIn("stage train again", [e["msg"].split(": ", 1)[1].split(" (")[0] for e in r.events])

    def test_stages_after_the_broken_one_start_fresh(self):
        """Live run: attempt 5's eval was compared with attempts 1-4's evals (0% ↺3, peak 1%)."""
        r = Run()
        for attempt in range(3):
            r.line(T0 + 1000 * attempt + 1, "PROGRESS_PHASE train")
            r.line(T0 + 1000 * attempt + 2, "PROGRESS 100/100")
            r.line(T0 + 1000 * attempt + 3, "PROGRESS_PHASE eval")
            r.line(T0 + 1000 * attempt + 4, "PROGRESS 13/1319")
            r.line(T0 + 1000 * attempt + 5, "ValueError: bad checkpoint")
            tracker.end_attempt(r.s, T0 + 1000 * attempt + 10)  # each attempt crashed in eval
        r.line(T0 + 3001, "PROGRESS_PHASE train")
        r.line(T0 + 3002, "PROGRESS 100/100")  # train starts fresh: it never broke
        r.line(T0 + 3003, "PROGRESS_PHASE eval")
        r.line(T0 + 3004, "PROGRESS 0/1319")  # eval broke last time: this one is compared
        # only the last eval lost ground (13 → 0); train never broke, so it never counts
        self.assertEqual([(b["kind"], b["from"], b["to"]) for b in r.s["setbacks"]], [("restart", 13, 0)])
        r.tick("Running", T0 + 3005)
        self.assertIsNone(r.s["last_error"])  # attempt 5 isn't blamed for the old crash

    def test_rows_show_each_stage_of_the_current_attempt(self):
        r = Run()
        r.line(T0 + 1, "PROGRESS_PHASE setup 1/3")
        r.line(T0 + 56, "PROGRESS_PHASE train 2/3")
        for i in range(11):
            r.line(T0 + 56 + 30 * i, f"PROGRESS {10 * i}/100")
        r.line(T0 + 410, "PROGRESS_PHASE eval 3/3")
        for i in range(4):
            r.line(T0 + 420 + 10 * i, f"PROGRESS {i * 50}/1319")
        rows = statusline.render_rows(r.s, T0 + 460)
        self.assertEqual(len(rows), 4)
        self.assertIn("stage 3/3", rows[0])
        self.assertIn(" 70%", rows[0])  # 2 stages done + ~11% of eval, over 3
        self.assertTrue(rows[1].startswith("   ✔ setup"))
        self.assertTrue(rows[2].startswith("   ✔ train"))
        self.assertIn("▸ eval ", rows[3])
        self.assertIn("ETA", rows[3])
        self.assertEqual(len(statusline.render_rows(r.s, T0 + 460, expand=False)), 1)

    def test_a_stage_with_no_reading_yet_says_so(self):
        r = Run()
        r.line(T0 + 1, "PROGRESS_PHASE setup 1/2")
        r.line(T0 + 60, "PROGRESS_PHASE train 2/2")  # model loading: no PROGRESS line yet
        self.assertIn("▸ train  no progress reported yet · 2m00s", statusline.render_rows(r.s, T0 + 180)[2])

    def test_a_new_tqdm_bar_in_the_same_attempt_is_not_a_setback(self):
        r = Run()
        r.line(T0 + 1, "PROGRESS_PHASE eval")
        for shots in range(2):  # lm-eval: one bar for 0-shot, then a new one for 5-shot
            for i in range(0, 1320, 330):
                r.line(T0 + 100 * shots + i / 10, f"Processed prompts: {i * 100 // 1319}%|█| {i}/1319")
        self.assertEqual(r.s["setbacks"], [])
        self.assertNotIn("setback", r.kinds())
        self.assertEqual(r.s["peak"], r.s["step"])  # no ▒ left over from the first bar

    def test_a_tqdm_drop_after_a_relaunch_is_a_setback(self):
        r = Run()
        for i in range(0, 60, 10):
            r.line(T0 + i, f"{i}%|█| {i}/100")
        tracker.end_attempt(r.s, T0 + 70)  # the job failed and was relaunched
        r.line(T0 + 200, " 30%|█| 30/100")
        self.assertEqual(r.s["setbacks"][-1]["kind"], "resume")

    def test_rows_hide_earlier_attempts(self):
        r = Run()
        r.line(T0 + 1, "PROGRESS_PHASE train")
        tracker.end_attempt(r.s, T0 + 50)
        r.line(T0 + 60, "PROGRESS_PHASE setup")
        rows = statusline.render_rows(r.s, T0 + 70)
        self.assertEqual([x.split()[1] for x in rows[1:]], ["setup"])
        self.assertIn("attempt 2", rows[0])

    def test_statusline_hint_and_config(self):
        proj = tempfile.mkdtemp()
        with mock.patch.dict(os.environ, HOME=proj):  # no user settings either
            self.assertIn("not set up", must(settings.statusline_hint("r", proj)))
            os.makedirs(os.path.join(proj, ".claude"))
            with open(os.path.join(proj, ".claude", "settings.json"), "w") as f:
                f.write('{"statusLine": {"type": "command", "command": "my-line.sh"}}')
            self.assertIn("keeps their current status line", must(settings.statusline_hint("r", proj)))
            cmd = settings.statusline_snippet("my-line.sh")["statusLine"]["command"]
            self.assertIn("my-line.sh", cmd)
            self.assertIn("progress.py", cmd)
            with open(os.path.join(proj, ".claude", "settings.local.json"), "w") as f:
                f.write('{"statusLine": {"command": "python3 /x/progress.py statusline"}}')
            self.assertIsNone(settings.statusline_hint("r", proj))

    def test_chained_statusline_runs_both(self):
        cmd = settings.statusline_snippet("echo theirs")["statusLine"]["command"]
        env = dict(os.environ, LIGHTNING_PROGRESS_DIR=tempfile.mkdtemp())
        out = subprocess.run(
            ["sh", "-c", cmd], input='{"workspace":{"project_dir":"/x"}}', capture_output=True, text=True, env=env
        ).stdout
        self.assertEqual(out.strip(), "theirs")  # no runs yet, so ours prints nothing

    def test_config_carries_a_custom_state_folder(self):
        d = Path(tempfile.mkdtemp())
        store.ensure_dirs(d)
        s = tracker.new_state("r1")
        s.update(phase="running", step=5, total=10, peak=5, updated_at=time.time())
        store.write_json(d / "state" / "r1.json", s)
        with mock.patch.dict(os.environ, LIGHTNING_PROGRESS_DIR=str(d)):
            cmd = settings.statusline_snippet(None)["statusLine"]["command"]
        self.assertIn("--dir", cmd)
        # the status line runs without the session's environment, and still finds the run
        env = {k: v for k, v in os.environ.items() if k != "LIGHTNING_PROGRESS_DIR"}
        out = subprocess.run(["sh", "-c", cmd], input="{}", capture_output=True, text=True, env=env).stdout
        self.assertIn("r1", out)

    def test_events_only_read_and_outlive_a_failed_attempt(self):
        d = Path(tempfile.mkdtemp()) / "not-yet"  # a sandboxed Monitor can't create this
        proc = subprocess.Popen(
            [sys.executable, str(ENTRY), "--dir", str(d), "events", "--run", "r"],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            time.sleep(1)
            self.assertIsNone(proc.poll())  # waiting for the poller, not crashed
            self.assertFalse(d.exists())  # and it wrote nothing
            store.ensure_dirs(d)
            store.append_events(
                d,
                [
                    core.make_event("hint", "r", "status-line bar is not set up", T0),
                    core.make_event(core.ATTEMPT_FAILED, "r", "attempt 1 failed", T0),
                    core.make_event("done", "r", "done in 5m", T0),
                ],
            )
            out, _ = proc.communicate(timeout=10)
        finally:
            proc.kill()
        self.assertEqual(out.splitlines(), ["r: status-line bar is not set up", "r: attempt 1 failed", "r: done in 5m"])

    def test_events_pass_on_only_what_needs_a_reply(self):
        r = Run()
        r.line(T0 + 1, "PROGRESS_PHASE train")
        for i in range(10):
            r.line(T0 + 10 * i, f"PROGRESS {i}/100")
        r.tick("Running", T0 + 90 + 121)
        r.line(T0 + 400, "PROGRESS 10/100")
        kinds = [e["kind"] for e in r.events if feed.wanted(e, "train-42", False)]
        self.assertEqual(kinds, ["stall"])  # milestones, the stage change and the recovery stay quiet
        loud = [e["kind"] for e in r.events if feed.wanted(e, "train-42", True)]
        self.assertTrue({"stage", "milestone", "recovered", "stall"} <= set(loud))
        self.assertFalse(feed.wanted(r.events[-1], "other-run", True))


class Locations(unittest.TestCase):
    def test_state_dir_does_not_depend_on_cwd(self):
        old = {k: os.environ.pop(k, None) for k in ("LIGHTNING_PROGRESS_DIR", "XDG_STATE_HOME")}
        cwd = os.getcwd()
        try:
            a = store.progress_dir()
            os.chdir(tempfile.mkdtemp())
            self.assertEqual(store.progress_dir(), a)
            self.assertEqual(a, Path.home() / ".local" / "state" / "lightning-progress")
        finally:
            os.chdir(cwd)
            for k, v in old.items():
                if v is not None:
                    os.environ[k] = v

    def test_statusline_ignores_session_dir(self):
        d = tempfile.mkdtemp()
        env = dict(os.environ, LIGHTNING_PROGRESS_DIR=d)
        store.ensure_dirs(Path(d))
        s = tracker.new_state("r1")
        s.update(phase="running", step=5, total=10, peak=5, updated_at=time.time())
        store.write_json(Path(d) / "state" / "r1.json", s)
        script = str(ENTRY)
        out = subprocess.run(
            [sys.executable, script, "statusline"],
            cwd=tempfile.mkdtemp(),
            env=env,
            input='{"workspace":{"project_dir":"/elsewhere"}}',
            capture_output=True,
            text=True,
        )
        self.assertIn("r1", out.stdout)

    def test_orphaned_run_is_flagged_then_dropped(self):
        d = Path(tempfile.mkdtemp())
        store.ensure_dirs(d)
        s = tracker.new_state("r1")
        s.update(phase="pending", pending_since=T0, updated_at=T0)
        store.write_json(d / "runs" / "r1.json", {"run": "r1", "pid": None})
        self.assertTrue(statusline.shown(d, s, T0 + 120))  # stale, still on screen
        self.assertFalse(statusline.shown(d, s, T0 + 3600))  # poller long dead: dropped
        store.write_json(d / "runs" / "r1.json", {"run": "r1", "pid": os.getpid()})
        self.assertTrue(statusline.shown(d, s, T0 + 3600))  # a live poller keeps it

    def test_relaunch_restarts_the_stage_clock(self):
        r = Run()
        r.line(T0 + 1, "PROGRESS_PHASE setup")
        tracker.end_attempt(r.s, T0 + 100)  # attempt 1 ended during setup
        r.line(T0 + 200, "PROGRESS_PHASE setup")  # attempt 2 prints the same stage again
        self.assertEqual(r.s["stage_since"], T0 + 200)
        self.assertIn("stage setup again", r.events[-1]["msg"])


class ScriptDone(BaseException):
    """Ends a scripted Studio run; a BaseException so the poller's retry handler doesn't catch it."""


class FakeStudio:
    """Stands in for lightning_sdk.Studio: runs the read command in a local bash, one scripted step per poll."""

    steps: ClassVar[list] = []
    writer = True

    def __init__(self, name, teamspace=None, create_ok=True):
        assert create_ok is False  # the poller must never create a Studio
        self.status = "Running"

    def run_with_exit_code(self, cmd):
        if not FakeStudio.steps:
            raise ScriptDone()
        FakeStudio.steps.pop(0)()
        out = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True).stdout.strip()
        # macOS has no /proc, so the scripted writer flag stands in for the open-file check
        return out.replace("WRITER 0", "WRITER 1" if FakeStudio.writer else "WRITER 0"), 0


class StudioMode(unittest.TestCase):
    def setUp(self):
        fake_sdk = types.ModuleType("lightning_sdk")
        fake_sdk.__dict__["Studio"] = FakeStudio
        sys.modules["lightning_sdk"] = fake_sdk
        watch.STUDIO_INTERVAL = 0
        self.dir = tempfile.mkdtemp()
        self.log = os.path.join(self.dir, "train.log")

    def tearDown(self):
        sys.modules.pop("lightning_sdk", None)

    def write(self, text, mode="a", writer=True):
        def step():
            with open(self.log, mode) as f:
                f.write(text)
            FakeStudio.writer = writer

        return step

    def run_studio(self, steps):
        FakeStudio.steps, FakeStudio.writer = list(steps), True
        state, events = tracker.new_state("train"), []

        def save(evs):
            events.extend(evs)

        entry = {"kind": "studio", "name": f"s:{self.log}", "studio": "s", "log": self.log}
        args = types.SimpleNamespace(teamspace=None, relaunch_wait=1800.0)
        try:
            outcome = watch.supervise_studio(
                entry, state, threading.Lock(), save, Path(self.dir) / "none.json", 0, args
            )
        except ScriptDone:
            outcome = None
        return outcome, state, events

    def test_crash_relaunch_into_same_log_then_finish(self):
        outcome, state, events = self.run_studio(
            [
                self.write("loading\n"),
                self.write("".join(f"PROGRESS {i}/100\n" for i in range(0, 41, 10))),
                self.write(
                    "Traceback (most recent call last):\nModuleNotFoundError: No module named 'soundfile'\n",
                    writer=False,
                ),  # crashed, no PROGRESS_EXIT line
                lambda: None,  # waiting for a relaunch
                self.write("PROGRESS 0/100\n", mode="w"),  # agent relaunched into the same log
                self.write("PROGRESS 10/100\n 20%|██   | 20/100"),  # tqdm-style partial line
                self.write("\nPROGRESS 100/100\nPROGRESS_EXIT 0\n", writer=False),
            ]
        )
        kinds = [e["kind"] for e in events]
        self.assertEqual(outcome, "Completed")
        self.assertIn(core.ATTEMPT_FAILED, kinds)
        self.assertNotIn("failed", kinds)  # the final kind, which would end a Monitor
        failed = next(e for e in events if e["kind"] == core.ATTEMPT_FAILED)
        self.assertIn("soundfile", failed["msg"])
        self.assertIn("relaunch", kinds)
        self.assertEqual(state["setbacks"][-1]["kind"], "restart")
        self.assertEqual(state["step"], 100)

    def test_attaching_mid_run_replays_no_old_milestones(self):
        _, state, events = self.run_studio(
            [
                self.write("".join(f"PROGRESS {i}/100\n" for i in range(0, 51, 5))),  # history at attach time
                self.write("PROGRESS 60/100\n"),
            ]
        )
        self.assertEqual([e["msg"].split(" · ")[0] for e in events if e["kind"] == "milestone"], ["train: 60%"])
        self.assertEqual(state["step"], 60)

    def test_nonzero_exit_line_waits_then_resume_by_appending(self):
        outcome, state, events = self.run_studio(
            [
                self.write("PROGRESS 50/100\nPROGRESS_EXIT 137\n", writer=False),
                lambda: None,
                self.write("PROGRESS 45/100\n"),  # appended by a resumed run
                self.write("PROGRESS 60/100\nPROGRESS_EXIT 0\n", writer=False),
            ]
        )
        self.assertEqual(outcome, "Completed")
        self.assertIn("exited (137)", next(e["msg"] for e in events if e["kind"] == core.ATTEMPT_FAILED))
        self.assertEqual(state["setbacks"][-1]["kind"], "resume")

    def test_missing_log_is_pending(self):
        outcome, state, _ = self.run_studio([lambda: None, lambda: None])
        self.assertIsNone(outcome)
        self.assertEqual(state["phase"], "pending")
        self.assertIn("waiting for", statusline.render_line(state, time.time()))


class Finish(unittest.TestCase):
    def test_done_summary(self):
        r = Run()
        r.line(T0 + 10, "PROGRESS 500/1000")
        r.line(T0 + 20, "PROGRESS 400/1000")
        r.s["cost"] = 1.234
        e = tracker.finish(r.s, "done", T0 + 3600)
        self.assertEqual(e["kind"], "done")
        self.assertIn("done in 1h00m", e["msg"])
        self.assertIn("1 setback(s)", e["msg"])
        self.assertIn("$1.23", e["msg"])


if __name__ == "__main__":
    unittest.main()
