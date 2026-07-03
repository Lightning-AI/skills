---
name: lightning-jobs
description: Launch and manage batch jobs on Lightning AI - run commands on cloud CPUs/GPUs from a Docker image or a Studio snapshot, monitor status, fetch logs, collect artifacts, and run multi-machine (distributed) training. Use when the user wants to run training, data processing, or any batch workload on lightning.ai.
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

## CLI reference

Subcommands: `run`, `list`, `inspect`, `stop`, `delete`. **There is no `logs` or `status` CLI subcommand** — use `inspect` (JSON, includes status) or the Python SDK for logs.

```bash
# image job — NOTE: job/mmt run need --teamspace <name> --org <owner> (bare "owner/name" breaks headless, see Gotchas)
lightning job run --name my-job --teamspace teamspace --org owner \
  --image python:3.11-slim --machine CPU \
  --command "python -c 'print(\"hello\")'" \
  [-e KEY=VALUE ...] [--interruptible] [--cloud PROVIDER]

# studio job (snapshot of an existing studio's environment; command required)
lightning job run --name my-job --teamspace teamspace --org owner \
  --studio my-studio --machine A100 --command "python train.py"

# private registry image
lightning job run ... --image-credentials <secret-name> [--cloud-account-auth]  # --cloud-account-auth for ECR-type registries

# monitor / manage
lightning job list --teamspace owner/teamspace [--all] [--sort-by status]
lightning job inspect my-job --teamspace owner/teamspace      # JSON incl. status, machine, cost
lightning job stop my-job --teamspace owner/teamspace
lightning job delete my-job --teamspace owner/teamspace

# multi-machine training: same flags plus --num-machines
lightning mmt run --name my-mmt --teamspace teamspace --org owner \
  --image pytorch/pytorch:2.4.1-cuda12.1-cudnn9-runtime --num-machines 2 --machine L4 \
  --command "python -m torch.distributed.run --nproc_per_node=1 train.py"
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
    print(job.logs)                   # ONLY available once job is terminal (raises while running)
print(job.total_cost)                 # USD

job.stop(); job.delete()

# distributed job — same API plus num_machines; per-worker access via .machines
mmt = MMT.run(name="my-mmt", num_machines=2, machine=Machine.L4, image="...", command="...", teamspace=ts)
mmt.wait()
for worker in mmt.machines:           # each worker is a Job
    print(worker.name, worker.status) # worker.logs per node; mmt.logs raises NotImplementedError
```

