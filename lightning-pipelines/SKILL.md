---
name: lightning-pipelines
description: Build, update, schedule, inspect and troubleshoot Lightning AI pipelines with dependent CPU/GPU jobs, Studio snapshots and shared data. Use when the user wants a multi-step workflow, recurring training, schedule replacement, or logs from a specific pipeline run.
license: Apache-2.0
compatibility: Requires Python with uv or pip, lightning-sdk, network access to lightning.ai and a Lightning AI account. Raw API examples use jq.
---

# Lightning AI Pipelines

Use pipelines to run preparation, training and other dependent steps as one workflow.
`Pipeline.run()` submits a new version **and starts it immediately**, including when
schedules are supplied. A schedule starts a copy of the version it targets.

Choose the operation before making changes:

| User wants | Action | Evidence of success |
|---|---|---|
| Run new or changed steps | Submit with `Pipeline.run()` | Selected run completes; expected step output exists |
| Change only when a pipeline runs | Update the existing schedule with PUT | Cron, timezone and target read back correctly; no new run submitted |
| Apply new steps to future scheduled runs | Submit the version and replace its schedules | One intended schedule set remains, targeting the new version |
| Explain a failed execution | Inspect the exact run and failing step | Job ID, failure evidence and relevant logs match that execution |
| Stop current work | Stop the selected run | Run reaches a terminal state; future schedules are handled separately |

Reuse the user's teamspace and resource choices. Resolve missing IDs from the API;
ask only when several resources fit and the request does not identify one.

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

If the task calls for Lightning and the CLI is missing, run the setup block above,
then check authentication:

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

Then resolve the teamspace from the request and memberships. If exactly one fits,
continue with it.

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

Replace the example values below with the verified selection. Use that same `$PID`
and `$TS` throughout the task:

```bash
TS=owner/teamspace
PID='verified-project-id'
lightning api "/v1/projects/$PID/clusters" -q '.clusters[] | [.id,.name,.status.phase] | @tsv'
```

Choose the user's cloud or the teamspace default; confirm it is usable. Do not
silently move a data-bearing workflow to another cloud to solve a startup failure.

## CLI reference

`lightning pipeline` currently exposes **logs**, not create/run/list/stop commands.
Use Python for constructing DAGs and [API operations](references/operations.md)
for discovery and lifecycle changes.
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
This one-step example needs no Studio or shared filesystem. Replace the teamspace
and pipeline name before submitting; choose a unique name for a disposable test:

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
states until terminal with a deadline appropriate to the workload. Do not resubmit
while polling. Inspect logs and expected output before declaring success; on timeout,
report the current state and whether the job is still running.

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

### "Move the weekly run to Monday at 05:40 UTC"

Use the [schedule-only update](references/operations.md#edit-a-schedule-without-starting-a-run). Confirm the
selected schedule, timezone and target, then change only the requested fields.
Read them back and report the next execution time. Do not call `Pipeline.run()`
for a cron-only change: that would start an unrequested execution.

### "Use the updated training steps for future runs"

1. Resolve the current version, root and ancestors. Record existing schedule IDs,
   targets, cron expressions and timezones before submitting.
2. Submit the intended steps and complete desired schedule set with `Pipeline.run()`.
   This starts one immediate execution as well as configuring future ones.
3. List schedules under the root and ancestors. Verify the desired schedules target
   the new version, remain grouped under the root, and replaced IDs are absent.
4. Report configuration verification separately from execution verification. For an
   end-to-end test, observe a scheduled child whose `scheduleId` matches and whose
   `parentPipelineId` is the intended version; check its step output. Do not move a
   user's weekly schedule forward merely to finish a test sooner.

**Version boundary:** SDK PR [#201](https://github.com/Lightning-AI/sdk/pull/201)
fixes repeated updates leaving schedules grouped under intermediate versions.
Until the installed release includes that change, do not promise automatic replacement.
After verifying ownership, remove obsolete schedules explicitly using their exact IDs.
The fixed implementation treats omitted `schedules` or `[]` as removal; preserving
schedules requires passing the intended schedules again. Replacement is several API
calls, not a transaction: after a partial failure inspect both runs and schedules
before retrying. Avoid concurrent updates to one pipeline.

### "Why did Monday's training fail?"

List runs under the original root and scheduled version, select the requested
timestamp/ID, and read its statuses. Fetch the failing step's logs. Confirm project ID, pipeline root,
run ID and job ID before exporting anything to a customer. Do not substitute
logs from the newest run or another similarly named pipeline.

If the job never started, inspect its `message` and `serverError`; a machine
startup failure is not a training exception. `Stopping` is not terminal, and the
SDK can refuse logs in that state. Poll with a deadline, then report the exact
state, resource IDs and what remains unknown.

## Raw API fallback

Use [Pipeline API operations](references/operations.md) when the task needs:

- [Run or schedule discovery](references/operations.md#find-runs-and-schedules):
  resolve exact IDs, walk version ancestry and find scheduled children.
- [A schedule-only edit](references/operations.md#edit-a-schedule-without-starting-a-run):
  change cron settings while preserving the version target.
- [Stopping or unscheduling](references/operations.md#stop-a-run-or-remove-a-schedule):
  stop current work or remove future occurrences as separate operations.
- [Test cleanup](references/operations.md#cleanup): remove the recorded test
  resources and verify both root and scheduled-child listings.

## Gotchas

- `Pipeline.stop()` in SDK commit `b2dc858` calls the generated update method
  without project/run IDs and raises `TypeError`. Use the [stop recipe](references/operations.md#stop-a-run-or-remove-a-schedule)
  until your installed SDK fixes it. The API enum is `PIPELINE_STATE_STOP`, not `stop`.
- Root grouping and schedule target are different fields. A schedule can appear
  under the correct UI pipeline while still targeting an old version.
- A successful create response is not a completed job. Record the actual
  scheduled run and output when validating a schedule change.
- Empty historical logs do not prove that a workload emitted nothing. Keep the
  lifecycle response and job ID, and distinguish missing logs from the job failure.

## Report the result

Return the pipeline link or exact run ID, teamspace, operation performed and
verification evidence. For schedules, include cron, timezone, target version and
next execution time. State whether an execution was observed or only configuration
was verified. For tests, say which resources were removed and identify any still
running. For failures, include the relevant error and log location without
substituting lifecycle messages for workload output.
