# Live progress, ETA and setbacks: details

The full procedure behind [SKILL.md](../SKILL.md#live-progress-eta-and-setbacks)'s short one.
`<SKILL_DIR>` is the `lightning-jobs` folder: the base directory Claude Code gave when it
loaded the skill, where `progress.py` sits.

For a job that runs long enough to be worth watching, show the user how far along it is, when it
should finish, and what a failure cost. The platform reports only a job's status, never how far
along it is, so the job has to print its own progress. `progress.py`, in `<SKILL_DIR>`, turns
those lines into a status-line bar and chat events:

```
job ── PROGRESS 450/1000 ──► progress.py watch (background, no tokens)
                               ├─► ~/.local/state/lightning-progress/state/<run>.json ─► status line (terminal)
                               └─► ~/.local/state/lightning-progress/events.jsonl ────► Monitor (terminal + desktop)
```

**1. Make the job print progress.** When you write or edit the training script, print
`PROGRESS 0/<total>` as soon as the total is known, then a line on every step (every few steps
if steps take well under a second). A stage has no bar until its first line arrives, so
reporting every N slow steps can leave it empty for minutes while models load and kernels
compile. On a multi-machine job print it from rank 0 only, since ranks' logs merge:

```python
print(f"PROGRESS {step}/{total_steps}", flush=True)   # optional: f"... attempt={n}" for in-script retries
```

- **Count in the unit that ends the run.** When training stops on a time budget rather than at a
  step count, report seconds used out of the budget (`PROGRESS {int(elapsed)}/{budget_s}`).
  Otherwise the ETA follows the step count and the bar jumps from partway to done. If the total
  can only be estimated, printing a refined total on later lines is fine; only a drop in the
  step counts as a setback.
- **Name the stages** when the job does more than train, so the bar says what is happening and
  a quiet stage isn't reported as a stall:

  ```bash
  echo "PROGRESS_PHASE setup 1/3"; pip install ...
  echo "PROGRESS_PHASE train 2/3"; python train.py      # its PROGRESS lines fill this stage's bar
  echo "PROGRESS_PHASE eval 3/3";  python eval.py
  ```

  Each stage keeps its own bar, and the final event lists how long each stage took. The optional
  `i/n` gives the run a whole-job bar (below). A stage that prints no `PROGRESS` lines shows
  `no progress reported yet` and its elapsed time; evals often print none, since vLLM and lm-eval
  draw no bars without a terminal. After a relaunch, only the stage the last attempt broke in is compared with its old
  progress, so resuming training from a checkpoint shows up as a setback, while stages that never
  broke simply start again.

Without a `PROGRESS` line, two fallbacks are read, in this order: tqdm bars, then the script's
own step counter (`step 60/200`, `iter 3/50`, e.g. `[train] step 60/200 loss 0.02`). Once a
better kind shows up in the log, the lesser kinds are ignored. Both are less reliable than
`PROGRESS`. PyTorch Lightning's per-epoch bars only give progress within the epoch, validation
bars and counters are ignored, and a counter says nothing of a time budget that ends the run
early. A bar or counter that starts over within one attempt (lm-eval draws one per few-shot
setting) is read as a new bar, not a setback.

**2. Start the poller** as a background Bash command. Use `<SKILL_DIR>`, not a guess at a plugin
cache, which may hold an older version:

```bash
python3 <SKILL_DIR>/progress.py watch train-run-42 --teamspace my-org/my-teamspace
```

- **Any `python3` works.** If that Python can't import `lightning_sdk` (the usual case with a
  `uv tool` or `pipx` install of the CLI), `watch` re-runs itself with the Python named on the
  `lightning` script's first line. If that fails too, it says so; run it with
  `uv run --with lightning-sdk python …` instead.
- **Run `watch` outside the agent sandbox.** The poller is the only part that talks to Lightning,
  and inside Claude Code's sandbox the SDK's requests fail even with `lightning.ai` allowed.
  `watch` detects this and exits with that message. Ask the user to approve this one command
  unsandboxed. The Monitor in step 3 and the status line only read the state folder, so they work
  inside the sandbox even though it can't write there.

`watch` first prints `run <RUN>: watching <job>`. `<RUN>` is the job name (or the log file's
stem), unless another workload already uses that name, such as a job of the same name in another
teamspace; then it gets a qualifier, e.g. `train-run-42@my-org-other-ts`. The teamspace is the
one the job resolved to, so watching without `--teamspace` and then changing the configured
default can't mix up two jobs. Use the printed name
for `events --run` and `abandon`. A finished run under that name starts over; only an explicit
`--run` continues one (step 5).

`watch` waits through `Pending`, follows the logs while the job runs, and exits once the run is
final. State goes to `~/.local/state/lightning-progress/`, so it doesn't matter which directory
`watch`, the Monitor or the status line runs in. Keep that default. If you do move it, with
`--dir PATH` before the command or `LIGHTNING_PROGRESS_DIR`, use the same folder for `watch`,
`events` and `statusline --config`. The status-line command points at a launcher inside that
folder, since the status line doesn't inherit the session's environment. Each session's status
line shows only the runs that session's `watch` started, so other sessions stay clear.

