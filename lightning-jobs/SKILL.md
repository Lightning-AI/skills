---
name: lightning-jobs
description: Launch and manage batch jobs on Lightning AI - run commands on cloud CPUs/GPUs from a Docker image or a Studio snapshot, monitor status, fetch logs, SSH into a running job or multi-machine worker, collect artifacts, and run multi-machine (distributed) training. Use when the user wants to run training, data processing, or any batch workload on lightning.ai, or asks to SSH into a job / MMT.
---

# Lightning AI Jobs

A Job runs a command on a dedicated cloud machine and terminates when done. Two flavors: **image jobs** (run inside any Docker image) and **studio jobs** (run inside a snapshot of an existing Studio's environment). Multi-machine distributed jobs are the same `Job` with `num_machines=N` (the older `mmt` CLI group and `MMT` class are deprecated).

## Setup & auth

```bash
# Use the Lightning AI CLI from the current env; install or upgrade it there if it's missing or older than 2026.9.18
v=$(lightning --version 2>/dev/null | sed -n 's/^Lightning CLI version //p')
[ -n "$v" ] && [ "$(printf '%s\n' 2026.9.18 "$v" | sort -V | head -1)" = 2026.9.18 ] \
  || uv pip install -U lightning-sdk || python3 -m pip install -U lightning-sdk
lightning --version   # must print "Lightning CLI version …"; if not, see the table below
lightning login                     # browser flow; or headless:
export LIGHTNING_API_KEY=... LIGHTNING_USER_ID=...   # USER_ID optional: with it Basic auth, without it the key is a Bearer token
```

This uses, and if needed installs into, the environment the agent runs in (the user's project
venv, a conda env, …). Then call plain `lightning …` everywhere. If setup doesn't go cleanly:

| Symptom | Fix |
|---|---|
| Both installs fail: no active env, or pip refuses with `externally-managed-environment` | Install it on its own instead: `uv tool install lightning-sdk` (or `pipx install lightning-sdk`), then call `"$(uv tool dir --bin)/lightning"` if that dir isn't on `PATH` |
| `lightning --version` still prints no `Lightning CLI version` line after installing | `command -v lightning` shows which one runs. Another tool owns the name (PyTorch Lightning also installs a `lightning` command), or the env you installed into isn't on `PATH`: activate it (`source .venv/bin/activate`) or call its `bin/lightning` by full path |
| `No such command`, `No such option` or `unexpected extra argument` | The CLI is older than this skill expects: `uv pip install -U lightning-sdk` (or `pip install -U lightning-sdk`) in the env `command -v lightning` points into; `uv tool upgrade lightning-sdk` for a uv tool install |
| The upgrade fails on a version conflict with the project's own pins | Don't fight the pins: install it outside the project with `uv tool install lightning-sdk` and call `"$(uv tool dir --bin)/lightning"` |
| Installing is blocked (read-only env or home, agent sandbox) | Skip it and run each command as `UV_CACHE_DIR="${TMPDIR:-/tmp}/uv" UV_TOOL_DIR="${TMPDIR:-/tmp}/uv-tools" uvx lightning-sdk …` |
| Every call fails on SSL/certificates, even `lightning --version` (`Could not find a suitable TLS CA certificate bundle`), only inside an agent sandbox | A `**/*.pem` read-deny rule is hiding certifi's public CA bundle (`site-packages/certifi/cacert.pem`). Allow that one path; don't disable the sandbox |
| Every call fails with `NameResolutionError`, only inside an agent sandbox | If `lightning api` fails too, ask the user to allow `lightning.ai` and `*.lightning.ai` (Claude Code: `/sandbox`). If only `studio`/`job` commands or the SDK fail, they bypass the sandbox's proxy and no allowlist helps: ask to run just those outside the sandbox. Background pollers fail the same way, silently |

The install above also makes `lightning_sdk` importable in that env, so Python snippets run with
its `python`. If the CLI came from a uv tool or `uvx` fallback instead, run one-off scripts with
`uv run --with lightning-sdk python script.py`. For code that stays in the user's project, ask,
then declare `lightning-sdk` as a dependency with the project's own tool (`uv add`, `poetry add`).

### First run: no CLI or account yet

The user may never have heard of Lightning and may not have asked for it by name. Say in one line
that the job would run on Lightning AI and why (e.g. "no GPU here"), then set up for them instead
of stopping at the first error. Run the setup block above (it installs the CLI if it's missing),
then check for a signed-in user:

```bash
lightning auth whoami || echo "not signed in"
```

If nobody is signed in, choose the path that fits the machine:

- **Has a browser (a laptop):** run `lightning login`. It opens lightning.ai, where a new user can
  create a free account. The command returns once the browser sign-in completes, so give it a few
  minutes and tell the user a tab is opening.
- **No browser (Claude Code cloud session, CI, remote box):** ask the user to sign up at
  lightning.ai, then copy `LIGHTNING_USER_ID` and `LIGHTNING_API_KEY` from their avatar →
  **Global Settings → Keys → Login via CLI**. They go in as environment variables where the agent
  runs. **Never ask for the key in chat.** In a Claude Code cloud session, both values belong in
  the cloud environment's **Environment variables**. The same dialog's **Network access** must be
  **Custom**, allowing `lightning.ai` and `*.lightning.ai` with the default list kept. Both
  changes take effect in a **new** session only.

Then continue with *Resolving org and teamspace*. A new account has one teamspace, so there's
nothing to ask.

## Resolving org and teamspace (do this first)

Jobs live in a teamspace owned by an organization or a user. **Never guess.** Use an explicit `--teamspace owner/teamspace` flag (Python: `teamspace="owner/teamspace"`; the separate `org=`/`user=` arguments are deprecated), or env vars `LIGHTNING_ORG` / `LIGHTNING_TEAMSPACE`, or the config default (`lightning config get teamspace`). If none is set, list the options and **ask the user which org/teamspace to use**:

```bash
lightning api /v1/memberships | jq -r '.memberships[] | [.ownerType, .name, .projectId] | @tsv'
```

Persist the choice: `lightning config set teamspace <owner>/<teamspace>`.

**From a scoped API key** (an agent, no user to ask): `/v1/memberships` gives the teamspace name and the owner *id*, but `--teamspace` needs the owner *slug* — resolve it via `/v1/orgs` (needs `jq`). **Select the `organization` entry rather than `.memberships[0]`**: the same teamspace is commonly listed twice, once with `ownerType: organization` and once with `ownerType: user` (identical `projectId` and `name`), so index 0 is a coin flip and the `user` row's `ownerId` will not resolve against `/v1/orgs`.

```bash
M=$(lightning api /v1/memberships)
ROW=$(echo "$M" | jq -c '[.memberships[] | select(.ownerType=="organization")][0] // .memberships[0]')
TS=$(echo "$ROW" | jq -r .name)                                                        # teamspace
OWNER=$(lightning api "/v1/orgs/$(echo "$ROW" | jq -r .ownerId)" | jq -r .name)         # owner (org) slug
lightning config set teamspace "$OWNER/$TS"     # every command now defaults here; or pass --teamspace "$OWNER/$TS"
```

## CLI reference

Subcommands: `run`, `list`, `inspect`, `logs`, `ssh`, `stop`, `delete`, `rename`, `tag`, `untag`. `inspect`, `logs` and `ssh` take `--rank N` to pick one machine of a multi-machine job. The `mmt` group is deprecated: each `mmt` subcommand just points you to `lightning job <cmd>`. `logs` reads a job's logs from the CLI — a snapshot by default, or `--follow` to stream a running job. **There is no `status` subcommand** — `status` comes from `inspect` (JSON).

```bash
# image job
lightning job run --name my-job --teamspace owner/teamspace \
  --image python:3.11-slim --machine CPU \
  --command "python -c 'print(\"hello\")'" \
  [-e KEY=VALUE ...] [--interruptible] [--cloud PROVIDER]

# studio job (snapshot of an existing studio's environment; command required)
lightning job run --name my-job --teamspace owner/teamspace \
  --studio my-studio --machine A100 --command "python train.py"

# omit both --studio and --image while running inside a Studio to target THAT Studio
# (only if its teamspace matches the resolved --teamspace) — no lookup needed:
lightning job run --name my-job --teamspace owner/teamspace --machine A100 --command "python train.py"

# private registry image
lightning job run ... --image-credentials <secret-name> [--cloud-account-auth]  # --cloud-account-auth for ECR-type registries

# monitor / manage
lightning job list --teamspace owner/teamspace [--all] [--sort-by status] [--json]   # includes multi-machine jobs
lightning job inspect my-job --teamspace owner/teamspace      # JSON incl. status, machine, cost
lightning job stop my-job --teamspace owner/teamspace
lightning job delete my-job --teamspace owner/teamspace -y   # -y/--yes: required non-interactively

# logs — snapshot by default; --follow streams a running job until it finishes or Ctrl-C
lightning job logs my-job --teamspace owner/teamspace [--follow] [--tail 100] [--timestamps]
lightning job logs my-job --teamspace owner/teamspace --query error --severity error   # filter server-side
lightning job logs my-job --teamspace owner/teamspace --since 2h --until 30m           # window: duration (30s/2h/3d/1w) or RFC3339

# ssh into a running job (fails unless status is Running)
lightning job ssh my-job --teamspace owner/teamspace

# multi-machine training: same command plus --num-machines
lightning job run --name my-mmt --teamspace owner/teamspace \
  --image pytorch/pytorch:2.4.1-cuda12.1-cudnn9-runtime --num-machines 2 --machine L4 \
  --command "python -m torch.distributed.run --nproc_per_node=1 train.py"

# multi-machine logs: every rank merged and labelled; --rank N reads one machine
lightning job logs my-mmt --teamspace owner/teamspace [--follow] [--tail 50] [--rank 1]

lightning job ssh my-mmt --teamspace owner/teamspace              # rank 0 by default
lightning job ssh my-mmt --rank 1 --teamspace owner/teamspace    # pick a worker
```

## Python SDK

```python
from lightning_sdk import Job, Machine, Status, Studio, Teamspace

ts = Teamspace("my-org/my-teamspace")   # owner/teamspace; org=/user= are deprecated

job = Job.run(
    name="my-job",                    # on a clash the platform renames it and the SDK warns
    machine=Machine.CPU,              # or "A100", Machine.from_str("L4"), ...
    image="python:3.11-slim",         # OR studio=<Studio|name> — mutually exclusive
    command="python train.py",        # required for studio jobs, optional for image jobs
    teamspace=ts,
    env={"RUN_MODE": "prod"},
    interruptible=False,              # True = spot: cheaper, can be preempted
    max_run_attempts=2,               # optional retries; None = backend default
)
print(job.link)                       # web UI URL

job.wait(interval=10, timeout=3600, stop_on_timeout=True)   # blocks until terminal
print(job.status)                     # Status.Pending / Running / Stopping / Stopped / Completed / Failed
if job.status == Status.Failed:
    print(job.logs)                   # snapshot of logs so far (also: `lightning job logs <name>`)
print(job.total_cost)                 # USD

job.stop(); job.delete()

# distributed job — same Job.run plus num_machines; per-worker access via .machines
mmt = Job.run(name="my-mmt", num_machines=2, machine=Machine.L4, image="...", command="...", teamspace=ts)
mmt.wait()
for worker in mmt.machines:           # rank-ordered; each worker is a Job
    print(worker.name, worker.status) # per-node logs via worker.logs; mmt.logs (or `lightning job logs`) merges all ranks
```

Fetch an existing job: `Job("my-job", teamspace=ts)` (raises `ValueError` if it doesn't exist).

Leaving both `studio=` and `image=` unset targets the Studio you're currently running inside (via `LIGHTNING_CLOUD_SPACE_ID`), if its teamspace matches — see Gotchas.

### Image vs studio jobs

| | Studio job | Image job |
|---|---|---|
| `command` | required | optional (falls back to image entrypoint) |
| `entrypoint`, `image_credentials`, `cloud_account_auth` | forbidden | allowed |
| artifacts | write outputs to the job's **home** (`$LIGHTNING_ARTIFACTS_DIR`); collected and read back under `/teamspace/jobs/<name>/artifacts` — see below | none by default — route via `path_mappings={"<container-path>": "<connection>:<path>"}` |
| scratch disks | `scratch_disks={"data": 100}` (GiB, under `/teamspace/scratch/`) | forbidden |

### Outputs & artifacts (studio jobs)

A studio job runs with its **home at the current-Studio home mount**,
`/teamspace/studios/this_studio` — the canonical path (the same regardless of which
Studio you launched from), and exactly where `$LIGHTNING_ARTIFACTS_DIR` points. To keep
any output, **write it under home** (use the env var, don't hardcode) — every file the
job **creates or modifies under home** during the run is captured as a job artifact.

```python
import os, joblib
out = os.environ["LIGHTNING_ARTIFACTS_DIR"]     # == the source Studio's home path
joblib.dump(model, f"{out}/model.joblib")        # a new/changed file under home -> artifact
```

Read the results back **from the source Studio**, under
`/teamspace/jobs/<job-name>/artifacts/`. Two things that trip agents up:

- **`/teamspace/jobs/<name>/artifacts` is read-only** — it is where you *read* a
  finished job's artifacts from the Studio, **not** a path to write to during the run.
  Writing there from inside the job fails with `OSError: [Errno 30] Read-only file
  system`. Write to home / `$LIGHTNING_ARTIFACTS_DIR` instead.
- **A job cannot mutate the live Studio filesystem.** Your outputs do **not** reappear
  in the Studio's home after the run — they surface only under
  `/teamspace/jobs/<name>/artifacts` (read-only) from the source Studio once the job
  is terminal.

**Fetch artifacts from anywhere** — the capture also surfaces in the teamspace
Drive under `jobs/<job-name>/`, so reading it does not require a Studio. In Python,
`job.artifacts_uri` gives that `lit://` address, and `job.list_artifacts(path, recursive=True)` /
`job.download_artifacts(target_dir, path)` read it (per machine on a multi-machine job). From the shell:

```bash
lightning cp lit://<owner>/<teamspace>/jobs/<job-name>/model.joblib ./model.joblib   # one file
lightning cp -r lit://<owner>/<teamspace>/jobs/<job-name>/outputs/ ./outputs         # a folder
```

The capture can hold much more than the files you wrote — up to the job's whole
home, dotfiles and caches included — so list and copy the specific subfolder, never the
`jobs/<job-name>/` root: `lightning ls lit://<owner>/<teamspace>/jobs/<job-name>/outputs`
returned in 2 s where the same `ls` on the job root was still hanging after 60 s. In Python,
`job.list_artifacts("outputs", recursive=True)` is instant, and
`job.download_artifacts("outputs", "outputs")` writes the *contents* of the job's `outputs/`
into the local `./outputs`. Deleting the job deletes this tree with it.

Image (docker) jobs have no home-artifact collection, and **no Lightning credentials inside**:
the container gets `LIGHTNING_CLOUD_URL`, the job and project ids and `LIGHTNING_USERNAME`, but no
`LIGHTNING_API_KEY`, so `lightning cp` to the Drive fails from there. Print small results to the
log (read them back with `lightning job logs`), mount an output location with `path_mappings`
(see the table above), or use a studio job when files must come back.

### Machines

`CPU_SMALL`, `CPU`, `CPU_X_2/4/8/16`, `DATA_PREP(_MAX/_ULTRA)`, `T4_SMALL`, `T4(_X_2/4/8)`, `L4(_X_2/4/8)`, `L40S(_X_2/4/8)`, `RTXP_6000(_X_2/4/8)`, `A100(_X_2/4/8)`, `H100(_X_2/4/8)`, `H200(_X_2/4/8)`, `B200(_X_8)`. Multi-GPU `_X_N` variants bill N GPUs; multi-machine jobs bill per machine × `num_machines`.

The names above are current as of writing and SKUs do get added, so treat the list as a starting
point, not a closed set. `lightning machine list` prints the names your installed CLI accepts, but
it is an offline list and says nothing about what a teamspace can launch.

**Pick the machine from live data, per cloud account.** A teamspace can launch on several cloud
accounts, and the same GPU differs between them in name, price and wait. List what each account
can start right now, fastest first:

```python
from lightning_sdk import Teamspace
ts = Teamspace("my-org/my-teamspace")
FAMILY = "H100"   # the GPU family the workload needs
# Accounts go by id: ts.cloud_accounts holds display names, and list_machines() returns [] for a name
rows = [(m.wait_time, m.cost, a.cluster_id, m) for a in ts.cloud_account_objs
        for m in ts.list_machines(cloud_account=a.cluster_id) if m.family == FAMILY]
for wait, cost, acct, m in sorted(rows, key=lambda r: (r[0] is None, r[0] or 0, r[1] or 0)):
    print(f"{acct:34} {m.name:28} x{m.accelerator_count}  ${cost}/h  wait ~{wait}s")
```

`cost` is USD per hour and `wait_time` the expected seconds until a machine is free. Launch with
the `Machine` object from the row you pick, on that row's account (`Job.run(machine=m,
cloud=acct, …)`, or a Studio created with `cloud=acct`): a machine name from one account fails on
another. `list_machines()` with no argument merges several accounts without saying which row
belongs to which, so it can't tell you where to launch. Show the user the top rows with price and
wait before launching on a GPU. For quotes before login, `GET
/v1/core/accelerators?cloudProvider=<PROVIDER>` needs no auth (the `lightning-cost-estimation`
skill has the provider values and the costing recipes). Don't invent a catalog endpoint:
`/v1/accelerators`, `/v1/accelerator-catalog`, `/v1/pricing` and `/v1/compute/accelerators` all
return `code: 5`.

<!-- TODO: remove this workaround once the backend rejects a GPU request it can't fill instead of
starting a CPU machine (Task Board: "CLI silently downgrades to CPU when a GPU SKU isn't available"). -->
**Pick the cloud account for a GPU job before launching.** The default cloud doesn't sell every
GPU at every count (1× H200 isn't on AWS), and Studios asked for one have silently come up on CPU
(see `lightning-studios`), so don't count on a job failing loudly. Take the account from the
listing above. From the CLI, pass it as `--cloud` with the GPU name, and put a hardware check first
in the command:

```bash
lightning job run --name my-job --teamspace owner/teamspace --machine H200 --cloud lightning-baremetal \
  --image python:3.12-slim --command "nvidia-smi --query-gpu=name,memory.total --format=csv,noheader"
```

### The first minute after launch

Most job failures happen in the first seconds: a wrong machine, a missing package, a renamed
argument. Once the job is `Running`, read its log before settling in to wait, and stop it at
the first traceback instead of letting it sit:

```bash
lightning job logs my-job --teamspace owner/teamspace --tail 40   # confirm the GPU line and the first steps
lightning job stop my-job --teamspace owner/teamspace             # only if the log shows a crash or the wrong machine
```

Put a hardware check first in the job's own command, e.g.
`nvidia-smi --query-gpu=name,memory.total --format=csv,noheader && python train.py`. The first log
line then tells you what you actually got.

## Example workflows

Prompts this skill handles: *"run this script on an A100 as a batch job"*, *"launch my docker image on lightning"*, *"why did my job fail — show me the logs"*, *"SSH into my running job"*, *"SSH into rank 1 of my multi-machine job"*, *"run a 2-node distributed training"*.

**Run a containerized script and report the outcome:**

```bash
lightning job run --name fmt-check-$(date +%s) --teamspace my-org/my-teamspace \
  --image python:3.11-slim --machine CPU \
  --command "pip install ruff && ruff check ." 
lightning job list --teamspace my-org/my-teamspace --sort-by status
```

**Launch, wait, and fetch logs (the reliable agent loop):**

```python
from lightning_sdk import Job, Machine, Status
job = Job.run(name="train-run-42", machine=Machine.L4, image="pytorch/pytorch:2.4.1-cuda12.1-cudnn9-runtime",
              command="python -c 'import torch; print(torch.cuda.is_available())'",
              teamspace="my-org/my-teamspace", interruptible=True)
job.wait(interval=15, timeout=2*3600, stop_on_timeout=True)
print(job.status, f"${job.total_cost:.4f}")
print(job.logs)          # full logs (job is terminal); stream a running job with: lightning job logs train-run-42 --follow
```

**Run a local script on a GPU and bring its output files back** (a studio job, since image jobs
can't return files on their own). Pick `ACCOUNT` and `MACHINE` from the live listing in
*Machines*, and confirm the price with the user first:

```python
import threading, time
from lightning_sdk import Job, Studio

studio = Studio("run-train", teamspace="my-org/my-teamspace", cloud=ACCOUNT, create_ok=True)
threading.Thread(target=studio.start, daemon=True).start()   # CPU is enough: it only holds the files
# Running is not ready, and start() can block past readiness (lightning-studios skill, Gotchas)
while not (str(studio.status).endswith("Running")
           and "setting things up" not in studio.run_with_exit_code("python -c pass")[0]):
    time.sleep(10)
studio.upload_file("train.py", "train.py")       # lands in the Studio home, the job's working dir
job = Job.run(name=f"train-{int(time.time())}", machine=MACHINE, studio=studio,  # no teamspace=
              command="env -u UV_LIGHTNING_VIRTUALENV_ROOT uv run train.py",       # see Gotchas
              max_run_attempts=1)
job.wait(interval=15, timeout=2 * 3600, stop_on_timeout=True)
print(job.status, job.total_cost)
job.download_artifacts("outputs", "outputs")     # whatever train.py wrote under ./outputs
studio.stop()
```

The job runs on a snapshot of the Studio, so the Studio can be stopped once `job.status` is
`Running`. The first job from a Studio waits in `Pending` while that snapshot is taken (5 min 14 s
for a fresh CPU Studio when measured); later jobs reuse it and started in about a minute. For a
single run where the wait matters, starting a Studio directly on the GPU and running the script
there is quicker (see the `lightning-studios` skill).

Check status and read logs of an existing job straight from the shell — works while it runs or after:

```bash
lightning job inspect train-run-42 --teamspace my-org/my-teamspace          # status / machine / cost as JSON
lightning job logs    train-run-42 --teamspace my-org/my-teamspace --tail 50   # last 50 lines; add --follow to stream
```

**Parameter sweep — several jobs from one loop:**

```python
for lr in ["1e-3", "3e-4", "1e-4"]:
    Job.run(name=f"sweep-lr-{lr}", machine=Machine.T4, studio="exp-1",
            command=f"python train.py --lr {lr}", env={"WANDB_RUN": f"lr-{lr}"},
            teamspace="my-org/my-teamspace", interruptible=True)
# each writes outputs to home ($LIGHTNING_ARTIFACTS_DIR); read them from the Studio under /teamspace/jobs/<name>/artifacts
```

**Distributed (2×L4, one process per node):**

```bash
lightning job run --name ddp-test --teamspace my-org/my-teamspace \
  --image pytorch/pytorch:2.4.1-cuda12.1-cudnn9-runtime --num-machines 2 --machine L4 \
  --command "python -m torch.distributed.run --nproc_per_node=1 train.py"
```

**SSH into a running job / multi-machine worker** (for a human user; agents should prefer `inspect` and
the Python SDK since `ssh` opens an interactive shell):

```bash
lightning job ssh train-run-42 --teamspace my-org/my-teamspace          # single job, must be Running
lightning job ssh ddp-test --teamspace my-org/my-teamspace              # multi-machine: rank 0
lightning job ssh ddp-test --rank 1 --teamspace my-org/my-teamspace     # multi-machine: rank 1
```

`--rank` only applies to multi-machine jobs; on a single job it errors.

## Raw API fallback

For what the CLI doesn't wrap (chiefly exact-cost JSON and other raw resource fields —
logs are now a first-class CLI command, see above). **Call these as plain GETs — do NOT
add `-F limit=…` without `-X GET`: `lightning api` sends any request with `-f`/`-F` fields and no
`-X` as a POST (the create call), and the server rejects it with `400 "spec is required"` (see
Gotchas). Slice client-side with `-q`.**

```bash
PROJECT_ID=$(lightning api /v1/memberships | jq -r '.memberships[] | select(.name=="<teamspace>") | .projectId' | head -1)

# list jobs (id + name) — plain GET, no -F
lightning api "/v1/projects/${PROJECT_ID}/jobs" -q '.jobs[] | [.id, .name] | @tsv'

# inspect one job as JSON (status, machine, cost, timestamps) — by JOB ID (job_...), not name
lightning api "/v1/projects/${PROJECT_ID}/jobs/${JOB_ID}"

# list multi-machine jobs — also a plain GET, no -F
lightning api "/v1/projects/${PROJECT_ID}/multi-machine-jobs" -q '.multiMachineJobs[].name'
```

`JOB_ID` is the `job_...` id from the list call (these endpoints 404 on the human name).
To find one job by name, **filter the list**. The `/jobs/find` route returned `501 Not
Implemented` when last tested live. The SDK's `Job("<name>")` calls it as
`GET .../jobs/find?name=<name>`, which may be what works, but that form is untested here. For everyday use prefer the CLI: `lightning job list --json`, `lightning job
inspect <name>`, `lightning job logs <name>`.

## Gotchas

- Jobs bill machine time while allocated; confirm with the user before launching on expensive GPUs (A100/H100/H200/B200) or high `num_machines`, and prefer `wait(..., stop_on_timeout=True)` so runaway jobs get stopped.
- **Prefer a job to a Studio for one-shot runs** (train, eval, batch). A job stops billing when its command exits, crash included. A crashed run on a Studio leaves the GPU billing idle until someone notices. The exception is a short run on a tight deadline: the first job from a Studio waits about 5 minutes for its snapshot (see *Example workflows*), so a Studio started on the GPU, with a deadline that stops it, finishes sooner.
- **Treat a silent monitor as a failure.** A poller with no deadline, one that only matches progress lines, or one that can't reach lightning.ai (a sandboxed background command) stays quiet through a crash. Poll `job.status` with a deadline, react to `Failed`/`Stopped` as well as `Completed`, and check the poller prints its first line.
- **`lightning job delete` prompts for confirmation — pass `-y`/`--yes` non-interactively.** Without it the command reads the prompt from a closed stdin, prints `Are you sure you want to delete? [y/N]: Aborted.` and exits **without deleting**. The job stays listed and keeps costing money, and the failure is easy to miss in a log.
- **`--query` and `--severity` can't filter every *finished* job.** Where a job's logs are stored decides this: if its lines aren't in the newer log storage, a finished job falls back to its saved log file, which can't be filtered server-side. The CLI and SDK then raise `This job's logs are only available as a saved file ... filter locally` rather than returning nothing, so fetch unfiltered and `grep` locally. `--timestamps` works on both paths. While a job is still `Running` the filters are applied server-side and work.
- **`job inspect` prints plain JSON** (`--json` is accepted too), so `lightning job inspect <name> | jq -r .status` is a fine polling source. For many jobs at once, `lightning job list --json` includes status, `started_at`/`stopped_at`, `total_cost` and `num_machines`.
- **`job inspect` has no exit code or failure message** — it returns `command`, `image`, `machine`, `name`, `status`, `studio`, `tags`, `teamspace` and `total_cost`. Start/stop times are on `job list --json` and `Job.started_at`/`Job.stopped_at`. To find out *why* a job failed, read the raw record's `message` field: `lightning api "/v1/projects/$PID/jobs" -q '.jobs[] | select(.name=="<name>") | .message'`.
- Logs read while a job runs, not just after: `lightning job logs <name> --follow` streams live, and `print(job.logs)` returns a snapshot of what's available so far. While a job is still `Pending` (no machine scheduled yet) there may be nothing to show.
- `lightning job ssh` only works while the target is **Running** — Pending/Completed/Failed/Stopped raise a clean error. For multi-machine jobs pass `--rank N` (defaults to 0).
- On the raw `lightning api` GET list endpoints (`/jobs`, `/multi-machine-jobs`), do **not** pass `-F limit=…` without `-X GET` — fields with no `-X` make `lightning api` send a POST, and the server 400s with `"spec is required"` (jobs) / `"name is required"` (multi-machine). Call them bare and slice with `-q`. The per-job endpoints take the `job_...` id, not the name; to look one up by name, filter the list (`/jobs/find` returned `501` when last tested).
- `image` and `studio` are mutually exclusive; a studio job's studio must be in the same teamspace and cloud account.
- **In Python, don't pass `teamspace=` next to a `Studio` object.** `Job.run(studio=s, teamspace="owner/ts")` raises `ValueError: Studio teamspace does not match provided teamspace` even when it is the Studio's own teamspace. Leave `teamspace` out: the job takes it from the Studio.
- **`uv run` fails inside a studio job** with `error: failed to symlink file from /system/conda/miniconda3/uv/cache/… to /system/conda/miniconda3/uv/venvs/…: No such file or directory`. The Studio's `UV_LIGHTNING_VIRTUALENV_ROOT` points at a folder that doesn't exist there (the same happens inside a fresh Studio). Run `env -u UV_LIGHTNING_VIRTUALENV_ROOT uv run …` (unsetting every `UV_*` variable works too).
- **Pass the `Machine` from `list_machines(cloud_account=…)`, not a name you know from elsewhere.** The same GPU has different names on different accounts (`lit-h200-1` on one, `lit-h200-141gb-1` on another), and a name the job's account doesn't know fails the launch. `Teamspace.cloud_accounts` gives display names such as `Lightning Cloud`; `list_machines()` returns an empty list for those and `Studio(cloud=…)` fails with `clusterID Lightning Cloud is invalid`, so use `Teamspace.cloud_account_objs[i].cluster_id`.
- **A job goes `Running` → `Pending` → `Completed` (or `Failed`).** The second `Pending`, about 30 s when measured, is the platform saving the job's outputs after the command exits, not a retry. Don't stop the job then; wait with `job.wait()`, which returns only on a terminal state.
- **`job.logs` is not a string.** `print(job.logs)` works, but slicing it (`job.logs[-2000:]`) raises `TypeError: '_Logs' object is not subscriptable`. Use `lightning job logs <name> --tail N` for the end of a log.
- Omitting **both** `--studio` and `--image` (Python: leaving both `studio=` and `image=` unset) does not error — it defaults to the Studio you're currently running inside, resolved via the `LIGHTNING_CLOUD_SPACE_ID` env var, as long as that Studio's teamspace matches the resolved `--teamspace`. Useful for "run this script from my current Studio" without looking up the Studio's name first. If you're not running inside a Studio (or the teamspace doesn't match), omitting both raises an error asking for one explicitly.
- Studio-job outputs go to **home** (`$LIGHTNING_ARTIFACTS_DIR`), not to `/teamspace/jobs/<name>/artifacts` — that path is **read-only** (writing to it fails `OSError: [Errno 30] Read-only file system`) and is only how you *read* artifacts back from the source Studio. Jobs can't write into the live Studio filesystem. See *Outputs & artifacts*.
- Job names are unique per teamspace: if the name is taken, the platform creates the job under a new name and the SDK warns `the job was created as '<new>' instead` — read `job.name` back rather than assuming. Omitted `--name` auto-generates one.
- `--machine` is **case-sensitive** (`--machine a100` fails with `Invalid value for '--machine'`); use the exact names above. A100_40GB/A100_80GB variants are SDK-only (hidden from CLI).
- `job.stop()` blocks (polls every 1s) until the job reaches a terminal state.
- `--org`/`--user` on `job run` are deprecated (the CLI prints a deprecation warning and will remove them) in favour of the combined `--teamspace owner/teamspace` form, which works headlessly with env-var auth. They still work today, so an existing command using them doesn't need rewriting to run.
- Image jobs can sit in `Pending`/`creating` for a long time (tens of minutes on busy shared pools) before a machine is scheduled — pending time is not billed, but don't treat a slow start as failure. The machine listing's `wait_time` predicts this: when measured, one account's H200 showed ~2 s and another's ~20 min, and a job on the latter sat in `Pending` for over 8 minutes. Always use `job.wait(timeout=..., stop_on_timeout=True)` or monitor `job.status` with your own deadline, and `job.stop()`+`job.delete()` if you give up.
