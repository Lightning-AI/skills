---
name: lightning-deployments
description: Deploy and operate services on Lightning AI - run Docker containers or HuggingFace models (vLLM) behind autoscaled HTTPS endpoints, manage replicas, releases, endpoint auth, and logs. Use when the user wants to deploy an API, model server, or container to lightning.ai, or manage an existing deployment.
---

# Lightning AI Deployments

A Deployment runs a container (or an auto-built vLLM server for a HuggingFace model) behind an HTTPS endpoint with autoscaling. Sources are mutually exclusive: `--image` (Docker), `--model` (HuggingFace id, GPU required), or `--studio` (deploy a Studio's environment).

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

## Resolving org and teamspace (do this first)

Deployments live in a teamspace owned by an organization or a user. **Never guess.** Use explicit `--teamspace owner/teamspace` (Python: `teamspace="owner/teamspace"`; the separate `org=`/`user=` arguments are deprecated), env vars `LIGHTNING_ORG` / `LIGHTNING_TEAMSPACE`, or the config default (`lightning config get teamspace`). If none is set, list the options and **ask the user which org/teamspace to use**:

```bash
lightning api /v1/memberships | jq -r '.memberships[] | [.ownerType, .name, .projectId] | @tsv'
```

Persist the choice: `lightning config set teamspace <owner>/<teamspace>`.

**From a scoped API key** (an agent with no user to ask): `--teamspace` needs the owner's *slug*,
while `/v1/memberships` only gives the owner's id, and the same teamspace can appear twice (an
`organization` row and a `user` row with one `projectId`). Continue only if a single teamspace is
listed, prefer its organization row, and resolve the owner by `ownerType` (needs `jq`):

```bash
M=$(lightning api /v1/memberships)
if [ "$(printf "%s" "$M" | jq '[.memberships[].projectId] | unique | length')" != 1 ]; then
  echo "several teamspaces: ask the user which one" >&2
else
  ROW=$(printf "%s" "$M" | jq -c '(.memberships | map(select(.ownerType=="organization"))) + .memberships | .[0]')
  TS=$(printf "%s" "$ROW" | jq -r .name); OID=$(printf "%s" "$ROW" | jq -r .ownerId)
  if [ "$(printf "%s" "$ROW" | jq -r .ownerType)" = organization ]; then
    OWNER=$(lightning api "/v1/orgs/$OID" | jq -r .name)
  else   # a personal teamspace; the search is fuzzy, so match the id exactly
    OWNER=$(lightning api /v1/users/search -X GET -f "query=$OID" \
      | jq -r --arg id "$OID" '.users[] | select(.id==$id) | .username')
  fi
  lightning config set teamspace "$OWNER/$TS"   # or pass --teamspace "$OWNER/$TS" each time
fi
```

## CLI reference

Subcommands: `create`, `list`, `inspect`, `update`, `delete`, `logs` (+ `logs download`), `reload-weights`, and `env` / `secret` (`list`/`set`/`delete` on a running deployment). There is no `stop` — stopping = scaling to zero via `update --min-replicas 0 --max-replicas 0`.