Fetch an existing job: `Job("my-job", teamspace=ts)` (raises `ValueError` if it doesn't exist).

### Image vs studio jobs

| | Studio job | Image job |
|---|---|---|
| `command` | required | optional (falls back to image entrypoint) |
| `entrypoint`, `image_credentials`, `cloud_account_auth` | forbidden | allowed |
| artifacts | `/teamspace/jobs/<name>/artifacts` | none by default — route via `path_mappings={"<container-path>": "<connection>:<path>"}` |
| scratch disks | `scratch_disks={"data": 100}` (GiB, under `/teamspace/scratch/`) | forbidden |

### Machines

`CPU_SMALL`, `CPU`, `CPU_X_2/4/8/16`, `DATA_PREP(_MAX/_ULTRA)`, `T4(_X_2/4/8)`, `L4(_X_2/4/8)`, `L40S(_X_2/4/8)`, `RTXP_6000(_X_2/4/8)`, `A100(_X_2/4/8)`, `H100(_X_2/4/8)`, `H200(_X_8)`, `B200_X_8`. Multi-GPU `_X_N` variants bill N GPUs; MMT bills per machine × `num_machines`.

## Example workflows

Prompts this skill handles: *"run this script on an A100 as a batch job"*, *"launch my docker image on lightning"*, *"why did my job fail — show me the logs"*, *"run a 2-node distributed training"*.

**Run a containerized script and report the outcome:**

```bash
lightning job run --name fmt-check-$(date +%s) --teamspace my-teamspace --org my-org \
  --image python:3.11-slim --machine CPU \
  --command "pip install ruff && ruff check ." 
lightning job list --teamspace my-org/my-teamspace --sort-by status
```

**Launch, wait, and fetch logs (the reliable agent loop — logs are SDK-only and terminal-state-only):**

```python
from lightning_sdk import Job, Machine, Status
job = Job.run(name="train-run-42", machine=Machine.L4, image="pytorch/pytorch:2.4.1-cuda12.1-cudnn9-runtime",
              command="python -c 'import torch; print(torch.cuda.is_available())'",
              teamspace="my-teamspace", org="my-org", interruptible=True)
job.wait(interval=15, timeout=2*3600, stop_on_timeout=True)
print(job.status, f"${job.total_cost:.4f}")
print(job.logs)          # safe now: job is terminal
```

One-liner to check an existing job from the shell:

```bash
uv run --with lightning-sdk python -c \
  "from lightning_sdk import Job; j=Job('train-run-42', teamspace='my-teamspace', org='my-org'); print(j.status); print(j.logs if str(j.status) in ('Completed','Failed','Stopped') else '(still running)')"
```

**Parameter sweep — several jobs from one loop:**

```python
for lr in ["1e-3", "3e-4", "1e-4"]:
    Job.run(name=f"sweep-lr-{lr}", machine=Machine.T4, studio="exp-1",
            command=f"python train.py --lr {lr}", env={"WANDB_RUN": f"lr-{lr}"},
            teamspace="my-teamspace", org="my-org", interruptible=True)
# artifacts of each land in /teamspace/jobs/<name>/artifacts (studio jobs)
```

**Distributed (2×L4, one process per node):**

```bash
lightning mmt run --name ddp-test --teamspace my-teamspace --org my-org \
  --image pytorch/pytorch:2.4.1-cuda12.1-cudnn9-runtime --num-machines 2 --machine L4 \
  --command "python -m torch.distributed.run --nproc_per_node=1 train.py"
```

## Raw API fallback

```bash
PROJECT_ID=$(lightning api /v1/memberships | jq -r '.memberships[0].projectId')
lightning api "/v1/projects/${PROJECT_ID}/jobs" -F limit=20 -q '.jobs[].name'
lightning api "/v1/projects/${PROJECT_ID}/jobs/find" -f name=my-job
lightning api "/v1/projects/${PROJECT_ID}/jobs/${JOB_ID}/download-logs"    # returns signed log URL
lightning api "/v1/projects/${PROJECT_ID}/multi-machine-jobs" -F limit=20
```

## Gotchas

- Jobs bill machine time while allocated; confirm with the user before launching on expensive GPUs (A100/H100/H200/B200) or high `num_machines`, and prefer `wait(..., stop_on_timeout=True)` so runaway jobs get stopped.
- `job.logs` raises while the job is Pending/Running — poll `job.status` first, read logs after it reaches a terminal state.
- `image` and `studio` are mutually exclusive; a studio job's studio must be in the same teamspace and cloud account.
- Job names must be unique per teamspace; omitted `--name` auto-generates one.
- `--machine` flag is case-insensitive; A100_40GB/A100_80GB variants are SDK-only (hidden from CLI).
- `job.stop()` blocks (polls every 1s) until the job reaches a terminal state.
- `job run`/`mmt run` fail with `--teamspace owner/name` when the username can't be resolved (headless env-var auth): the real error "Teamspace owner/name does not exist" is masked by "Neither name is provided nor can the user be inferred from the environment variable!". Pass `--teamspace <name> --org <owner>` instead. `job list`/`inspect`/`stop`/`delete` accept `owner/name` fine.
- Same rule in Python: `Job.run(..., teamspace="<name>", org="<owner>")` — a combined `"owner/name"` string is not valid for `teamspace=` in SDK classes.
- Image jobs can sit in `Pending`/`creating` for a long time (tens of minutes on busy shared pools) before a machine is scheduled — pending time is not billed, but don't treat a slow start as failure. Always use `job.wait(timeout=..., stop_on_timeout=True)` or monitor `job.status` with your own deadline, and `job.stop()`+`job.delete()` if you give up.