**Work running in a Studio** has no job log stream, so point `watch` at the log file instead.
Start the process so its last line records the exit code, then watch that file. Paths are
relative to the Studio's home, `/teamspace/studios/this_studio`:

```bash
# inside the Studio (e.g. via studio.run_and_detach): the echo marks success or failure
nohup sh -c 'python train.py; echo PROGRESS_EXIT $?' > work/train.log 2>&1 &
```
```bash
# locally, in the background
python3 <SKILL_DIR>/progress.py watch --studio my-studio --log work/train.log --teamspace my-org/my-teamspace
```

It reads new bytes every 10 s over `Studio.run`. Without the `PROGRESS_EXIT` line it notices
the process has ended once nothing holds the log open, and calls it failed if the last lines
show an error. Relaunching into the same log, whether overwritten or appended, is the run's
next attempt, also when the new log outgrows the old one between two reads. It never creates a Studio, and a Studio that stops or switches machines counts as
downtime.

**3. Watch events in the session** with a Monitor running
`python3 <SKILL_DIR>/progress.py events --run train-run-42` at the maximum timeout, re-armed on
expiry. Start it after `watch` has printed its `run` line. Nothing is lost between Monitors: the
first one replays what the run's poller reported before it started, and a re-armed one carries on
where the last one stopped (it keeps its place in the temp folder, which the sandbox can write). It prints only what needs a reply: stalls, setbacks, failures, retries, relaunches and
the final state. A failed attempt that waits for a relaunch doesn't end it; it exits only when
the run itself ends. Report each of these to the user when it lands.
Routine progress (10% milestones, stage changes) stays in the status-line bar, so it doesn't
interrupt the session. Where there is no bar (the desktop app, IDE extensions, or the user
declined it), add `--all` to get those too, and keep the updates to a line each.

**4. Offer the status-line bar once.** It is the only live, always-visible view. If `watch`
found it already set up, skip this step; otherwise its first event (and the Monitor's) says so.
Right after starting `watch`, ask with the ask-user tool, as its own question rather than a line
inside a status update, where it is easy to miss:

- **All projects (recommend this).** One setup, and no question again in later sessions or
  other projects. Run the first command and merge what it prints into `~/.claude/settings.json`.
- **This project only.** Pass the directory Claude Code was started in (your primary working
  directory, never a scratchpad or temp folder) as `--project-dir`, and merge what it prints into
  that directory's `.claude/settings.local.json`. It stays set for later sessions in that
  project, so don't offer it as "this session only".

```bash
python3 <SKILL_DIR>/progress.py statusline --config --user                       # all projects
python3 <SKILL_DIR>/progress.py statusline --config --project-dir <PROJECT_DIR>  # this project only
```

Claude Code asks for approval before the edit, because settings files are protected; that is
expected, and no rule can pre-approve it. The block runs a small launcher in the state folder
that always starts the newest `progress.py` (`watch` keeps it current), so a plugin update
doesn't break it. It prints nothing while no run is active, and if the user already has a
status line, it runs theirs first and adds the bars below; running the setup again keeps it. The comments printed alongside say
if a project's own status line would hide a user-level one. The bar shows in the terminal
only; the desktop app and IDE extensions don't draw status lines, so there the Monitor events
(with `--all`) are the view.

```
▶ train-run-42  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░   71%  stage 3/3 · attempt 2 · ↺1 (+1m35s) · $1.41
   ✔ setup  55s
   ✔ train  5m55s
   ▸ eval   ▓▓▓░░░░░░░░░░░░░░░░░   15%  ETA 4m40s
```

The top row is the whole job: finished stages plus the current stage's own progress, out of the
stage count. Under it is one row per stage of the current attempt; earlier attempts show only as
`attempt N`. A job without stage markers gets a single row with its step bar. `▓` is done, `▒` is
ground lost to a setback (it clears once progress passes the old peak), `↺N` counts setbacks, and
`(+…)` is the time they and stalls have cost. The two most recently active runs are expanded;
other and finished runs take one row each.

**5. Keep a failed run going across relaunches.** A run is a chain of attempts, so its peak,
setback history and lost time carry over when the job name changes. When a job fails, the poller
holds the run open for 30 minutes (`--relaunch-wait`). After fixing the cause, launch the new job,
then hand it to the run:

```bash
python3 <SKILL_DIR>/progress.py watch train-run-42-a2 --run train-run-42 --note "OOM: batch 32→16"
```

If the poller is still alive, this passes the job to it and returns. If it has already exited,
this starts a new poller that continues the run's history. To give up on the run, use
`progress.py abandon train-run-42`. The first progress line of the new attempt decides what kind
of setback it was:

| New attempt's first step | Recorded as |
|---|---|
| Above 0, below the old peak | `resume` from a checkpoint; the lost ground shows as `▒` |
| About 0 | `restart` from scratch |
| A different `total` | `new-setup`; the old peak is dropped |

A step that drops inside a running job counts the same way, as does the platform retrying the job
itself (`max_run_attempts`) or requeueing it. A job that stops printing progress for more than
about 4× its usual interval is marked `stalled`, with the latest error line as the likely cause.
