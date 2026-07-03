---
name: lightning-sandboxes
description: Run code in Lightning AI Sandboxes - fast, isolated ephemeral VMs with optional persistence, snapshots, custom images, network egress policies, file I/O, and interactive PTY sessions. Use when the user wants to execute untrusted or experimental code safely, needs a throwaway cloud VM, or asks about lightning.ai sandboxes or snapshots.
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
  [--image ghcr.io/org/img:latest]   # custom rootfs, CPU-only; mutually exclusive with --runtime \
  [--image-secret-ref <docker-registry-secret>] \
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
lightning sandbox delete <SANDBOX_ID>    # destroys sandbox AND its auto-snapshot

# snapshots (filesystem only — running processes are not preserved)
lightning sandbox snapshot create <SANDBOX_ID> [--expiration <MS>] [--exclude PATH]
lightning sandbox snapshot list [--name N] [--teamspace owner/teamspace]
lightning sandbox snapshot get <SNAPSHOT_ID>
lightning sandbox snapshot delete <SNAPSHOT_ID>
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
lightning sandbox delete $SBX
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
- Ephemeral (default) sandboxes lose everything on stop; only `persistent=True` gives stop/resume. Snapshots capture the filesystem only, never running processes.
- **The default runtime is `node24` — Node.js only, no Python.** Pass `runtime="python313"` / `--runtime python313` for Python workloads (naming: `node22`, `node24`, `python313`; invalid ids fail with "invalid runtime"). `image` (custom rootfs) is CPU/gVisor-only and mutually exclusive with `runtime`; private images need `image_secret_ref` pointing at a Docker-registry secret.
- Network policy is **create-time only** — you cannot change egress rules on a running sandbox. Default is open egress (`allow-all`); use `"deny-all"` or CIDR allowlists for untrusted code.
- Commands run as **root** inside the sandbox.
- Error "organization_id is required" → the API key isn't org/teamspace-scoped; "API key is not authorized for this project" → the teamspace-scoped key is bound to a different teamspace.
