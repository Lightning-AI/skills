---
name: lightning-jobs
description: Launch and manage batch jobs on Lightning AI - run commands on cloud CPUs/GPUs from a Docker image or a Studio snapshot, monitor status, fetch logs, SSH into a running job or multi-machine worker, collect artifacts, and run multi-machine (distributed) training. Use when the user wants to run training, data processing, or any batch workload on lightning.ai, or asks to SSH into a job / MMT.
---

# Lightning AI Jobs

A Job runs a command on a dedicated cloud machine and terminates when done. Two flavors: **image jobs** (run inside any Docker image) and **studio jobs** (run inside a snapshot of an existing Studio's environment). Multi-machine distributed jobs use `MMT`.

## Setup & auth

```bash
uvx lightning-sdk --version         # CLI without installing; `lightning` == `lightning-sdk`
lightning login                     # browser flow; or headless:
export LIGHTNING_USER_ID=... LIGHTNING_API_KEY=...   # both required
```

Python snippets: `uv run --with lightning-sdk python script.py`.

## Resolving org and teamspace (do this first)

Jobs live in a teamspace owned by an organization or a user. **Never guess.** Use an explicit `--teamspace owner/teamspace` flag (Python: `Teamspace(name, org=...)` or `user=...`, mutually exclusive), or env vars `LIGHTNING_ORG` / `LIGHTNING_TEAMSPACE`, or the config default (`lightning config get teamspace`). If none is set, list the options and **ask the user which org/teamspace to use**:

```bash
lightning api /v1/memberships | jq -r '.memberships[] | [.ownerType, .name, .projectId] | @tsv'
```

Persist the choice: `lightning config set teamspace <owner>/<teamspace>`.

**From a scoped API key** (an agent, no user to ask): the key has exactly one membership. `/v1/memberships` gives the teamspace name and the owner *id*, but `--teamspace` needs the owner *slug* — resolve it via `/v1/orgs` (needs `jq`):

```bash
M=$(lightning api /v1/memberships)
TS=$(echo "$M" | jq -r '.memberships[0].name')                                                  # teamspace
OWNER=$(lightning api "/v1/orgs/$(echo "$M" | jq -r '.memberships[0].ownerId')" | jq -r .name)   # owner (org) slug
lightning config set teamspace "$OWNER/$TS"     # every command now defaults here; or pass --teamspace "$OWNER/$TS"
```

## CLI reference

Subcommands: `run`, `list`, `inspect`, `logs`, `ssh`, `stop`, `delete` (same set on `mmt`, plus `--rank` on `mmt ssh`). `logs` reads a job's logs from the CLI — a snapshot by default, or `--follow` to stream a running job. **There is no `status` subcommand** — `status` comes from `inspect` (JSON).

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
lightning job list --teamspace owner/teamspace [--all] [--sort-by status]
lightning job inspect my-job --teamspace owner/teamspace      # JSON incl. status, machine, cost
lightning job stop my-job --teamspace owner/teamspace
lightning job delete my-job --teamspace owner/teamspace -y   # -y/--yes: required non-interactively

# logs — snapshot by default; --follow streams a running job until it finishes or Ctrl-C
lightning job logs my-job --teamspace owner/teamspace [--follow] [--tail 100] [--timestamps]
lightning job logs my-job --teamspace owner/teamspace --query error --severity error   # filter server-side
lightning job logs my-job --teamspace owner/teamspace --since 2h --until 30m           # window: duration (30s/2h/3d/1w) or RFC3339

# ssh into a running job (fails unless status is Running)
lightning job ssh my-job --teamspace owner/teamspace

# multi-machine training: same flags plus --num-machines
lightning mmt run --name my-mmt --teamspace owner/teamspace \
  --image pytorch/pytorch:2.4.1-cuda12.1-cudnn9-runtime --num-machines 2 --machine L4 \
  --command "python -m torch.distributed.run --nproc_per_node=1 train.py"

# multi-machine logs: every rank merged and labelled (read one machine with `lightning job logs <machine-name>`)
lightning mmt logs my-mmt --teamspace owner/teamspace [--follow] [--tail 50]

lightning mmt ssh my-mmt --teamspace owner/teamspace              # rank 0 by default
lightning mmt ssh my-mmt --rank 1 --teamspace owner/teamspace    # pick a worker
```

## Python SDK

```python
from lightning_sdk import Job, MMT, Machine, Status, Studio, Teamspace

ts = Teamspace("my-teamspace", org="my-org")   # or user="username"

job = Job.run(
    name="my-job",                    # must be unique within the teamspace
    machine=Machine.CPU,              # or "A100", Machine.from_str("L4"), ...
    image="python:3.11-slim",         # OR studio=<Studio|name> — mutually exclusive
    command="python train.py",        # required for studio jobs, optional for image jobs
    teamspace=ts,
    env={"RUN_MODE": "prod"},
    interruptible=False,              # True = spot: cheaper, can be preempted
    max_runtime=3 * 3600,             # seconds; default ~3h cap
)
print(job.link)                       # web UI URL

job.wait(interval=10, timeout=3600, stop_on_timeout=True)   # blocks until terminal
print(job.status)                     # Status.Completed / Failed / Stopped / Running / Pending
if job.status == Status.Failed:
    print(job.logs)                   # snapshot of logs so far (also: `lightning job logs <name>`)
print(job.total_cost)                 # USD

job.stop(); job.delete()

# distributed job — same API plus num_machines; per-worker access via .machines
mmt = MMT.run(name="my-mmt", num_machines=2, machine=Machine.L4, image="...", command="...", teamspace=ts)
mmt.wait()
for worker in mmt.machines:           # each worker is a Job
    print(worker.name, worker.status) # per-node logs via worker.logs; mmt.logs (or `lightning mmt logs`) merges all ranks
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
Drive under `jobs/<job-name>/`, so reading it does not require a Studio:

```bash
lightning cp lit://<owner>/<teamspace>/jobs/<job-name>/model.joblib ./model.joblib   # one file
lightning cp -r lit://<owner>/<teamspace>/jobs/<job-name>/outputs/ ./outputs         # a folder
```

The capture can hold much more than the files you wrote — up to the job's whole
home — so copy the specific files or subfolder rather than the whole
`jobs/<job-name>/` tree. To see what's there first:
`lightning api "/v1/projects/<pid>/artifacts/trees/jobs/<job-name>?recursive=true"`.
Deleting the job deletes this tree with it.

Image (docker) jobs have no home-artifact collection — mount an output location with
`path_mappings` (see the table above). As an explicit escape hatch from any job you can
`lightning cp <file> lit://<owner>/<teamspace>/uploads/<path>` to the teamspace Drive,
but for studio jobs writing to home is the intended path.

### Machines

`CPU_SMALL`, `CPU`, `CPU_X_2/4/8/16`, `DATA_PREP(_MAX/_ULTRA)`, `T4(_X_2/4/8)`, `L4(_X_2/4/8)`, `L40S(_X_2/4/8)`, `RTXP_6000(_X_2/4/8)`, `A100(_X_2/4/8)`, `H100(_X_2/4/8)`, `H200(_X_8)`, `B200_X_8`. Multi-GPU `_X_N` variants bill N GPUs; MMT bills per machine × `num_machines`.

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
lightning mmt run --name ddp-test --teamspace my-org/my-teamspace \
  --image pytorch/pytorch:2.4.1-cuda12.1-cudnn9-runtime --num-machines 2 --machine L4 \
  --command "python -m torch.distributed.run --nproc_per_node=1 train.py"
```

**SSH into a running job / MMT worker** (for a human user; agents should prefer `inspect` and
the Python SDK since `ssh` opens an interactive shell):

```bash
lightning job ssh train-run-42 --teamspace my-org/my-teamspace          # single job, must be Running
lightning mmt ssh ddp-test --teamspace my-org/my-teamspace              # MMT rank 0
lightning mmt ssh ddp-test --rank 1 --teamspace my-org/my-teamspace     # MMT rank 1
```

Use `job ssh` for single jobs and `mmt ssh --rank N` for multi-machine workers — `job ssh`
has no `--rank` flag.

## Raw API fallback

For what the CLI doesn't wrap (chiefly exact-cost JSON and other raw resource fields —
logs are now a first-class CLI command, see above). **Call these as plain GETs — do NOT
add `-F limit=…`: a `-F` field flips the request into a spec'd form and the server rejects
it with `400 "spec is required"` (see Gotchas). Slice client-side with `-q`.**

```bash
PROJECT_ID=$(lightning api /v1/memberships | jq -r '.memberships[0].projectId')

# list jobs (id + name) — plain GET, no -F
lightning api "/v1/projects/${PROJECT_ID}/jobs" -q '.jobs[] | [.id, .name] | @tsv'

# inspect one job as JSON (status, machine, cost, timestamps) — by JOB ID (job_...), not name
lightning api "/v1/projects/${PROJECT_ID}/jobs/${JOB_ID}"

# list multi-machine jobs — also a plain GET, no -F
lightning api "/v1/projects/${PROJECT_ID}/multi-machine-jobs" -q '.multiMachineJobs[].name'
```

`JOB_ID` is the `job_...` id from the list call (these endpoints 404 on the human name).
To find one job by name, **filter the list** — the `/jobs/find` route returns `501 Not
Implemented`. For everyday use prefer the CLI: `lightning job list`, `lightning job
inspect <name>`, `lightning job logs <name>`, `lightning mmt list`.

## Gotchas

- Jobs bill machine time while allocated; confirm with the user before launching on expensive GPUs (A100/H100/H200/B200) or high `num_machines`, and prefer `wait(..., stop_on_timeout=True)` so runaway jobs get stopped.
- **`lightning job delete` prompts for confirmation — pass `-y`/`--yes` non-interactively.** Without it the command reads the prompt from a closed stdin, prints `Are you sure you want to delete? [y/N]: Aborted.` and exits **without deleting**. The job stays listed and keeps costing money, and the failure is easy to miss in a log.
- **`--query`, `--severity` and `--timestamps` can silently do nothing on a *finished* job.** Where a job's logs are stored decides this, and you cannot tell from the outside: if its lines aren't in the newer log storage, a finished job falls back to its saved log file, and that path ignores all three flags. `--query <term>` and `--severity <level>` then return **zero lines** for every value while the same command unfiltered returns the full log, and `--timestamps` output is byte-for-byte identical to plain output. Nothing warns you, so an empty result is indistinguishable from "no matches". **Don't trust a filtered read of a finished job** — fetch unfiltered and filter locally (`grep`). While a job is still `Running` the flags are applied server-side and work.
- **`job inspect` does not emit parseable JSON.** It pretty-prints to terminal width and hard-wraps long values — notably `command` — inserting raw newlines inside JSON strings, so `jq` fails with `Invalid string: control characters from U+0000 through U+001F must be escaped`. Don't build a polling loop on `job inspect | jq`; use `lightning api "/v1/projects/$PID/jobs"` and filter with `-q`, or `job logs --json`.
- **`job inspect` has no exit code, failure message, or timestamps** — it returns only `command`, `image`, `machine`, `name`, `status`, `studio`, `teamspace`, `total_cost`, and the SDK `Job` object exposes no equivalents either. To find out *why* and *when* a job failed, read the raw record's `message` field: `lightning api "/v1/projects/$PID/jobs" -q '.jobs[] | select(.name=="<name>") | .message'`.
- Logs read while a job runs, not just after: `lightning job logs <name> --follow` streams live, and `print(job.logs)` returns a snapshot of what's available so far. While a job is still `Pending` (no machine scheduled yet) there may be nothing to show.
- `lightning job ssh` / `lightning mmt ssh` only work while the target is **Running** — Pending/Completed/Failed/Stopped raise a clean error. For multi-machine jobs use `mmt ssh --rank N` (defaults to 0); `job ssh` has no `--rank`.
- On the raw `lightning api` GET list endpoints (`/jobs`, `/multi-machine-jobs`), do **not** pass `-F limit=…` — a `-F` field turns the GET into a spec'd request and the server 400s with `"spec is required"` (jobs) / `"name is required"` (mmt). Call them bare and slice with `-q`. The per-job endpoints take the `job_...` id, not the name, and `/jobs/find` returns `501` (filter the list instead).
- `image` and `studio` are mutually exclusive; a studio job's studio must be in the same teamspace and cloud account.
- Omitting **both** `--studio` and `--image` (Python: leaving both `studio=` and `image=` unset) does not error — it defaults to the Studio you're currently running inside, resolved via the `LIGHTNING_CLOUD_SPACE_ID` env var, as long as that Studio's teamspace matches the resolved `--teamspace`. Useful for "run this script from my current Studio" without looking up the Studio's name first. If you're not running inside a Studio (or the teamspace doesn't match), omitting both raises an error asking for one explicitly.
- Studio-job outputs go to **home** (`$LIGHTNING_ARTIFACTS_DIR`), not to `/teamspace/jobs/<name>/artifacts` — that path is **read-only** (writing to it fails `OSError: [Errno 30] Read-only file system`) and is only how you *read* artifacts back from the source Studio. Jobs can't write into the live Studio filesystem. See *Outputs & artifacts*.
- Job names must be unique per teamspace; omitted `--name` auto-generates one.
- `--machine` flag is case-insensitive; A100_40GB/A100_80GB variants are SDK-only (hidden from CLI).
- `job.stop()` blocks (polls every 1s) until the job reaches a terminal state.
- `--org`/`--org=` on `job run`/`mmt run` is deprecated (the CLI prints a `DeprecationWarning` and will remove it) in favor of the combined `--teamspace owner/teamspace` form, which works fine headlessly as of `lightning-sdk` 2026.8.5 — verified against a live env-var-only auth session. Older guidance told you to split `--teamspace <name> --org <owner>` to dodge a headless-auth resolution bug; if you're on an SDK version old enough to still hit "Neither name is provided nor can the user be inferred from the environment variable!" on the combined form, fall back to the split flags, but prefer combined `owner/teamspace` first and only split if it actually errors.
- Image jobs can sit in `Pending`/`creating` for a long time (tens of minutes on busy shared pools) before a machine is scheduled — pending time is not billed, but don't treat a slow start as failure. Always use `job.wait(timeout=..., stop_on_timeout=True)` or monitor `job.status` with your own deadline, and `job.stop()`+`job.delete()` if you give up.
