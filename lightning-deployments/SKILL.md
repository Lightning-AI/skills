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

**From a scoped API key** (an agent, no user to ask): `/v1/memberships` gives the teamspace name and the owner *id*, but `--teamspace` needs the owner *slug* — resolve it via `/v1/orgs` (needs `jq`). **Select the `organization` entry rather than `.memberships[0]`**: the same teamspace is commonly listed twice, once with `ownerType: organization` and once with `ownerType: user` (identical `projectId` and `name`), so index 0 is a coin flip and the `user` row's `ownerId` will not resolve against `/v1/orgs`.

```bash
M=$(lightning api /v1/memberships)
ROW=$(echo "$M" | jq -c '[.memberships[] | select(.ownerType=="organization")][0] // .memberships[0]')
TS=$(echo "$ROW" | jq -r .name)                                                        # teamspace
OWNER=$(lightning api "/v1/orgs/$(echo "$ROW" | jq -r .ownerId)" | jq -r .name)         # owner (org) slug
lightning config set teamspace "$OWNER/$TS"     # every command now defaults here; or pass --teamspace "$OWNER/$TS"
```

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

# cloud account — defaults to lightning-public-prod and prints a notice saying so.
# Other deployments in the same teamspace may sit on a different account, which
# `deployment list` shows in its last column; match it with --cloud when relevant.
lightning deployment create ... --cloud gcp-lightning-public-prod

# autoscaling / env / secrets
lightning deployment create ... \
  --min-replicas 0 --max-replicas 4 --autoscale-metric GPU --autoscale-threshold 90 \
  -e KEY=VALUE --secret MY_LIGHTNING_SECRET --interruptible   # CHECK THE SPOT PRICE FIRST — see Gotchas

# endpoint auth — mutually exclusive; OMITTING ALL THREE MAKES THE ENDPOINT PUBLIC
  --api-key-auth                    # require a Lightning API key (Bearer)
  --basic-auth USER:PASS
  --token-auth TOKEN

# operate
lightning deployment list --teamspace owner/teamspace [--all] [--sort-by state]
lightning deployment inspect my-api --teamspace owner/teamspace     # full JSON incl. endpoint URLs
lightning deployment logs my-api --teamspace owner/teamspace [-f] [--tail 100] [--timestamps]
lightning deployment logs my-api --teamspace owner/teamspace --query timeout --severity error   # filter server-side
lightning deployment logs my-api --teamspace owner/teamspace --since 30m --until 5m             # window (replicas merged + labelled)
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
    ports=[80],                                 # REQUIRED for non-model deployments; a LIST, not an int
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

HuggingFace model serving via SDK: `dep.start(model="meta-llama/...", machine=Machine.L40S, ports=[8000], hf_token_secret="...")` — `image`/`studio` must be None; still pass `ports`.

### Private container images

A deployment whose image needs registry credentials must set
`V1JobSpec.image_secret_ref` to the name of a `SECRET_TYPE_DOCKER_REGISTRY`
secret in the teamspace. **Neither the CLI nor `Deployment.start()` exposes
this**, and the server rejects an unpullable image with a bare
`500` — `Exception: The jobs_service_create_deployment_with_http_info request
failed to reach the server, response: 500.` — which names neither the image nor
the missing credential. Until the SDK exposes it, patch the field onto the
create request:

```python
from lightning_sdk.lightning_cloud.openapi.api import jobs_service_api

_create = jobs_service_api.JobsServiceApi.jobs_service_create_deployment_with_http_info

def _with_image_secret(self, body, project_id, **kwargs):
    if getattr(body, "spec", None) is not None:
        body.spec.image_secret_ref = "MY_REGISTRY_SECRET"
    return _create(self, body, project_id, **kwargs)

jobs_service_api.JobsServiceApi.jobs_service_create_deployment_with_http_info = _with_image_secret
```

List the teamspace's secrets, and their types, with:

```bash
PROJECT_ID=$(lightning api /v1/memberships | jq -r '.memberships[] | select(.name=="<teamspace>") | .projectId')
lightning api "/v1/projects/${PROJECT_ID}/secrets" | jq -r '.secrets[] | "\(.name) \(.type)"'
```

