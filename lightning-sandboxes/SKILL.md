---
name: lightning-sandboxes
description: Run code in Lightning AI Sandboxes - fast, isolated ephemeral VMs with optional persistence, snapshots, custom images, Docker-in-sandbox (docker / docker compose), network egress policies, file I/O, public port URLs, and interactive PTY sessions. Use when the user wants to execute untrusted or experimental code safely, needs a throwaway cloud VM, wants to build/run containers or preview a containerized app, or asks about lightning.ai sandboxes or snapshots.
---

# Lightning AI Sandboxes

A Sandbox is a fast-booting isolated VM for code execution. Ephemeral by default (`persistent=True` enables stop/resume via auto-snapshots). Import from the subpackage — `from lightning_sdk.sandbox import Sandbox` (the top-level `lightning_sdk.sandbox.py` `_Sandbox` class is legacy; ignore it).

## Setup & auth (sandbox-specific)

```bash
uvx lightning-sdk --version    # CLI; `sandbox <cmd>` is also installed standalone == `lightning sandbox <cmd>`
```

**Org scope comes from the API key — there is no org flag or `LIGHTNING_ORG_ID` env var (it's rejected).** Sandboxes need an **org- or teamspace-scoped API key** in `LIGHTNING_SANDBOX_API_KEY`; a personal `lightning login` credential fails with *"Use a teamspace- or org-scoped API key (Members → API keys), not your personal login key."*

You can mint an org-scoped key from the CLI (requires working personal auth):

```bash
export LIGHTNING_SANDBOX_API_KEY=$(lightning api-key create --org my-org --name sandbox-agent)
# or reuse the org default key: lightning api-key get --org my-org
```

If the user belongs to multiple orgs and none is configured, **ask which org to use**:

```bash
lightning api /v1/memberships | jq -r '.memberships[] | select(.ownerType=="organization") | [.ownerId, .name] | @tsv'
```

Snapshot/stop of persistent sandboxes needs a **teamspace-scoped** key (org-scoped is not enough).

## CLI reference

```bash
# create — blocks until running (usually seconds)
lightning sandbox create --name devbox \
  [--instance-type cpu-1]            # default cpu-1 \
  [--runtime python313]              # DEFAULT IS node24 (Node.js, NO python!) — use python313 for Python \
                                     # node22 | node24 | python313, each with a -docker variant \
  [--image ghcr.io/org/img:latest]   # custom rootfs, CPU-only; mutually exclusive with --runtime \
  [--image-secret-ref <docker-registry-secret>] \
  [--docker]                         # dockerd at boot; == appending -docker to --runtime \
  [--teamspace owner/teamspace] [--persistent] [--spot] [--port 8000] \
  [--snapshot-id snap-...]           # warm start from a snapshot \
  [--timeout 3600000]                # auto-stop lifetime in MILLISECONDS \
  [--json]                           # prints the sandbox id — capture it for later commands

lightning sandbox list [--teamspace owner/teamspace] [--limit N] [--json]

# run a command (use -- before the command); CLI exits with the command's exit code
lightning sandbox run <SANDBOX_ID> [--cwd /workspace] [--env KEY=VALUE] -- python -c "print('hi')"
lightning sandbox run <SANDBOX_ID> --detached -- bash -lc "long-task"   # prints cmd_id
lightning sandbox command  <SANDBOX_ID> <COMMAND_ID>     # status + output
lightning sandbox logs     <SANDBOX_ID> <COMMAND_ID>
lightning sandbox commands <SANDBOX_ID>                  # history

# lifecycle
lightning sandbox stop   <SANDBOX_ID>    # persistent: auto-snapshot + pause; ephemeral: == delete
lightning sandbox start  <SANDBOX_ID>    # resume a stopped persistent sandbox
lightning sandbox delete <SANDBOX_ID> -y # destroys sandbox AND its auto-snapshot; -y required non-interactively

# snapshots (filesystem only — running processes are not preserved)
lightning sandbox snapshot create <SANDBOX_ID> [--expiration <MS>] [--exclude PATH]
lightning sandbox snapshot list [--name N] [--teamspace owner/teamspace]
lightning sandbox snapshot get <SNAPSHOT_ID>
lightning sandbox snapshot delete <SNAPSHOT_ID> -y
```

There is no `cp`/upload CLI — move files via `run` with shell commands, or the SDK file API below.

## Python SDK

```python
from lightning_sdk.sandbox import Sandbox, SandboxConfig, RunCommandOpts, NetworkPolicy

# optional explicit config; otherwise env (LIGHTNING_SANDBOX_API_KEY) / lightning login creds are used
Sandbox.configure(api_key="...")

sb = Sandbox.create(
    name="devbox",
    instance_type="cpu-1",
    runtime="python313",                   # default is node24 (Node.js only — no python!)
    teamspace="owner/teamspace",
    persistent=True,                       # enables stop()/resume()
    docker=True,                           # dockerd at boot; see "Docker inside a sandbox"
    ports=[8080],                          # exposes public HTTPS URLs for these ports
    network_policy=NetworkPolicy(allow_cidrs=["10.0.0.0/8"]),  # or "deny-all" / default open egress
    timeout=30 * 60 * 1000,                # auto-stop, MILLISECONDS
)                                          # blocks until running

# commands — non-detached blocks until exit
cmd = sb.run_command("python -c 'print(42)'")
print(cmd.exit_code, cmd.output)           # output = combined stdout+stderr
bg = sb.run_command(RunCommandOpts(cmd="python", args=["train.py"], cwd="/workspace",
                                   env={"MODE": "test"}, detached=True))
bg.wait(timeout=600); print(bg.output)     # or sb.kill_command(bg.cmd_id)

# files — content strings via REST; no host<->sandbox copy helper
sb.write_file("/workspace/app.py", "print('hi')")
text = sb.read_file("/workspace/out.txt")  # None if missing
sb.fs.mkdir("/workspace/data", recursive=True)
sb.fs.exists("/workspace/app.py"); sb.fs.readdir("/workspace"); sb.fs.rm("/tmp/x", recursive=True)

# exposed ports (only those passed as ports=[...] at create) — proxied public HTTPS
sb.port_urls                               # {"8080": "https://8080-<sandbox-id>-s.cloudspaces.litng.ai"}
sb.get_port_url(8080)                      # ValueError if 8080 wasn't exposed at create
# normally populated by create; if empty, re-get: Sandbox().get(sb.sandbox_id).port_urls

# lifecycle
sb.extend_timeout(10 * 60 * 1000)          # heartbeat; milliseconds, min 1000
snap = sb.snapshot()                       # filesystem snapshot; later: Sandbox.create(snapshot_id=snap.id)
auto_snap_id = sb.stop()                   # persistent only; sb.resume() brings it back with same id
sb.delete()                                # ALWAYS clean up — GC does not delete remote sandboxes

# find existing
client = Sandbox()
for s in client.list(teamspace="owner/teamspace").sandboxes: print(s.sandbox_id, s.status)
sb = client.get("sbx-...")
```

Interactive PTY (needs `pip install websocket-client`): `sb.process.create_pty(PtyCreateOpts(session_name="main"))` → `pty.send_input("ls\n")`, `pty.wait()`. PTY exit codes are unreliable (0/-1/None only) — prefer `run_command` when you need exit codes.

## Docker inside a sandbox

The `-docker` runtimes ship Docker (engine, CLI, buildx, compose) with **`dockerd` already running** by the time create returns — nothing to install, no daemon to bootstrap, `docker version` works immediately. Pick the variant explicitly, or use `--docker` / `docker=True` to append the suffix to the base runtime you asked for:

```bash
lightning sandbox create --runtime python313-docker --port 8080 --json   # cpu-1 default is fine for Docker
lightning sandbox create --runtime python313 --docker   # identical — resolves to python313-docker
lightning sandbox create --docker                       # no runtime -> node24-docker (Node, NOT Python)
```

Variants: `node22-docker`, `node24-docker`, `python313-docker`; anything else fails with `invalid runtime: <id>`. `docker=True` is rejected together with `image` (a custom image opts in itself by carrying the OCI label `ai.lightning.sandbox.docker=true`) or `snapshot_id` (the runtime comes from the snapshot).

### Where images are stored, and how much room you get

Pick the instance type for where `/var/lib/docker` lands, not for its RAM: **`cpu-1` and `cpu-4` keep images on disk, every other shape keeps them in RAM.** That makes the default `cpu-1` a usable Docker box — a 1.1 GB image pulls fine on a sandbox with 1.25 GiB of RAM, because the image never touches memory — while the larger `cpu-2` gives you more RAM but less image room.

| `--instance-type` | RAM       | `/var/lib/docker` | image room | `storage_gb` raises it? |
| ----------------- | --------- | ----------------- | ---------- | ----------------------- |
| `cpu-1` (default) | 1.25 GiB  | disk              | 5 GB       | yes                     |
| `cpu-2`           | 8.75 GiB  | RAM (tmpfs)       | 4.4 GB     | no                      |
| `cpu-4`           | 18.75 GiB | disk              | 40 GB      | yes                     |
| `cpu-8`           | 38.75 GiB | RAM (tmpfs)       | 20 GB      | no                      |
| `cpu-16`          | 77.5 GiB  | RAM (tmpfs)       | 39 GB      | no                      |

Those are the current defaults, measured; treat them as a planning guide and confirm the live number with `df -h /var/lib/docker` before sizing a pull.

- **On the disk shapes there is one quota for everything** — `/`, `/tmp` and `/var/lib/docker` draw on the same pool, so a 3 GB file in `/root` leaves 3 GB less for images. Raise it with the SDK-only `storage_gb` (`Sandbox.create(..., instance_type="cpu-1", storage_gb=20)` gives docker 20 GB); there is no CLI flag for it.
- **On the RAM shapes the cap is half the sandbox's memory** and those bytes count against your memory budget, so an image plus a hungry process can still push the sandbox into an out-of-memory kill. `storage_gb` buys disk for `/`, never image room.
- **`df -h /var/lib/docker` reports the real ceiling** — trust it over any figure in this table. Older clusters report a large meaningless size instead: if you see hundreds of GB, nothing is bounding docker but the sandbox's own memory, and overrunning it kills the sandbox rather than failing the pull — keep images well under the RAM column.

**Rules of thumb:** `cpu-1` for anything image-heavy on a budget, `cpu-4` when you want both room (40 GB) and RAM for builds, `storage_gb` when 5 GB is tight. Reach for `cpu-2`/`cpu-8` only when the workload itself needs the memory, and keep images small there.

Running out of space is now an ordinary error rather than a fatality — a pull into a full data root fails with `no space left on device` and **the sandbox keeps running**, so you can delete images and retry:

```bash
lightning sandbox run $SBX -- bash -lc 'docker system df; docker image prune -af; df -h /var/lib/docker'
```

### Networking — the part you must adapt

`dockerd` runs `--bridge=none --iptables=false --ip6tables=false`, so only the `host` and `none` docker networks exist (`docker network ls` confirms it):

- **Run containers with `--network=host`.** Without it a container has no network at all — DNS fails (`wget: bad address`). Image *pulls* still work either way, since dockerd itself sits on the sandbox's netstack.
- **Build with `--network=host`** (compose: `build: {context: ., network: host}`), or every `RUN` step dies with `failed to solve: ... network bridge not found`.
- **Container→container over `127.0.0.1:<port>`**, never a compose service name — host networking means no bridge DNS, so `db` or `api` dies with `gaierror: [Errno -2] Name or service not known`. Plain `localhost` resolves fine; it is the service names that don't exist.
- **`-p 8080:80` is silently ignored** — port publishing belongs to the bridge, and host networking has none. The container is already on the sandbox's netstack, so the process inside must *listen on* the port you want (`nginx` configured for 8080, `uvicorn --port 8080`); remapping it from the outside is not possible. `docker run -p` exits 0 and serves nothing.
- A host-networked container listening on `:8080` is what the sandbox's public URL for 8080 serves — declare `--port 8080` / `ports=[8080]` at create, then `sb.get_port_url(8080)`.

Compose template that works as-is (FastAPI + Redis; run `docker compose up -d --build` detached with a generous timeout, since builds pull layers):

```yaml
services:
  web:
    build: {context: ., network: host}    # host net so pip install reaches PyPI
    network_mode: host
    environment: [REDIS_HOST=127.0.0.1]   # NOT "redis" — no bridge DNS
    depends_on: [redis]
  redis:
    image: redis:7-alpine
    network_mode: host
```

### Docker gotchas

- **Where images live depends on the instance type** — disk on `cpu-1`/`cpu-4`, RAM everywhere else — and it decides both your ceiling and whether `storage_gb` helps. See [Where images are stored](#where-images-are-stored-and-how-much-room-you-get); getting this wrong is the difference between a 5 GB budget and a 4.4 GB one that also competes with your processes.
- **A full data root fails the pull, it no longer kills the sandbox.** Both backings return `no space left on device` and stay up. If you are on an older cluster you may still hit the historical behaviour, where the sandbox was terminated and every later call returned `429 sandbox terminated: out of memory` — if you see that, you overran the data root, so drop to a smaller image or move to `cpu-1`/`cpu-4`.
- **Restricted egress and image pulls don't mix.** Under `network_policy="deny-all"` dockerd still starts, but every pull fails after a long stall with `Get "https://registry-1.docker.io/v2/": context deadline exceeded`. A CIDR allowlist doesn't rescue it: allowlisting the resolvers (`1.1.1.1/32`, `8.8.8.8/32`, see the DNS gotcha below) does fix name resolution, and the pull then dies waiting on registry IPs you would have to enumerate — which Docker Hub serves from a shifting CDN. Bake the images into a custom image, or pull under the default open egress.
- Docker Engine is pinned to **v27** (`docker info` shows the exact build) and v28+ is known-broken in this environment — don't upgrade it in-sandbox, it is unsupported. Docker is CPU-sandbox only, never GPU.
- On a plain runtime, `docker` and `dockerd` are **absent** and `mount` returns `permission denied`, so you cannot add Docker after the fact — choose a `-docker` runtime at create time.
- There is no `docker` field on the REST API. If you drive `/v1/core/sandboxes` directly, send `"runtime": "node24-docker"`.

## Example workflows

Prompts this skill handles: *"run this untrusted script somewhere safe"*, *"test my code in a clean VM"*, *"spin up a container from ghcr.io/... and poke around"*, *"give me a devbox that survives restarts"*.

**Safely execute untrusted/generated code (no network egress, auto-cleanup):**

```python
from lightning_sdk.sandbox import Sandbox

sb = Sandbox.create(name="quarantine", teamspace="my-org/my-teamspace", runtime="python313",
                    network_policy="deny-all", timeout=15 * 60 * 1000)  # hard kill after 15 min
try:
    sb.write_file("/workspace/suspect.py", open("suspect.py").read())
    cmd = sb.run_command("python /workspace/suspect.py")
    print(cmd.exit_code, cmd.output)
finally:
    sb.delete()
```

**Test code in a clean environment from the CLI:**

```bash
SBX=$(lightning sandbox create --name test-run --teamspace my-org/my-teamspace --runtime python313 --timeout 1800000 --json | jq -r .id)
lightning sandbox run $SBX -- python --version
lightning sandbox run $SBX --cwd /workspace -- bash -lc "pip install requests && python -c 'import requests; print(requests.__version__)'"
lightning sandbox run $SBX --detached -- bash -lc "pytest -q > /workspace/test.log 2>&1"   # prints cmd_id
lightning sandbox command $SBX <cmd_id>          # poll status + output
lightning sandbox delete $SBX -y
```

**Run a containerized app and preview it on a public URL:**

```bash
SBX=$(lightning sandbox create --name preview --runtime python313-docker \
  --port 8080 --timeout 1800000 --json | jq -r .id)     # cpu-1 default: images go to disk

# --network=host is mandatory, and the container must LISTEN on 8080 (-p is ignored)
lightning sandbox run $SBX -- docker run -d --network=host --name web \
  -v /workspace:/srv -w /srv python:3.13-alpine python -m http.server 8080

lightning sandbox run $SBX -- curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080
lightning sandbox list --json | jq -r ".sandboxes[] | select(.id==\"$SBX\") | .port_urls[\"8080\"]"
lightning sandbox delete $SBX -y
```

**Try out a custom image:**

```bash
lightning sandbox create --name img-test --image ghcr.io/myorg/myimage:latest \
  --teamspace my-org/my-teamspace --timeout 1800000
# private registry: --image-secret-ref <docker-registry-secret-name>
```

**Persistent devbox with snapshot-based branching:**

```python
sb = Sandbox.create(name="devbox", teamspace="my-org/my-teamspace", runtime="python313", persistent=True)
sb.run_command("pip install torch numpy pandas")     # slow setup, do once
snap = sb.snapshot()                                 # golden image
sb.stop()                                            # pause billing; sb.resume() later, same id
fresh = Sandbox.create(name="experiment-1", snapshot_id=snap.id,
                       teamspace="my-org/my-teamspace")   # warm clone with deps preinstalled
```

## Raw API fallback

```bash
lightning api /v1/core/sandboxes -X GET -f "organizationId=${ORG_ID}" -f "projectId=${PROJECT_ID}" -f limit=20
lightning api /v1/core/sandboxes -X GET ... | jq -r '.sandboxes[] | .name // .id'
lightning api "/v1/core/sandboxes/${SANDBOX_ID}" -f "organizationId=${ORG_ID}"
lightning api "/v1/core/sandboxes/${SANDBOX_ID}/commands" -X POST -f command=ls -F detached=false
```

`ORG_ID` from memberships: `lightning api /v1/memberships | jq -r '[.memberships[] | select(.ownerType == "organization") | .ownerId][0]'`.

## Gotchas

- **Timeout units differ:** `create --timeout` / `extend_timeout()` / snapshot `--expiration` are **milliseconds**; `sandbox run --timeout` (detached wait) is **seconds**.
- Sandboxes keep billing until stopped/deleted and are not cleaned up by garbage collection — always `stop()`/`delete()`, and set a create-time `timeout` as a safety net.
- **`sandbox delete` and `sandbox snapshot delete` prompt for confirmation — pass `-y`/`--yes` non-interactively.** Without it they read the prompt from a closed stdin, print `Are you sure you want to delete? [y/N]: Aborted.` and exit **without deleting**. Given the point above, a cleanup step that silently aborts leaves the sandbox billing indefinitely — this is the single easiest way to leak money here.
- **A CIDR allowlist does not implicitly permit DNS.** `allow_cidrs` is enforced at the IP layer, and the sandbox's `/etc/resolv.conf` points at `1.1.1.1` and `8.8.8.8` — outside any realistic application allowlist — so every hostname lookup fails with `Temporary failure in name resolution` while raw-IP connections work. Include the resolver addresses (`1.1.1.1/32`, `8.8.8.8/32`) in the allowlist, or point the sandbox at a resolver inside it.
- **Files restored from a snapshot come back with mtime `1970-01-01T00:00:00Z`.** Epoch-zero timestamps break `make`, `ccache`, and pip/setuptools staleness checks — which bites hardest in the pre-baked-environment use case snapshots exist for. Touch files you depend on, or avoid mtime-based staleness logic in a restored sandbox.
- **Egress policy is Python-SDK-only.** `sandbox create` has no network-policy flag, so `deny-all` and CIDR allowlists require dropping into `lightning_sdk`; everything else here (create, run, snapshot, list, delete) is CLI-doable.
- **There is no cost surface for sandboxes.** `/v1/billing/usage`, `/v1/projects/<pid>/usage` and `/v1/core/sandboxes/<id>/usage` all 404, no CLI reports spend, and sandbox instance types (`cpu-1`, `cpu-2`) don't appear in the priced `lightning machine list` catalog — so a sandbox run cannot be priced, even by hand. Budget with a create-time `timeout` rather than by measuring after.
- Ephemeral (default) sandboxes lose everything on stop; only `persistent=True` gives stop/resume. Snapshots capture the filesystem only, never running processes.
- **The default runtime is `node24` — Node.js only, no Python.** Pass `runtime="python313"` / `--runtime python313` for Python workloads (naming: `node22`, `node24`, `python313`, plus the `-docker` variants; invalid ids fail with "invalid runtime"). `image` (custom rootfs) is CPU/gVisor-only and mutually exclusive with `runtime`; private images need `image_secret_ref` pointing at a Docker-registry secret.
- To run containers inside a sandbox, use a `-docker` runtime (`--runtime python313-docker`) or `docker=True` / `--docker` — never hand-install Docker on a plain runtime. See [Docker inside a sandbox](#docker-inside-a-sandbox), especially the `--network=host` requirement.
- Public port URLs only exist for ports declared at create time (`ports=[8080]` / `--port 8080`); there is no way to expose a port later. `create` normally returns them already populated in `port_urls`; if empty, re-`get` the sandbox.
- Network policy is **create-time only** — you cannot change egress rules on a running sandbox. Default is open egress (`allow-all`); use `"deny-all"` or CIDR allowlists for untrusted code.
- Commands run as **root** inside the sandbox.
- Error "organization_id is required" → the API key isn't org/teamspace-scoped; "API key is not authorized for this project" → the teamspace-scoped key is bound to a different teamspace.