```bash
# container deployment (a --port is required for non-model deployments)
lightning deployment create my-api --teamspace owner/teamspace \
  --image nginx:latest --machine CPU --port 80 --replicas 1 --api-key-auth

# HuggingFace model via vLLM (GPU machine required; port defaults to 8000)
lightning deployment create llama --teamspace owner/teamspace \
  --model meta-llama/Llama-3.1-8B-Instruct --machine L40S \
  [--hf-token-secret <secret-name>] [--tensor-parallel-size 2] [--max-model-len 8192] \
  [--quantization fp8] [--dtype bfloat16] [--dry-run] [--force]

# cloud account — if you pass none, the SDK picks one (config `cloud-account`, then
# LIGHTNING_CLUSTER_ID, then the teamspace's default binding) and prints which on stderr.
# Other deployments in the same teamspace may sit on a different account, which
# `deployment list` shows in its last column; match it with --cloud when relevant.
lightning deployment create ... --cloud gcp-lightning-public-prod

# autoscaling / env / secrets
lightning deployment create ... \
  --min-replicas 0 --max-replicas 4 --autoscale-metric GPU --autoscale-threshold 90 \
  -e KEY=VALUE --secret MY_LIGHTNING_SECRET --interruptible   # CHECK THE SPOT PRICE FIRST — see Gotchas

# endpoint auth — mutually exclusive; OMITTING ALL THREE MAKES THE ENDPOINT PUBLIC
# Run `lightning auth whoami` first: --api-key-auth only accepts *user* keys.
  --api-key-auth                    # require a Lightning USER API key (Bearer) — see Gotchas
  --basic-auth USER:PASS
  --token-auth TOKEN                # any bearer string you choose; use this from a scoped key

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

dep = Deployment("my-api", teamspace="my-org/my-teamspace")   # owner/teamspace, same as the CLI; org=/user= are deprecated
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

# changing image/command/env/entrypoint/spot/... creates a NEW RELEASE and needs a release strategy
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
lightning auth whoami    # `user` → --api-key-auth is callable by you; `scoped-api-key` → use --token-auth
lightning deployment create hello-api --teamspace my-org/my-teamspace \
  --image nginx:latest --machine CPU --port 80 --min-replicas 0 --max-replicas 1 --api-key-auth
lightning deployment inspect hello-api --teamspace my-org/my-teamspace   # JSON: status, endpoint URL
URL=<endpoint URL from the inspect output above>
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $LIGHTNING_API_KEY" "$URL/"   # verify the ENDPOINT, not the Studio
```
```python
from lightning_sdk import Deployment
dep = Deployment("hello-api", teamspace="my-org/my-teamspace")
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
# review the model spec it prints, then re-run without --dry-run; if the real create reports
# unacknowledged warnings, re-run with --ack <code> (repeatable) or --force
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
PROJECT_ID=$(lightning api /v1/memberships | jq -r '.memberships[] | select(.name=="<teamspace>") | .projectId' | head -1)
lightning api "/v1/projects/${PROJECT_ID}/deployments" -q '.deployments[].name'   # plain GET; -f/-F without -X would send a POST
lightning api "/v1/projects/${PROJECT_ID}/deployments/${DEPLOYMENT_ID}"
lightning api "/v1/projects/${PROJECT_ID}/deployments/${DEPLOYMENT_ID}" -X DELETE
```

## Gotchas

