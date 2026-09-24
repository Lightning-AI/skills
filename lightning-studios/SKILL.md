---
name: lightning-studios
description: Manage Lightning AI Studios (cloud dev machines with CPUs/GPUs) - create, start, stop, delete studios, switch machine types, run commands in them, upload/download files, and SSH in. Use when the user wants to work with lightning.ai Studios, needs a cloud GPU dev box, or asks to run something "on a studio".
---

# Lightning AI Studios

A Studio is a persistent cloud development machine on [lightning.ai](https://lightning.ai). Its filesystem persists across sessions; compute (CPU/GPU) attaches on start and detaches on stop. Everything below uses the `lightning` CLI and `lightning_sdk` Python package.

## Setup & auth

```bash
# Use the Lightning AI CLI from the current env; install or upgrade it there if it's missing or older than 2026.9.18
v=$(lightning --version 2>/dev/null | sed -n 's/^Lightning CLI version //p')
[ -n "$v" ] && [ "$(printf '%s\n' 2026.9.18 "$v" | sort -V | head -1)" = 2026.9.18 ] \
  || uv pip install -U lightning-sdk || python3 -m pip install -U lightning-sdk
lightning --version   # must print "Lightning CLI version …"; if not, see the table below
lightning login                     # browser flow; or set env vars for headless use:
export LIGHTNING_API_KEY=... LIGHTNING_USER_ID=...   # USER_ID optional: with it Basic auth, without it the key is a Bearer token
```

This uses, and if needed installs into, the environment the agent runs in (the user's project
venv, a conda env, …). Then call plain `lightning …` everywhere. If setup doesn't go cleanly:

| Symptom | Fix |
|---|---|
| Both installs fail: no active env, or pip refuses with `externally-managed-environment` | Install it on its own instead: `uv tool install lightning-sdk` (or `pipx install lightning-sdk`), then call `"$(uv tool dir --bin)/lightning"` if that dir isn't on `PATH` |
| `lightning --version` still prints no `Lightning CLI version` line after installing | `command -v lightning` shows which one runs. Another tool owns the name (PyTorch Lightning also installs a `lightning` command), or the env you installed into isn't on `PATH`: activate it (`source .venv/bin/activate`) or call its `bin/lightning` by full path |
| `No such command`, `No such option` or `unexpected extra argument` | The CLI is older than this skill expects: `uv pip install -U lightning-sdk` (or `pip install -U lightning-sdk`) in the env `command -v lightning` points into; `uv tool upgrade lightning-sdk` for a uv tool install |
| The upgrade fails on a version conflict with the project's own pins | Don't fight the pins: install it outside the project with `uv tool install lightning-sdk` and call `"$(uv tool dir --bin)/lightning"` |
| Installing is blocked (read-only env or home, agent sandbox) | Skip it and run each command as `UV_CACHE_DIR="${TMPDIR:-/tmp}/uv" uvx lightning-sdk …` |
| Every call fails on SSL/certificates, even `lightning --version` (`Could not find a suitable TLS CA certificate bundle`), only inside an agent sandbox | A `**/*.pem` read-deny rule is hiding certifi's public CA bundle (`site-packages/certifi/cacert.pem`). Allow that one path; don't disable the sandbox |

Credentials are stored in `~/.lightning/credentials.json`.

The install above also makes `lightning_sdk` importable in that env, so Python snippets run with
its `python`. If the CLI came from a uv tool or `uvx` fallback instead, run one-off scripts with
`uv run --with lightning-sdk python script.py`. For code that stays in the user's project, ask,
then declare `lightning-sdk` as a dependency with the project's own tool (`uv add`, `poetry add`).

## Resolving org and teamspace (do this first)

Every studio lives in a teamspace owned by either an organization or a user. **Never guess.** Resolve in this order:

1. Explicit `--teamspace owner/teamspace` flag (owner = org or user name) / `Teamspace("owner/teamspace")` in Python (the separate `org=`/`user=` arguments are deprecated).
2. Env vars `LIGHTNING_ORG`, `LIGHTNING_TEAMSPACE`, `LIGHTNING_USERNAME`, or config defaults in `~/.lightning/config.yaml` (`lightning config get teamspace`).
3. Otherwise list the options and **ask the user which org/teamspace to use**:

```bash
lightning api /v1/memberships | jq -r '.memberships[] | [.ownerType, .name, .projectId] | @tsv'
```

Persist the user's choice so they aren't asked again: `lightning config set teamspace <owner>/<teamspace>`.

**From a scoped API key** (an agent, no user to ask): `/v1/memberships` gives the teamspace name and the owner *id*, but `--teamspace` needs the owner *slug* — resolve it via `/v1/orgs` (needs `jq`). **Select the `organization` entry rather than `.memberships[0]`**: the same teamspace is commonly listed twice, once with `ownerType: organization` and once with `ownerType: user` (identical `projectId` and `name`), so index 0 is a coin flip and the `user` row's `ownerId` will not resolve against `/v1/orgs`.

```bash
M=$(lightning api /v1/memberships)
ROW=$(echo "$M" | jq -c '[.memberships[] | select(.ownerType=="organization")][0] // .memberships[0]')
TS=$(echo "$ROW" | jq -r .name)                                                        # teamspace
OWNER=$(lightning api "/v1/orgs/$(echo "$ROW" | jq -r .ownerId)" | jq -r .name)         # owner (org) slug
lightning config set teamspace "$OWNER/$TS"     # every command now defaults here; or pass --teamspace "$OWNER/$TS"
```

## CLI reference

`--teamspace` always takes `owner/teamspace`. Omitting `--name`/`--teamspace` in an interactive terminal opens a picker menu; in scripts always pass them explicitly.

```bash
# create (registers the studio, does NOT attach compute)
lightning studio create --name my-studio --teamspace owner/teamspace [--cloud PROVIDER] [--studio-type TEMPLATE]

# start compute (blocking; --create makes it if missing)
lightning studio start --name my-studio --teamspace owner/teamspace --machine CPU [--create] [--interruptible]
lightning studio start --name my-studio --teamspace owner/teamspace --gpus L4:4    # --gpus and --machine are mutually exclusive

# manage
lightning studio list --teamspace owner/teamspace [--all] [--sort-by status]   # default lists only your studios
lightning studio switch --name my-studio --teamspace owner/teamspace --machine A100   # requires Running studio
lightning studio stop --name my-studio --teamspace owner/teamspace
lightning studio delete my-studio --teamspace owner/teamspace -y              # NAME is positional here; -y/--yes: required non-interactively

# ssh / one-shot connect (create + start + ssh)
lightning studio ssh --name my-studio --teamspace owner/teamspace
lightning studio connect my-studio --teamspace owner/teamspace --machine CPU

# machine names the CLI accepts (offline list; says nothing about availability)
lightning machine list [--json]
```

### Files: `lit://` paths

Studio paths use `lit://<owner>/<teamspace>/studios/<studio-name>/<path>`. Exactly one side of a copy must be `lit://`; directories need `-r`.

```bash
lightning cp ./train.py lit://owner/teamspace/studios/my-studio/train.py     # upload
lightning cp -r lit://owner/teamspace/studios/my-studio/logs/ ./logs         # download dir
lightning ls lit://owner/teamspace/studios/my-studio                         # list files (-r recursive, --json)
lightning rm lit://owner/teamspace/studios/my-studio/old.txt [-r] [-f]
```

## Python SDK

```python
from lightning_sdk import Machine, Studio, Teamspace

ts = Teamspace("my-org/my-teamspace")                  # owner/teamspace; org=/user= are deprecated
studio = Studio(name="my-studio", teamspace=ts, create_ok=True)  # create_ok=False -> error if missing

studio.start(machine=Machine.CPU)          # blocking; interruptible=True for spot pricing
print(studio.status)                       # NotCreated|Pending|Running|Stopping|Stopped|Completed|Failed

# run commands — studio must be Running
out = studio.run("nvidia-smi")                                  # raises RuntimeError on non-zero exit
out, code = studio.run_with_exit_code("pytest -q")              # no raise on non-zero
out, code = studio.run_and_detach("python train.py", timeout=30)  # keeps running in background

studio.upload_file("train.py", remote_path="train.py")          # remote path relative to studio home
studio.download_file("outputs/model.ckpt", "model.ckpt")
studio.upload_folder("./src"); studio.download_folder("outputs/")

studio.switch_machine(Machine.A100)        # change machine while running
studio.set_env({"WANDB_API_KEY": "..."})   # env vars; partial=True merges
studio.stop()                              # releases compute, keeps filesystem
studio.delete()
```

### Machine types

Pass as `Machine.<NAME>` or string (`Machine.from_str("A100")` accepts name or slug):

- CPU: `CPU_SMALL`, `CPU` (default, 4 cores), `CPU_X_2/4/8/16`; big-disk: `DATA_PREP`, `DATA_PREP_MAX`, `DATA_PREP_ULTRA`
- GPU: `T4_SMALL`, `T4`, `T4_X_2/4/8`, `L4`, `L4_X_2/4/8`, `L40S`, `L40S_X_2/4/8`, `RTXP_6000` (+`_X_2/4/8`), `A100` (+`_X_2/4/8`), `H100` (+`_X_2/4/8`), `H200` (+`_X_2/4/8`), `B200`, `B200_X_8`
- `A100_40GB*`/`A100_80GB*` variants exist in the SDK but are hidden from CLI `--machine` (usable with `studio switch` and in Python).

The names above are current as of writing and SKUs do get added, so treat the list as a starting
point, not a closed set. `lightning machine list` prints the names your installed CLI accepts, but
it is an offline list and says nothing about what a teamspace can launch. For live availability
and prices, use `Teamspace("owner/teamspace").list_machines()` in Python (drops out-of-capacity
machines; each has `cost`/`interruptible_cost`) or
`GET /v1/core/accelerators?cloudProvider=<PROVIDER>` (no auth needed, so plain `curl` works; the
`lightning-cost-estimation` skill has the provider values and the costing recipes). Don't invent a
catalog endpoint: `/v1/accelerators`, `/v1/accelerator-catalog`, `/v1/pricing` and
`/v1/compute/accelerators` all return `code: 5`.

Interruptible (spot) is a flag, not a machine type: `--interruptible` / `interruptible=True`.

## Example workflows

Prompts this skill handles: *"spin up a GPU studio and run my training script"*, *"copy this repo to my studio and start a long run"*, *"SSH into exp-studio"*, *"my studio is idle, stop it"*.

**Create a studio, run a script, collect results, stop** (cheap default: start on CPU, switch to GPU only for the run):

```bash
lightning studio start --name exp-1 --teamspace my-org/my-teamspace --machine CPU --create
lightning cp -r ./src lit://my-org/my-teamspace/studios/exp-1/src/
lightning studio switch --name exp-1 --teamspace my-org/my-teamspace --machine L4
```
```python
from lightning_sdk import Studio
studio = Studio("exp-1", teamspace="my-org/my-teamspace")
out, code = studio.run_with_exit_code("cd ~/src && pip install -r requirements.txt && python train.py")
print(out)
```
```bash
lightning cp -r lit://my-org/my-teamspace/studios/exp-1/src/outputs/ ./outputs
lightning studio stop --name exp-1 --teamspace my-org/my-teamspace
```

**SSH in and run scripts interactively** (for a human user; agents should prefer `studio.run*` above since `ssh` opens an interactive shell):

```bash
lightning studio ssh --name exp-1 --teamspace my-org/my-teamspace          # interactive shell
lightning ssh configure --name exp-1 --teamspace my-org/my-teamspace      # writes ~/.ssh/config Host block...
ssh exp-1 'python ~/src/eval.py'                                          # ...then plain ssh runs one-off commands
lightning studio connect exp-1 --teamspace my-org/my-teamspace --machine CPU   # create+start+ssh in one shot
```

**Kick off a long run and detach** (survives your session; studio keeps billing until stopped):

```python
out, code = studio.run_and_detach("cd ~/src && nohup python train.py > train.log 2>&1", timeout=30)
# later: studio.run("tail -20 ~/src/train.log")
```

## Raw API fallback

For anything the CLI doesn't wrap, `lightning api <path>` makes an authenticated request (`-X` method, `-F` typed field, `-f` string field, `-q` jq filter):

```bash
lightning api /v1/memberships -q '.memberships[].name'
PROJECT_ID=$(lightning api /v1/memberships | jq -r '.memberships[] | select(.name=="<teamspace>") | .projectId' | head -1)
# studios are "cloudspaces" in the API — one word, and call it BARE (see Gotchas)
lightning api "/v1/projects/${PROJECT_ID}/cloudspaces" -q '.cloudspaces[].name'
```

## Gotchas

- Starting compute costs money; GPU machines cost more. Prefer `CPU` for setup work, switch to GPU only when needed, and **stop studios when done**. Ask before starting expensive machines (A100/H100/H200/B200) unless the user already specified one.
- `run*` methods and `studio switch` require status `Running`; `start()` on a studio already running on a different machine raises — use `switch_machine` instead.
- Disabling auto-sleep (`studio.auto_sleep = False`) or setting `auto_sleep_time` converts a free CPU studio to paid.
- `studio create` does not attach compute; `studio start --create` does both.
- **`lightning studio delete` prompts for confirmation — pass `-y`/`--yes` non-interactively.** Without it, a scripted or agent-run delete reads the prompt from a closed stdin, prints `Are you sure you want to delete? [y/N]: Aborted.` and exits **without deleting**, leaving the studio (and its billing) alive. Confirm with the user first, then pass `-y`; there is no need to drop into the Python SDK for this.
- **The studios list endpoint is `/cloudspaces`, one word, and takes no `-F` fields.** The hyphenated `/v1/projects/{pid}/cloud-spaces` returns `HTTP 404 Not Found`. Once corrected, adding `-F limit=20` still fails with `HTTP 400 Bad Request`, because `lightning api` sends any request with `-f`/`-F` fields and no `-X` as a POST (the create call). Call it bare and slice with `-q`, or pass `-X GET` to send the fields as query params.
- Inside a Studio, `Studio()` with no args resolves to the current studio (via `LIGHTNING_CLOUD_SPACE_ID`).
- In Python, `teamspace=` takes the same `"owner/teamspace"` string as the CLI `--teamspace` flag (for `Teamspace`, `Studio` and `Job`). The separate `org=`/`user=` arguments still work but are deprecated and emit a `DeprecationWarning`; passing both forms raises `ValueError`.
