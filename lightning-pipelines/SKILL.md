---
name: lightning-pipelines
description: Build, update, schedule, inspect and troubleshoot Lightning AI pipelines with dependent CPU/GPU jobs, Studio snapshots and shared data. Use when the user wants a multi-step workflow, recurring training, schedule replacement, or logs from a specific pipeline run.
license: Apache-2.0
compatibility: Requires Python with uv or pip, lightning-sdk, network access to lightning.ai and a Lightning AI account. Raw API examples use jq.
---

# Lightning AI Pipelines

`Pipeline.run()` submits a new version **and starts it immediately**, even with
schedules. A schedule starts a copy of its target version.

| Request | Use | Verify |
|---|---|---|
| Run new or changed steps | `Pipeline.run()` | Run state and expected step output |
| Change only the timing | Schedule PUT | Cron, timezone, target and next execution |
| Use new steps for future runs | Submit version and replace schedules | Desired schedules target the new version; old IDs are absent |
| Explain a failure | Exact run and step logs | Project, run and job IDs match the requested execution |
| Stop current work | Run PUT | Terminal state; schedules remain active unless removed separately |

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

If authentication fails, check `lightning auth whoami`. Use `lightning login` on
a machine with a browser; in headless environments, configure `LIGHTNING_API_KEY`
through the environment's secret settings. Never request keys in chat.

## Resolving org and teamspace (do this first)

Reuse the requested `owner/teamspace`, environment or configured default.
If ambiguous, list memberships and ask which one; deduplicate by `projectId`.

```bash
lightning config get teamspace
lightning api /v1/memberships -q '.memberships[] | [.ownerType,.ownerId,.name,.projectId] | @tsv'
```

For an organization, GET `/v1/orgs/<ownerId>` and use `.name`. For a personal
owner, GET `/v1/users/search` with `query=<ownerId>` and match `.users[].id`
exactly before taking `.username`. Python `Teamspace("owner/teamspace").id`
also resolves an explicit slug.

Replace these example values with the verified selection:

```bash
TS=owner/teamspace
PID='verified-project-id'
lightning api "/v1/projects/$PID/clusters" -q '.clusters[] | [.id,.name,.status.phase] | @tsv'
```

Use the requested cloud or teamspace default. Keep data-bearing workflows on
their intended cloud; a startup failure does not authorize moving their data.

## CLI reference

`lightning pipeline` exposes **logs**, not create/run/list/stop commands.
Use Python to submit DAGs and [API operations](references/operations.md) to
inspect runs or change schedules/lifecycle state.

```bash
lightning pipeline logs "$RUN" prepare --teamspace "$TS" --tail 100 --timestamps
lightning pipeline logs "$RUN" prepare --teamspace "$TS" --follow
lightning pipeline logs "$RUN" prepare --teamspace "$TS" --query error --severity error
```

Use the exact run ID. The second argument is `.statuses[].name`, not the
generated job name. Inspect `lightning pipeline logs download --help` for export.
Deployment steps need deployment logs; multi-machine steps may need `--rank`.

## Python SDK

Replace the teamspace and name. Use a unique name for a test; reuse an existing
name only to update that pipeline. This image example needs no Studio:

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

For Studio snapshots, shared files, explicit DAGs or multi-machine steps, read
[Studio and multi-step workflows](references/studio-workflows.md).
Dependencies control execution order; they do not transfer files. Shared
filesystem mode requires supported AWS/GCP placement. On Lightning Cloud image
jobs, use `shared_filesystem=False` and explicit durable outputs when needed.

After submission, poll the exact run with a workload-appropriate deadline.
Do not resubmit while polling. Check terminal state, step logs and expected
outputs. On timeout, report the current state and whether work is still running.

## Example workflows

### Change only the weekly schedule

Follow the [schedule-only update](references/operations.md#edit-a-schedule-without-starting-a-run).
Preserve the target and change only requested fields. Read back cron, timezone,
target and next execution. Do not submit a pipeline version for a timing-only edit.

### Apply updated steps to future runs

1. Resolve the version, root and ancestors. Record schedule IDs, targets, cron
   expressions and timezones before submission.
2. Submit the steps and complete desired schedule set with `Pipeline.run()`.
3. List schedules under the root and ancestors. Verify new targets/root grouping
   and absence of replaced IDs.
4. For an execution test, verify a child's `scheduleId`, `parentPipelineId` and
   step output. Otherwise report configuration verification only. Keep a user's
   weekly schedule intact; use a disposable pipeline for an earlier test.

SDK [PR #201](https://github.com/Lightning-AI/sdk/pull/201) fixes duplicate schedules
across repeated updates. Check whether the installed release includes it before
relying on replacement. Older versions need explicit removal of verified obsolete
schedule IDs. In the fixed version, omitted `schedules` or `[]` removes schedules;
pass the desired schedules again to retain them. Replacement uses several API
calls: inspect partial results before retrying and avoid concurrent updates.

### Diagnose a failed execution

[Find the run](references/operations.md#find-runs-and-schedules) by timestamp/ID
under both the root and scheduled version. Read statuses and fetch the failing
step's logs. Before customer export, verify project, root, run and job IDs.
For a job that never started, inspect `message` and `serverError`.
`Stopping` is not terminal; the SDK can refuse logs until it finishes.

## Raw API fallback

Load only the recipe the task needs:

- [Discovery](references/operations.md#find-runs-and-schedules): IDs, ancestry, scheduled children and pagination.
- [Schedule editing](references/operations.md#edit-a-schedule-without-starting-a-run): GET, change requested fields, PUT, read back.
- [Stopping or unscheduling](references/operations.md#stop-a-run-or-remove-a-schedule): distinct lifecycle operations.
- [Cleanup](references/operations.md#cleanup): remove recorded test resources and verify their absence.

## Gotchas

- Omit `timezone` for UTC. The tested SDK rejects explicit `timezone="UTC"`;
  named zones such as `Europe/London` follow DST.
- Read back `parallel_runs`: production returned false even when true was
  submitted. Do not infer concurrency from the request alone.
- `parentResourceId` groups schedules under a root; `resourceId` chooses the
  version to execute. Correct UI grouping does not prove the correct target.
- `Pipeline.stop()` at SDK commit `b2dc858` raises `TypeError` because its update
  call lacks IDs. Use the [stop recipe](references/operations.md#stop-a-run-or-remove-a-schedule)
  with `PIPELINE_STATE_STOP` until the installed SDK fixes it.
- Lifecycle messages and missing historical logs are not workload tracebacks.
  Preserve the job ID and distinguish unavailable logs from an application failure.

## Report and resume

Report teamspace, run link/ID, change, observed state/output and remaining work.
For schedules, include cron, timezone, target and next execution; distinguish
configuration readback from an observed scheduled execution. For tests, report
cleanup and any resources still running.

For a task spanning a long wait or handoff, save the project/root/run/job/schedule
IDs, schedule snapshot and last verified state in a local task record. On resume,
reload it and GET current state before deciding whether to submit or retry.
