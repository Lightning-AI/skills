# Studio and multi-step pipelines

Read this when the workflow uses a Studio snapshot, shared files, explicit dependencies,
multi-machine jobs or deployment steps. Resolve the teamspace and cloud using
[the skill](../SKILL.md) first.

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