- Omitting all auth flags creates a **publicly reachable endpoint** — confirm that's intended.
- **Check `lightning auth whoami` before picking an auth flag.** `--api-key-auth` gates the endpoint on a Lightning *user* key (it serializes to `userApiKey: true`), so an `Auth type: scoped-api-key` caller gets **401 on every request** to an endpoint that is otherwise healthy — replicas running, seconds billed. Use `--token-auth <token>` when a scoped key, CI job or agent is what will call the endpoint; `--api-key-auth` when humans with their own Lightning logins will. Verified against a control: a second, pre-existing healthy deployment returned an identical 401 to the same scoped key.
- **Verify a deployment against its public URL from `deployment inspect` — never against the app running inside the source Studio.** The Studio proves the image; only the endpoint proves the service. A deployment can show a healthy replica and bill normally while 401-ing every request.
- **`--interruptible` is not reliably cheaper — check the price before you pass it.** On GCP the L4 is **$0.48/hr on-demand but $0.727/hr spot**: interruptible costs 51% *more* and you take preemption risk for the privilege. Nothing warns you at create time. Fetch both rates from the accelerator catalog first (see the `lightning-cost-estimation` skill) and take `min(cost, spotPrice)`; spot is only reliably cheaper on some SKUs and clouds.
- `min_replicas=0` enables scale-to-zero and **genuinely stops billing at zero** — a teamspace credit balance stays flat while a deployment sits at zero replicas. The next request is served transparently: the edge accepts the connection and *holds it open* until a replica is ready (~6 minutes on a GPU image) and then returns a normal 200 — it does not return 503 or refuse, so any client without a short timeout just waits. `min_replicas >= 1` bills continuously — flag this cost to the user.
- **The idle window before scale-to-zero is SDK-only.** `AutoScaleConfig(idle_threshold_seconds=...)` has no equivalent flag on `deployment create` or `deployment update`, so from the CLI you get the default and cannot tune how long a replica lingers.
- **A health check is SDK-only too.** `HttpHealthCheck(path=..., port=...)` exists in Python but there is no `--health-check-path`/`--readiness-probe` flag, so a CLI-created deployment has nothing stopping traffic reaching a replica that is up but not ready.
- Changing image, command, env, entrypoint, path mappings, health check, cloud account, spot, quantity or max runtime forces a new release (switching the machine alone does not). The SDK raises `RuntimeError` if the deployment has no release strategy, neither passed nor already stored; the CLI auto-adds a rolling update.
- `Deployment.start()` on an existing deployment silently becomes an update/restart — it won't error on a name collision.
- A port is mandatory for non-model deployments (`ValueError` otherwise); `--model` defaults to 8000 and requires a GPU machine. In the SDK `ports` must be a **list** — passing an int fails with `TypeError: 'int' object is not iterable` raised from inside the SDK, which does not mention `ports`.
- `AutoScaleConfig` accepts no `metric` at construction, then `start()` raises `ValueError: The autoscaling metric is required. Currently supported metrics are ['GPU', 'CPU', 'RPM']`. Always pass `metric=`; the traceback points at `start()`, not at the config object.
- A private image needs `image_secret_ref`, which no CLI flag or SDK argument sets — see [Private container images](#private-container-images). The failure is an opaque `500`.
- `Secret("NAME")` serializes with an empty `name` field, so `deployment inspect` shows `{"from_secret": "NAME", "name": "", "value": ""}`. That is not a bug: the platform resolves the reference and names the environment variable after the secret. Verified by reading `env` inside a running replica.
- Deployment logs are readable by anyone with teamspace access. A container that echoes its environment on boot will therefore leak secret values into `deployment logs` — do not debug secret injection that way on a real secret.
- Deleting is destructive and the CLI prompts unless `--yes`; confirm with the user first. **`Deployment deleted` is not "gone"** — the deployment keeps appearing in `deployment list` (and `list --all`) for 90 seconds to ~2.5 minutes afterwards, sometimes in `PENDING` with a stale replica count and `deletedAt: None`. Don't treat a post-delete listing as a failed delete, and don't poll `list` to confirm teardown; re-issue `delete` only if it is still there after a few minutes.
- **`deployment logs` without `-f` can hang indefinitely.** A bounded read (`--tail 25`, no `--follow`) has been observed producing no output at all and never returning, so a scripted log fetch needs its own timeout. `-f` works.
- **There is no cost surface for a deployment.** `deployment inspect` exposes `total_cost`, but it reads `0.0` even after ~34 minutes of L4 GPU time (~$0.40 of real spend), and stays 0 for the deployment's whole life — unlike jobs, whose `total_cost` does populate. No billing/usage endpoint resolves either. The only way to see what a deployment cost is to difference the teamspace credit balance before and after, and that field flips between full float precision and 2-decimal rounding between consecutive calls — so difference over a window long enough that the rounding is noise.
- `--model` deployments may return validation warnings on create (`Deployment has unacknowledged warnings: ... Re-run with --ack <code> (repeatable) or --force.`). `--dry-run` never calls the server, so it never shows those warnings: it only echoes the model spec you set (`served_model_name`, `weight_source` and any vLLM flags you passed), not the machine, image variant or replica config.
- **`--model` may be gated on your account, and the refusal is an opaque `403`.** `deployment create ... --model <hf-id>` can fail with `Exception: The jobs_service_create_deployment_with_http_info request failed to reach the server, response: 403.` — no server message, and `LIGHTNING_DEBUG=1` adds a traceback but still no reason. It is an entitlement, not a bad argument, so don't debug the model id or flags. Fall back to deploying a vLLM container image directly (`--image`), which needs no entitlement.
- In the Python SDK, `teamspace=` takes `"owner/teamspace"` like the CLI. The separate `org=`/`user=` arguments are deprecated (`DeprecationWarning`), and passing both forms raises `ValueError`. On SDKs older than 2026.7.31, the combined form failed with "Teamspace owner/name does not exist"; upgrade rather than splitting it.
