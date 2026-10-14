---
name: lightning-pipelines
description: Build, update, schedule, inspect and troubleshoot Lightning AI pipelines with dependent CPU/GPU jobs, Studio snapshots and shared data. Use when the user wants a multi-step workflow, recurring training, schedule replacement, a pipeline rerun, or logs from a specific pipeline run.
license: Apache-2.0
compatibility: Requires Python with uv or pip, lightning-sdk, network access to lightning.ai and a Lightning AI account. Raw API examples use jq.
---

# Lightning AI Pipelines

A pipeline stores a DAG and starts an execution when submitted. A scheduled execution
copies the version referenced by its schedule. Creating/updating the pipeline can
start billable work immediately, even when schedules are supplied.

## Setup & auth

```bash
export DEBUG=0 LIGHTNING_DEBUG=0   # an inherited DEBUG=1 logs the Authorization header; set this in every new shell
# Use the Lightning AI CLI from the current env; install or upgrade it there if it's missing or older than 2026.9.18
v=$(lightning --version 2>/dev/null | sed -n 's/^Lightning CLI version //p')
[ -n "$v" ] && [ "$(printf '%s\n' 2026.9.18 "$v" | sort -V | head -1)" = 2026.9.18 ] \
  || uv pip install -U lightning-sdk || python3 -m pip install -U lightning-sdk
lightning --version   # must print "Lightning CLI version …"; if not, see the table below
lightning login                     # browser flow; or headless:
export LIGHTNING_API_KEY=... LIGHTNING_USER_ID=...   # USER_ID optional: with it Basic auth, without it the key is a Bearer token
```

This uses, and if needed installs into, the agent's current environment (project venv, conda env,
…); then call plain `lightning …` everywhere. If setup doesn't go cleanly:

