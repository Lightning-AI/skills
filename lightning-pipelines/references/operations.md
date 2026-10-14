# Pipeline API operations

Use these recipes for run discovery, schedule changes, stopping or cleanup.
Complete setup and teamspace resolution in [the skill](../SKILL.md) first:
`$PID` is the verified project ID and `$TS` its `owner/teamspace` slug. Replace
example IDs with API results. Choose the section for the requested operation;
these recipes are not one script to execute top to bottom.

## Find runs and schedules

The commands below use the verified `$PID`; IDs come from API readback. Follow
`nextPageToken` if present, passing it as `-f pageToken=...`. Do not assume the
first page contains every run.

```bash
# Discover roots, then select one by name/id and verify its project.
lightning api "/v1/projects/$PID/pipelines" -X GET -F standalone=true -F limit=100
ROOT='original-pipeline-id'
lightning api "/v1/projects/$PID/pipelines" -X GET -f originalPipelineId="$ROOT" -F limit=100
# Scheduled children can be absent from the root-filtered response; query their target too.
VERSION='scheduled-version-id'
lightning api "/v1/projects/$PID/pipelines" -X GET -f parentPipelineId="$VERSION" -F limit=100
RUN='selected-run-id'
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
SID='verified-schedule-id'
lightning api "/v1/projects/$PID/schedules/$SID"
```

## Edit a schedule without starting a run

GET the selected schedule, retain its intended target and grouping, and PUT the
revised body to the same schedule URL. Read back its cron, timezone, target and next execution time. This example changes only
the cron expression; it preserves the existing timezone. Use it for 05:40 UTC only
when that schedule already uses UTC:

```bash
(
  set -e
  SCHEDULE_BODY=$(mktemp)
  trap 'rm -f "$SCHEDULE_BODY"' EXIT
  lightning api "/v1/projects/$PID/schedules/$SID" > "$SCHEDULE_BODY"
  jq -e '.id and .resourceId' "$SCHEDULE_BODY" >/dev/null
  UPDATED=$(jq '.cronExpression = "40 5 * * 1"' "$SCHEDULE_BODY")
  printf '%s\n' "$UPDATED" > "$SCHEDULE_BODY"
  lightning api "/v1/projects/$PID/schedules/$SID" -X PUT --input "$SCHEDULE_BODY"
  lightning api "/v1/projects/$PID/schedules/$SID"
)
```

Do not invent a `schedule run` endpoint for pipelines: the similarly
named endpoint under `/cloudspaces/.../schedules/.../runs` belongs to Studios.

## Stop a run or remove a schedule

These are separate operations. Run only the one the user requested; stopping an
execution leaves future schedules active, and deleting a schedule does not stop
an execution already started.

```bash
# Stop the selected execution, then poll its state until terminal.
lightning api "/v1/projects/$PID/pipelines/$RUN" -X PUT -f state=PIPELINE_STATE_STOP
```

```bash
# Remove the selected schedule, then verify its absence from the schedule list.
lightning api "/v1/projects/$PID/schedules/$SID" -X DELETE
```

## Cleanup

For a disposable test, delete its schedules first, request stop for its active
runs, wait for terminal states, then delete the test runs and root. Save evidence
before deletion. Enumerate scheduled children through `parentPipelineId` for each
known version as well as the root listing; do not assume root deletion removes
children omitted from that listing. Delete those exact children first and verify
both listings are empty. Do not delete a user's workflow to stop
one execution. Deleting a pipeline can delete its run history.

```bash
SCHEDULED_RUN='verified-test-child-id'
lightning api "/v1/projects/$PID/pipelines/$SCHEDULED_RUN" -X DELETE
lightning api "/v1/projects/$PID/pipelines/$ROOT" -X DELETE
lightning api "/v1/projects/$PID/pipelines" -X GET -f originalPipelineId="$ROOT"
lightning api "/v1/projects/$PID/schedules" -X GET -f parentResourceId="$ROOT"
lightning api "/v1/projects/$PID/pipelines" -X GET -f parentPipelineId="$VERSION"
```

