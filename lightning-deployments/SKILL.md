---
name: lightning-deployments
description: Deploy and operate services on Lightning AI - run Docker containers or HuggingFace models (vLLM) behind autoscaled HTTPS endpoints, manage replicas, releases, endpoint auth, and logs. Use when the user wants to deploy an API, model server, or container to lightning.ai, or manage an existing deployment.
---

# Lightning AI Deployments

A Deployment runs a container (or an auto-built vLLM server for a HuggingFace model) behind an HTTPS endpoint with autoscaling. Sources are mutually exclusive: `--image` (Docker), `--model` (HuggingFace id, GPU required), or `--studio` (deploy a Studio's environment).

## Setup & auth

```bash
uvx lightning-sdk --version         # CLI without installing; `lightning` == `lightning-sdk`
lightning login                     # browser flow; or headless:
export LIGHTNING_USER_ID=... LIGHTNING_API_KEY=...   # both required
```

Python snippets: `uv run --with lightning-sdk python script.py`.

## Resolving org and teamspace (do this first)

Deployments live in a teamspace owned by an organization or a user. **Never guess.** Use explicit `--teamspace owner/teamspace` (Python: `Teamspace(name, org=...)` or `user=...`), env vars `LIGHTNING_ORG` / `LIGHTNING_TEAMSPACE`, or the config default (`lightning config get teamspace`). If none is set, list the options and **ask the user which org/teamspace to use**:

```bash
lightning api /v1/memberships | jq -r '.memberships[] | [.ownerType, .name, .projectId] | @tsv'
```

Persist the choice: `lightning config set teamspace <owner>/<teamspace>`.

## CLI reference

Subcommands: `create`, `list`, `inspect`, `update`, `delete`, `logs`, `reload-weights`. There is no `stop` — stopping = scaling to zero via `update --min-replicas 0 --max-replicas 0`.

```bash
# container deployment (a --port is required for non-model deployments)
lightning deployment create my-api --teamspace owner/teamspace \
  --image nginx:latest --machine CPU --port 80 --replicas 1 --api-key-auth

# HuggingFace model via vLLM (GPU machine required; port defaults to 8000)
lightning deployment create llama --teamspace owner/teamspace \
  --model meta-llama/Llama-3.1-8B-Instruct --machine L40S \
  [--hf-token-secret <secret-name>] [--tensor-parallel-size 2] [--max-model-len 8192] \
  [--quantization fp8] [--dtype bfloat16] [--dry-run] [--force]

# autoscaling / env / secrets
lightning deployment create ... \
  --min-replicas 0 --max-replicas 4 --autoscale-metric GPU --autoscale-threshold 90 \
  -e KEY=VALUE --secret MY_LIGHTNING_SECRET --interruptible

# endpoint auth — mutually exclusive; OMITTING ALL THREE MAKES THE ENDPOINT PUBLIC
  --api-key-auth                    # require a Lightning API key (Bearer)
  --basic-auth USER:PASS
  --token-auth TOKEN

# operate
lightning deployment list --teamspace owner/teamspace [--all] [--sort-by state]
lightning deployment inspect my-api --teamspace owner/teamspace     # full JSON incl. endpoint URLs
lightning deployment logs my-api --teamspace owner/teamspace [-f] [--tail 100]
lightning deployment update my-api --teamspace owner/teamspace --max-replicas 8
lightning deployment update my-api --teamspace owner/teamspace --min-replicas 0 --max-replicas 0   # "stop"
lightning deployment update my-api --teamspace owner/teamspace --image myimg:v2 [--max-surge 1] [--max-unavailable 0]  # new release, rolling update
lightning deployment reload-weights llama --teamspace owner/teamspace   # hot-reload BYOM weights
lightning deployment delete my-api --teamspace owner/teamspace --yes
```

## Python SDK

```python
from lightning_sdk import Deployment, Machine
from lightning_sdk.api.deployment_api import (
    ApiKeyAuth, BasicAuth, TokenAuth, AutoScaleConfig, Env, Secret,
    RollingUpdateReleaseStrategy, HttpHealthCheck,
)

dep = Deployment("my-api", teamspace="my-teamspace", org="my-org")   # SDK takes the BARE teamspace name + org=/user= — "owner/name" strings are CLI-only
dep.start(
    image="nginx:latest",
    machine=Machine.CPU,
    ports=80,                                   # REQUIRED for non-model deployments
    autoscale=AutoScaleConfig(min_replicas=0, max_replicas=4, metric="CPU", threshold=90),
    env=[Env("MODE", "prod"), Secret("HF_TOKEN")],   # or a plain dict for env vars
    auth=ApiKeyAuth(),                          # None => PUBLIC endpoint
    health_check=HttpHealthCheck(path="/health", port=80),
    spot=False,                                 # True = interruptible replicas
)

print(dep.urls)                                 # endpoint URL(s); no .status property — use replica counts
print(dep.running_replicas, dep.pending_replicas, dep.failing_replicas)
r = dep.get(path="/health")                     # convenience HTTP; auto-auths only for ApiKeyAuth

# changing image/machine/command/env creates a NEW RELEASE and needs a release strategy
dep.update(image="nginx:1.27", release_strategy=RollingUpdateReleaseStrategy(max_surge=1, max_unavailable=0))
dep.update(min_replicas=1, max_replicas=8)      # scaling: no new release, no strategy needed
dep.stop()                                      # scales to 0; blocks until replicas reach 0
```

HuggingFace model serving via SDK: `dep.start(model="meta-llama/...", machine=Machine.L40S, ports=8000, hf_token_secret="...")` — `image`/`studio` must be None; still pass `ports`.

**Trap:** `dep.delete()` does NOT delete the deployment — it is an HTTP helper like `dep.get()`/`dep.post()` and sends an HTTP `DELETE` request to the deployed service's endpoint. To delete the deployment resource use `lightning deployment delete NAME --yes` or the raw API.

## Example workflows

Prompts this skill handles: *"deploy this docker image behind an API"*, *"serve Llama 3.1 8B on lightning"*, *"scale my deployment down at night"*, *"roll out the new image version"*.

**Deploy a container, verify it responds, then clean up:**

```bash
lightning deployment create hello-api --teamspace my-org/my-teamspace \
  --image nginx:latest --machine CPU --port 80 --min-replicas 0 --max-replicas 1 --api-key-auth
lightning deployment inspect hello-api --teamspace my-org/my-teamspace   # JSON: status, endpoint URL
```
```python
from lightning_sdk import Deployment
dep = Deployment("hello-api", teamspace="my-teamspace", org="my-org")   # bare name + org, not "owner/name"
print(dep.urls)                       # e.g. ['https://80-dep-<id>-d.cloudspaces.litng.ai'] — available even while scaled to zero
print(dep.get(path="/").status_code)  # sends the caller's Lightning API key for ApiKeyAuth
```
```bash
lightning deployment delete hello-api --teamspace my-org/my-teamspace --yes
```

**Serve an open-weights LLM (vLLM, built server-side — no Dockerfile needed):**

```bash
lightning deployment create qwen-served --teamspace my-org/my-teamspace \
  --model Qwen/Qwen2.5-7B-Instruct --machine L40S --min-replicas 0 --max-replicas 1 --api-key-auth --dry-run
# review the resolved vLLM config, then re-run without --dry-run (add --ack <code> if warnings are listed)
lightning deployment logs qwen-served --teamspace my-org/my-teamspace -f   # watch it come up
```

The endpoint serves an OpenAI-compatible API on port 8000 — point any OpenAI client's `base_url` at `dep.urls[0]`.

**Operate: scale, update, roll back traffic costs:**

```bash
lightning deployment update hello-api --teamspace my-org/my-teamspace --min-replicas 1 --max-replicas 4   # keep warm
lightning deployment update hello-api --teamspace my-org/my-teamspace --min-replicas 0 --max-replicas 0   # stop (scale to zero)
lightning deployment update hello-api --teamspace my-org/my-teamspace --image nginx:1.27                  # new release, rolling by default
```

## Raw API fallback

```bash
PROJECT_ID=$(lightning api /v1/memberships | jq -r '.memberships[0].projectId')
lightning api "/v1/projects/${PROJECT_ID}/deployments" -F limit=20 -q '.deployments[].name'
lightning api "/v1/projects/${PROJECT_ID}/deployments/${DEPLOYMENT_ID}"
lightning api "/v1/projects/${PROJECT_ID}/deployments/${DEPLOYMENT_ID}" -X DELETE
```

## Gotchas

- Omitting all auth flags creates a **publicly reachable endpoint** — confirm that's intended; default to `--api-key-auth` otherwise.
- `min_replicas=0` enables scale-to-zero (idle replicas stop; cold start on next request, tunable via `AutoScaleConfig(idle_threshold_seconds=...)`). `min_replicas >= 1` bills continuously — flag this cost to the user.
- Changing image/machine/command/env/entrypoint/spot forces a new release; the SDK raises `RuntimeError` if no `release_strategy` is passed (the CLI auto-adds a rolling update).
- `Deployment.start()` on an existing deployment silently becomes an update/restart — it won't error on a name collision.
- A port is mandatory for non-model deployments (`ValueError` otherwise); `--model` defaults to 8000 and requires a GPU machine.
- Deleting is destructive and the CLI prompts unless `--yes`; confirm with the user first.
- `--model` deployments may return validation warnings; re-run with `--ack <code>` or `--force`, and use `--dry-run` to preview the resolved vLLM config.
- In the Python SDK, `teamspace=` must be the bare teamspace name with `org=`/`user=` passed separately; `Deployment("x", teamspace="owner/name")` fails with "Teamspace owner/name does not exist" — and in headless (env-var auth) runs that error is masked by a misleading "Neither name is provided nor can the user be inferred from the environment variable!".