| Symptom | Fix |
|---|---|
| No active env, `externally-managed-environment`, or a conflict with the project's own pins | Install it on its own: `uv tool install lightning-sdk` (or `pipx install lightning-sdk`), and call `"$(uv tool dir --bin)/lightning"` if that isn't on `PATH` |
| Still no `Lightning CLI version` line after installing | Another `lightning` (PyTorch Lightning's) or an inactive env comes first: check `command -v lightning`, then activate the env or call its `bin/lightning` |
| `No such command`, `No such option` or `unexpected extra argument` | The CLI is too old: upgrade it where `command -v lightning` points (`uv pip install -U lightning-sdk`, or `uv tool upgrade lightning-sdk`) |
| Installing is blocked (read-only env or home, agent sandbox) | Run each command as `UV_CACHE_DIR="${TMPDIR:-/tmp}/uv" UV_TOOL_DIR="${TMPDIR:-/tmp}/uv-tools" uvx lightning-sdk …` |
| SSL/CA errors on every call, even `lightning --version`, only inside an agent sandbox | A `**/*.pem` read-deny rule hides certifi's `cacert.pem`: allow that one path; don't disable the sandbox |
| `NameResolutionError`, only inside an agent sandbox | If `lightning api` fails too, ask the user to allow `lightning.ai` and `*.lightning.ai` (`/sandbox`). If only `studio`/`job` commands or the SDK fail, they bypass the sandbox proxy: run just those outside it. Background pollers fail the same way, silently |

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

Use the user's explicit `owner/teamspace`, configured default, or environment.
If none identifies one teamspace, list memberships and ask once; reuse the answer.
Do not guess an organization from a teamspace name. Membership rows can duplicate
one project through user and organization access: deduplicate by `projectId`.

```bash
lightning api /v1/memberships -q '.memberships[] | [.ownerType,.ownerId,.name,.projectId] | @tsv'
lightning config get teamspace
```

Resolve an organization owner ID with `lightning api /v1/orgs/<ownerId>` (`.name`).
For a personal owner, use `/v1/users/search -X GET -f query=<ownerId>` and match
`.users[].id` exactly before taking `.username`. A fuzzy match is not identity proof.
For an explicit slug, Python `Teamspace("owner/teamspace").id` resolves the project.

Set shell variables from that verified selection, not from the first list result:

```bash
TS=owner/teamspace
PID=<verified-project-id>
lightning api "/v1/projects/$PID/clusters" -q '.clusters[] | [.id,.name,.status.phase] | @tsv'
```

Choose the user's cloud or the teamspace default; confirm it is usable. Do not
silently move a data-bearing workflow to another cloud to solve a startup failure.

## CLI reference

`lightning pipeline` currently exposes **logs**, not create/run/list/stop commands.
Use Python for constructing DAGs and the raw API below for lifecycle operations.
Use exact run IDs for investigations; resolving a display name can select another run.

```bash
lightning pipeline --help
lightning pipeline logs "$RUN" prepare --teamspace "$TS" --tail 100 --timestamps
lightning pipeline logs "$RUN" prepare --teamspace "$TS" --follow
lightning pipeline logs "$RUN" prepare --teamspace "$TS" --query error --severity error
```

The second positional argument is the **step name** in `.statuses[].name`, not
its generated job name. For download syntax, inspect `lightning pipeline logs download --help`.
Deployment steps require deployment logs; multi-machine steps may need `--rank`.
A lifecycle line saying "workload failed" is not the underlying Python traceback.

## Python SDK

### Submit an image workflow

Use the SDK in the same environment as the CLI. A unique name creates a disposable
pipeline; reuse the name only when updating that existing workflow is intended.
This one-step smoke test needs no Studio or shared filesystem:

```python
from lightning_sdk import Machine, Teamspace
from lightning_sdk.pipeline import JobStep, Pipeline

ts = Teamspace("owner/teamspace")
pipeline = Pipeline(name="my-pipeline", teamspace=ts, shared_filesystem=False)
pipeline.run(steps=[
    JobStep(name="prepare", image="python:3.11-slim", machine=Machine.CPU,
            command="python -c 'print(123)'", max_runtime=300),
])
```

Submission success only proves the API accepted the DAG. Read the run and step
states below until terminal; inspect logs and expected output before declaring success.

### Studio preparation then training

The scripts must already exist in the chosen Studio. Construct `Studio` with the
same explicit teamspace. A Studio name is not a local directory.

```python
from lightning_sdk import Machine, Studio, Teamspace
from lightning_sdk.pipeline import JobStep, Pipeline, Schedule

ts = Teamspace("owner/teamspace")
studio = Studio("train-env", teamspace=ts)
pipeline = Pipeline(name="training-on-studio-env", teamspace=ts,
                    studio=studio, shared_filesystem=True)
pipeline.run(
    steps=[
        JobStep(name="prepare", command="sh /teamspace/studios/this_studio/prepare_data.sh",
                machine=Machine.CPU_X_16, max_runtime=3600),
        JobStep(name="training", command="sh /teamspace/studios/this_studio/training.sh",
                machine=Machine.RTXP_6000, wait_for=["prepare"], max_runtime=7200),
    ],
    schedules=[Schedule("40 5 * * 1", name="weekly-training")],
)
```

Omitted `timezone` means UTC. Do not pass `timezone="UTC"`: the SDK's explicit
allow-list rejects that spelling. A named zone such as `Europe/London` follows DST.
`parallel_runs=False` is the schedule default. Read back the value: production
returned false even when the tested SDK submitted true; do not promise concurrent
scheduled execution from the requested value alone.

With the default `dependency_mode="linear"`, steps without `wait_for` chain in
list order. For a DAG, use `dependency_mode="explicit"` and explicit dependency
names: two independent preparation steps can have `wait_for=[]`, with training
waiting for both. Step names must be unique and dependencies must reference them.

A dependency does **not** transfer files. Shared filesystem mode needs a supported
AWS/GCP cloud and compatible placement; the SDK raises for unsupported clouds.
Without it, choose an explicit durable output location and make downstream steps
read it. For Lightning Cloud image smoke tests use `shared_filesystem=False`.

`JobStep(num_machines=N)` represents a multi-machine job. `DeploymentStep` adds
serving to the DAG; deployments have their own lifetime, auth and cleanup. Inspect
the installed signatures before configuring those less common steps; do not assume
that every `Job.run` or `Deployment.start` option has a pipeline equivalent.

## Example workflows

### "Update this pipeline's weekly schedule"

1. Resolve the existing run, its root and all ancestor IDs. Snapshot the current
   schedule IDs and expressions before submitting anything.
2. Submit the intended new steps/schedules with `Pipeline.run`. This also starts
   an immediate run. Do not use it merely to edit a cron expression when no run
   was requested; edit that schedule through the API instead.
3. Read schedules back: the desired schedule's `resourceId` must be the new run ID,
   and `parentResourceId` the original root. Old schedule IDs must be absent.
4. Observe a scheduled child: its `scheduleId` must match and its
   `parentPipelineId` must identify the intended version. Check its step command
   and output marker, not just the number of schedules in the UI.

**Version boundary:** SDK PR [#201](https://github.com/Lightning-AI/sdk/pull/201)
fixes repeated updates leaving schedules grouped under intermediate versions.
Until the installed release includes that change, do not promise automatic replacement.
After verifying ownership, remove obsolete schedules explicitly using their exact IDs.
The fixed implementation treats omitted `schedules` or `[]` as removal; preserving
schedules requires passing the intended schedules again. Replacement is several API
calls, not a transaction: after a partial failure inspect both runs and schedules
before retrying. Avoid concurrent updates to one pipeline.

### "Why did Monday's training fail?"

List runs under the original root, select the requested timestamp/ID, read its
statuses, then fetch the failing step's logs. Confirm project ID, pipeline root,
run ID and job ID before exporting anything to a customer. Do not substitute
logs from the newest run or another similarly named pipeline.

If the job never started, inspect its `message` and `serverError`; a machine
startup failure is not a training exception. `Stopping` is not terminal, and the
SDK can refuse logs in that state. Poll with a deadline, then report the exact
state/IDs and evidence gap rather than manufacturing a workload error.

## Raw API fallback

The commands below use the verified `$PID`; IDs come from API readback. Follow
`nextPageToken` if present, passing it as `-f pageToken=...`. Do not assume the
first page contains every run.

```bash
# Discover roots, then select one by name/id and verify its project.
lightning api "/v1/projects/$PID/pipelines" -X GET -F standalone=true -F limit=100
ROOT=<original-pipeline-id>
lightning api "/v1/projects/$PID/pipelines" -X GET -f originalPipelineId="$ROOT" -F limit=100
# Scheduled children can be absent from the root-filtered response; query their target too.
VERSION=<scheduled-version-id>
lightning api "/v1/projects/$PID/pipelines" -X GET -f parentPipelineId="$VERSION" -F limit=100
RUN=<selected-run-id>
lightning api "/v1/projects/$PID/pipelines/$RUN" -q '{id,name,projectId,parentPipelineId,scheduleId,state,message,statuses}'
lightning api "/v1/projects/$PID/pipelines/$RUN" -q '.steps'
```

If the response lacks `originalPipelineId`, walk `parentPipelineId` with GET
until empty, keeping visited IDs to detect a cycle. Never treat the immediate
parent as the root after several updates.

```bash
# Root-grouped schedules (UI grouping) and schedules targeting this version.
lightning api "/v1/projects/$PID/schedules" -X GET -f parentResourceId="$ROOT"
lightning api "/v1/projects/$PID/schedules" -X GET -f resourceId="$RUN"
SID=<verified-schedule-id>
lightning api "/v1/projects/$PID/schedules/$SID"

# Stop an exact execution. This does not disable future schedules.
lightning api "/v1/projects/$PID/pipelines/$RUN" -X PUT -f state=PIPELINE_STATE_STOP

# Disable future occurrences by deleting only the selected schedule.
lightning api "/v1/projects/$PID/schedules/$SID" -X DELETE
```

To edit a schedule without launching a job, GET it, retain its intended target
and grouping, and PUT the revised schedule body to the same schedule URL. Read
it back. For example, change only the cron expression, preserving the other fields:

```bash
SCHEDULE_BODY=$(mktemp)
lightning api "/v1/projects/$PID/schedules/$SID" \
  | jq '.cronExpression = "40 5 * * 1"' > "$SCHEDULE_BODY"
lightning api "/v1/projects/$PID/schedules/$SID" -X PUT --input "$SCHEDULE_BODY"
rm "$SCHEDULE_BODY"
lightning api "/v1/projects/$PID/schedules/$SID"
```

Do not invent a `schedule run` endpoint for pipelines: the similarly
named endpoint under `/cloudspaces/.../schedules/.../runs` belongs to Studios.

### Cleanup

For a disposable test, delete its schedules first, request stop for its active
runs, wait for terminal states, then delete the test runs and root. Save evidence
before deletion. Enumerate scheduled children through `parentPipelineId` for each
known version as well as the root listing; do not assume root deletion removes
children omitted from that listing. Delete those exact children first and verify
both listings are empty. Do not delete a user's workflow to stop
one execution. Deleting a pipeline can delete its run history.

```bash
SCHEDULED_RUN=<verified-test-child-id>
lightning api "/v1/projects/$PID/pipelines/$SCHEDULED_RUN" -X DELETE
lightning api "/v1/projects/$PID/pipelines/$ROOT" -X DELETE
lightning api "/v1/projects/$PID/pipelines" -X GET -f originalPipelineId="$ROOT"
lightning api "/v1/projects/$PID/schedules" -X GET -f parentResourceId="$ROOT"
lightning api "/v1/projects/$PID/pipelines" -X GET -f parentPipelineId="$VERSION"
```

## Gotchas

- `Pipeline.stop()` in SDK commit `b2dc858` calls the generated update method
  without project/run IDs and raises `TypeError`. Use the verified PUT above
  until your installed SDK fixes it. The API enum is `PIPELINE_STATE_STOP`, not `stop`.
- Root grouping and schedule target are different fields. A schedule can appear
  under the correct UI pipeline while still targeting an old version.
- A successful create response is not a completed job. Record the actual
  scheduled run and output when validating a schedule change.
- Empty historical logs do not prove that a workload emitted nothing. Keep the
  lifecycle response and job ID, and distinguish missing logs from the job failure.
