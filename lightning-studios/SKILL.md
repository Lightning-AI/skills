---
name: lightning-studios
description: Manage Lightning AI Studios (cloud dev machines with CPUs/GPUs) - create, start, stop, delete studios, switch machine types, run commands in them, upload/download files, and SSH in. Use when the user wants to work with lightning.ai Studios, needs a cloud GPU dev box, or asks to run something "on a studio".
---

# Lightning AI Studios

A Studio is a persistent cloud development machine on [lightning.ai](https://lightning.ai). Its filesystem persists across sessions; compute (CPU/GPU) attaches on start and detaches on stop. Everything below uses the `lightning` CLI and `lightning_sdk` Python package.

## Setup & auth

```bash
uvx lightning-sdk --version         # CLI without installing; `lightning` == `lightning-sdk`
pip install lightning-sdk           # or persistent install
lightning login                     # browser flow; or set env vars for headless use:
export LIGHTNING_USER_ID=... LIGHTNING_API_KEY=...   # both required (Basic auth is user_id:api_key)
```

Credentials are stored in `~/.lightning/credentials.json`. Python snippets can run via `uv run --with lightning-sdk python script.py`.

## Resolving org and teamspace (do this first)

Every studio lives in a teamspace owned by either an organization or a user. **Never guess.** Resolve in this order:

1. Explicit `--teamspace owner/teamspace` flag (owner = org or user name) / `Teamspace(name, org=...)` or `Teamspace(name, user=...)` in Python (`org` and `user` are mutually exclusive).
2. Env vars `LIGHTNING_ORG`, `LIGHTNING_TEAMSPACE`, `LIGHTNING_USERNAME`, or config defaults in `~/.lightning/config.yaml` (`lightning config get teamspace`).
3. Otherwise list the options and **ask the user which org/teamspace to use**:

```bash
lightning api /v1/memberships | jq -r '.memberships[] | [.ownerType, .name, .projectId] | @tsv'
```

Persist the user's choice so they aren't asked again: `lightning config set teamspace <owner>/<teamspace>`.

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
lightning studio delete --name my-studio --teamspace owner/teamspace           # interactive confirmation prompt

# ssh / one-shot connect (create + start + ssh)
lightning studio ssh --name my-studio --teamspace owner/teamspace
lightning studio connect my-studio --teamspace owner/teamspace --machine CPU

# machines available
lightning machine list
```

### Files: `lit://` paths

Studio paths use `lit://<owner>/<teamspace>/studios/<studio-name>/<path>`. Exactly one side of a copy must be `lit://`; directories need `-r`.

```bash
lightning cp ./train.py lit://owner/teamspace/studios/my-studio/train.py     # upload
lightning cp -r lit://owner/teamspace/studios/my-studio/logs/ ./logs         # download dir
lightning studio ls lit://owner/teamspace/studios/my-studio/                 # list files
lightning studio rm lit://owner/teamspace/studios/my-studio/old.txt [-r] [-f]
```

## Python SDK

```python
from lightning_sdk import Machine, Studio, Teamspace

ts = Teamspace("my-teamspace", org="my-org")            # or user="username" — never both
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
- GPU: `T4`, `T4_X_2/4/8`, `L4`, `L4_X_2/4/8`, `L40S`, `L40S_X_2/4/8`, `RTXP_6000` (+`_X_2/4/8`), `A100` (+`_X_2/4/8`), `H100` (+`_X_2/4/8`), `H200`, `H200_X_8`, `B200_X_8`
- `A100_40GB*`/`A100_80GB*` variants exist in the SDK but are hidden from CLI `--machine` (usable with `studio switch` and in Python).

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
studio = Studio("exp-1", teamspace="my-teamspace", org="my-org")
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
PROJECT_ID=$(lightning api /v1/memberships | jq -r '.memberships[0].projectId')
lightning api "/v1/projects/${PROJECT_ID}/cloud-spaces" -F limit=20     # studios are "cloud spaces" in the API
```

## Gotchas

- Starting compute costs money; GPU machines cost more. Prefer `CPU` for setup work, switch to GPU only when needed, and **stop studios when done**. Ask before starting expensive machines (A100/H100/H200/B200) unless the user already specified one.
- `run*` methods and `studio switch` require status `Running`; `start()` on a studio already running on a different machine raises — use `switch_machine` instead.
- Disabling auto-sleep (`studio.auto_sleep = False`) or setting `auto_sleep_time` converts a free CPU studio to paid.
- `studio create` does not attach compute; `studio start --create` does both.
- `lightning studio delete` prompts for confirmation — in non-interactive contexts confirm with the user first, then use the Python SDK `studio.delete()`.
- Inside a Studio, `Studio()` with no args resolves to the current studio (via `LIGHTNING_CLOUD_SPACE_ID`).
- `lightning cp lit://.../file.txt file.txt` (bare local filename as destination) fails with `FileNotFoundError: [Errno 2] ... ''` — write the destination as `./file.txt` or a directory path.
- In Python, `teamspace=` takes the bare teamspace name with `org=`/`user=` separate; `"owner/name"` combined strings only work in CLI `--teamspace` flags.