Diagnosing this 500 is much faster if you bisect against a **public** image
first (`nginx:latest`): if that create succeeds with otherwise identical
arguments, the image is the variable, not the machine, port, replica counts or
cloud account.

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
- **`--interruptible` is not reliably cheaper — check the price before you pass it.** On GCP the L4 is **$0.48/hr on-demand but $0.727/hr spot**: interruptible costs 51% *more* and you take preemption risk for the privilege. Nothing warns you at create time. Fetch both rates from the accelerator catalog first (see the `lightning-cost-estimation` skill) and take `min(cost, spotPrice)`; spot is only reliably cheaper on some SKUs and clouds.
- `min_replicas=0` enables scale-to-zero and **genuinely stops billing at zero** — a teamspace credit balance stays flat while a deployment sits at zero replicas. The next request is served transparently: the edge accepts the connection and *holds it open* until a replica is ready (~6 minutes on a GPU image) and then returns a normal 200 — it does not return 503 or refuse, so any client without a short timeout just waits. `min_replicas >= 1` bills continuously — flag this cost to the user.
- **The idle window before scale-to-zero is SDK-only.** `AutoScaleConfig(idle_threshold_seconds=...)` has no equivalent flag on `deployment create` or `deployment update`, so from the CLI you get the default and cannot tune how long a replica lingers.
- **A health check is SDK-only too.** `HttpHealthCheck(path=..., port=...)` exists in Python but there is no `--health-check-path`/`--readiness-probe` flag, so a CLI-created deployment has nothing stopping traffic reaching a replica that is up but not ready.
- Changing image/machine/command/env/entrypoint/spot forces a new release; the SDK raises `RuntimeError` if no `release_strategy` is passed (the CLI auto-adds a rolling update).
- `Deployment.start()` on an existing deployment silently becomes an update/restart — it won't error on a name collision.
- A port is mandatory for non-model deployments (`ValueError` otherwise); `--model` defaults to 8000 and requires a GPU machine. In the SDK `ports` must be a **list** — passing an int fails with `TypeError: 'int' object is not iterable` raised from inside the SDK, which does not mention `ports`.
- `AutoScaleConfig` accepts no `metric` at construction, then `start()` raises `ValueError: The autoscaling metric is required. Currently supported metrics are ['GPU', 'CPU', 'RPM']`. Always pass `metric=`; the traceback points at `start()`, not at the config object.
- A private image needs `image_secret_ref`, which no CLI flag or SDK argument sets — see [Private container images](#private-container-images). The failure is an opaque `500`.
- `Secret("NAME")` serializes with an empty `name` field, so `deployment inspect` shows `{"from_secret": "NAME", "name": "", "value": ""}`. That is not a bug: the platform resolves the reference and names the environment variable after the secret. Verified by reading `env` inside a running replica.
- Deployment logs are readable by anyone with teamspace access. A container that echoes its environment on boot will therefore leak secret values into `deployment logs` — do not debug secret injection that way on a real secret.
- Deleting is destructive and the CLI prompts unless `--yes`; confirm with the user first. **`Deployment deleted` is not "gone"** — the deployment keeps appearing in `deployment list` (and `list --all`) for 90 seconds to ~2.5 minutes afterwards, sometimes in `PENDING` with a stale replica count and `deletedAt: None`. Don't treat a post-delete listing as a failed delete, and don't poll `list` to confirm teardown; re-issue `delete` only if it is still there after a few minutes.
- **`deployment logs` without `-f` can hang indefinitely.** A bounded read (`--tail 25`, no `--follow`) has been observed producing no output at all and never returning, so a scripted log fetch needs its own timeout. `-f` works.
- **There is no cost surface for a deployment.** `deployment inspect` exposes `total_cost`, but it reads `0.0` even after ~34 minutes of L4 GPU time (~$0.40 of real spend), and stays 0 for the deployment's whole life — unlike jobs, whose `total_cost` does populate. No billing/usage endpoint resolves either. The only way to see what a deployment cost is to difference the teamspace credit balance before and after, and that field flips between full float precision and 2-decimal rounding between consecutive calls — so difference over a window long enough that the rounding is noise.
- `--model` deployments may return validation warnings; re-run with `--ack <code>` or `--force`, and use `--dry-run` to preview the resolved vLLM config — though `--dry-run` prints only `served_model_name` and `weight_source`, not the machine, resolved vLLM args, image variant or replica config.
- **`--model` may be gated on your account, and the refusal is an opaque `403`.** `deployment create ... --model <hf-id>` can fail with `Exception: The jobs_service_create_deployment_with_http_info request failed to reach the server, response: 403.` — no server message, and `LIGHTNING_DEBUG=1` adds a traceback but still no reason. It is an entitlement, not a bad argument, so don't debug the model id or flags. Fall back to deploying a vLLM container image directly (`--image`), which needs no entitlement.
- In the Python SDK, `teamspace=` must be the bare teamspace name with `org=`/`user=` passed separately; `Deployment("x", teamspace="owner/name")` fails with "Teamspace owner/name does not exist" — and in headless (env-var auth) runs that error is masked by a misleading "Neither name is provided nor can the user be inferred from the environment variable!".
