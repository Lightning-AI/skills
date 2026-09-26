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
| Installing is blocked (read-only env or home, agent sandbox) | Skip it and run each command as `UV_CACHE_DIR="${TMPDIR:-/tmp}/uv" UV_TOOL_DIR="${TMPDIR:-/tmp}/uv-tools" uvx lightning-sdk …` |
| Every call fails on SSL/certificates, even `lightning --version` (`Could not find a suitable TLS CA certificate bundle`), only inside an agent sandbox | A `**/*.pem` read-deny rule is hiding certifi's public CA bundle (`site-packages/certifi/cacert.pem`). Allow that one path; don't disable the sandbox |
| Every call fails with `NameResolutionError`, only inside an agent sandbox | If `lightning api` fails too, ask the user to allow `lightning.ai` and `*.lightning.ai` (Claude Code: `/sandbox`). If only `studio`/`job` commands or the SDK fail, they bypass the sandbox's proxy and no allowlist helps: ask to run just those outside the sandbox. Background pollers fail the same way, silently |

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

**`cp -r` copies a folder's contents, not the folder.** `lightning cp -r ./proj lit://…/studios/my-studio/`
puts `proj`'s files straight into the Studio's home, whether or not either path ends in `/`. To
keep the folder, name it in the destination: `lightning cp -r ./proj lit://…/studios/my-studio/proj/`.
Check the result with `lightning ls -r` before running anything that expects the files in place.

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
it is an offline list and says nothing about what a teamspace can launch.

**Pick the machine from live data, per cloud account.** A teamspace can launch on several cloud
accounts, and the same GPU differs between them in name, price and wait. List what each account
can start right now, fastest first:

```python
from lightning_sdk import Teamspace
ts = Teamspace("my-org/my-teamspace")
FAMILY = "H100"   # the GPU family the workload needs
# Accounts go by id: ts.cloud_accounts holds display names, and list_machines() returns [] for a name
rows = [(m.wait_time, m.cost, a.cluster_id, m) for a in ts.cloud_account_objs
        for m in ts.list_machines(cloud_account=a.cluster_id) if m.family == FAMILY]
for wait, cost, acct, m in sorted(rows, key=lambda r: (r[0] is None, r[0] or 0, r[1] or 0)):
    print(f"{acct:34} {m.name:28} x{m.accelerator_count}  ${cost}/h  wait ~{wait}s")
```

`cost` is USD per hour and `wait_time` the expected seconds until a machine is free. A Studio's
cloud account is fixed when it is created, so create it on the row's account
(`Studio(..., cloud=acct)` / `--cloud acct`) and start or switch it with that row's `Machine`
object: a machine name from one account fails on another. `list_machines()` with no argument
merges several accounts without saying which row belongs to which. Show the user the top rows with
price and wait before starting a GPU. For quotes before login, `GET
/v1/core/accelerators?cloudProvider=<PROVIDER>` needs no auth (the `lightning-cost-estimation`
skill has the provider values and the costing recipes). Don't invent a catalog endpoint:
`/v1/accelerators`, `/v1/accelerator-catalog`, `/v1/pricing` and `/v1/compute/accelerators` all
return `code: 5`.

Interruptible (spot) is a flag, not a machine type: `--interruptible` / `interruptible=True`.

### Launching on a GPU

<!-- TODO: remove this workaround once the backend rejects a GPU request it can't fill instead of
starting a CPU machine (Task Board: "CLI silently downgrades to CPU when a GPU SKU isn't available"). -->

A Studio's cloud is fixed at creation (`studio switch` can't move it), and the default cloud
doesn't sell every GPU at every count: 1× H200 isn't on AWS. **Ask for a GPU the Studio's cloud
doesn't sell and `studio start` comes up on CPU with only a "hasn't been vetted" warning.** So:

1. **Pick a cloud account that sells the GPU at your count.** The per-account listing in
   *Machine types* shows only what each account can launch; create the Studio on that row's
   account. A reused Studio keeps its old cloud, so check it in `lightning studio list` first and
   create a new Studio if it's the wrong one.
2. **Set up on CPU on that account.** Installs, model downloads and CUDA source builds bill at GPU
   rates otherwise. Builds need their target set, e.g. `TORCH_CUDA_ARCH_LIST=9.0` for H100/H200.
3. **Switch with that row's `Machine`, wait until the Studio can run commands again, and check the
   hardware** before running anything. No GPU means stop the Studio and go back to step 1; don't
   retry.

The first workflow below does exactly this.

## Example workflows

Prompts this skill handles: *"spin up a GPU studio and run my training script"*, *"copy this repo to my studio and start a long run"*, *"SSH into exp-studio"*, *"my studio is idle, stop it"*.

**Set up on CPU, switch to a GPU, run, collect results, stop.** `Running` comes before a Studio
can run commands, and `start()` can keep blocking after it can, so start in the background and
poll for readiness with a deadline (see Gotchas):

```python
import threading, time
from lightning_sdk import Machine, Studio, Teamspace

ts = Teamspace("my-org/my-teamspace")
# the fastest, then cheapest, 1x H200 across the teamspace's cloud accounts (see *Machine types*)
rows = [(m.wait_time, m.cost, a.cluster_id, m) for a in ts.cloud_account_objs
        for m in ts.list_machines(cloud_account=a.cluster_id)
        if m.family == "H200" and m.accelerator_count == 1]
wait, cost, acct, gpu = min(rows, key=lambda r: (r[0] is None, r[0] or 0, r[1] or 0))
print(acct, gpu.name, f"${cost}/h", f"wait ~{wait}s")        # confirm with the user before the GPU starts

studio = Studio("exp-1", teamspace=ts, cloud=acct, create_ok=True)
# an existing Studio of that name is reused as is, on whatever cloud it was created on
assert studio.cloud_account == acct, f"{studio.name} is on {studio.cloud_account}: pick a new name"

def wait_ready(minutes=10):
    deadline = time.time() + minutes * 60
    while time.time() < deadline:
        if str(studio.status).endswith("Running"):
            out, code = studio.run_with_exit_code("python -c pass")   # not `true`: see Gotchas
            if code == 0 and "setting things up" not in out:
                return
        time.sleep(10)
    studio.stop()   # stuck in setup: create a new Studio rather than starting this one again
    raise TimeoutError(f"{studio.name} never finished setup")

threading.Thread(target=studio.start, kwargs={"machine": Machine.CPU}, daemon=True).start()
wait_ready()
studio.upload_folder("./src", "src")                          # after ready, so it lands at once
studio.run("cd ~/src && pip install -r requirements.txt")     # setup at CPU rates
try:                                                          # any failure from here stops the GPU
    try:
        studio.switch_machine(gpu)                            # the row's Machine, not Machine.H200
    except Exception as e:                                    # it can report this and switch anyway
        if "cannot switch to a Studio" not in str(e):
            raise
    for _ in range(30):                                       # so trust nvidia-smi, not the call
        out, code = studio.run_with_exit_code("nvidia-smi --query-gpu=name --format=csv,noheader")
        gpus = out.splitlines() if code == 0 else []
        if gpus:
            break
        time.sleep(10)
    print(gpus)                                               # e.g. ['NVIDIA H200']
    assert len(gpus) == 1 and "H200" in gpus[0], "wrong hardware: pick another account"
    wait_ready()                                              # a new machine: wait until it can run commands
    # train.exit gets the exit code when training ends; clear the last run's before launching
    studio.run_and_detach("cd ~/src && rm -f train.exit && nohup sh -c 'python train.py > train.log 2>&1; echo $? > train.exit'", timeout=30)
    print(studio.run("tail -n 40 ~/src/train.log"))          # within the first minute: crashes show up in seconds
    deadline = time.time() + 2 * 3600                         # a bit over the expected run time
    while studio.run_with_exit_code("test -f ~/src/train.exit")[1] != 0:
        if time.time() > deadline:
            raise TimeoutError(studio.run("tail -n 40 ~/src/train.log"))
        print(studio.run("tail -n 1 ~/src/train.log"))        # progress line each poll
        time.sleep(60)
    assert studio.run("cat ~/src/train.exit") == "0", studio.run("tail -n 40 ~/src/train.log")
except BaseException:
    studio.stop()
    raise
```
```bash
lightning cp -r lit://my-org/my-teamspace/studios/exp-1/src/outputs/ ./outputs
lightning studio stop --name exp-1 --teamspace my-org/my-teamspace
```

For a short run where setup is light (a `uv` script that declares its own dependencies), skip
the CPU phase: create the Studio on the row's account, start it with `machine=gpu`, `wait_ready()`,
`studio.upload_file("train.py", "train.py")`, then
`studio.run("cd ~ && env -u UV_LIGHTNING_VIRTUALENV_ROOT uv run train.py")` (see Gotchas).

**SSH in and run scripts interactively** (for a human user; agents should prefer `studio.run*` above since `ssh` opens an interactive shell):

```bash
lightning studio ssh --name exp-1 --teamspace my-org/my-teamspace          # interactive shell
lightning ssh configure --name exp-1 --teamspace my-org/my-teamspace      # writes ~/.ssh/config Host block...
ssh exp-1 'python ~/src/eval.py'                                          # ...then plain ssh runs one-off commands
lightning studio connect exp-1 --teamspace my-org/my-teamspace --machine CPU   # create+start+ssh in one shot
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
- **For a one-shot run (train, eval, batch), prefer a job (`lightning-jobs`).** A job stops billing
  when its command exits, crash included; a crashed run on a Studio keeps the GPU billing. The
  exception is a short run on a tight deadline: the first job from a Studio waits about 5 minutes
  for its snapshot, so run on a Studio started on the GPU, with a deadline that stops it.
- **Before relaunching on a GPU, check the last attempt isn't still holding it**, or the new run
  hits `CUDA out of memory`: `nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader`
  should print nothing.
- **Read a detached run's log within the first minute, and treat a silent monitor as a failure.**
  A poller with no deadline, one that only matches progress lines, or one that can't reach
  lightning.ai (a sandboxed background command) stays quiet through a crash. Give it a deadline,
  match `Traceback`/`Error`/`Killed`/`CUDA out of memory` too, and check it prints its first line.
- **`lightning studio delete` prompts for confirmation — pass `-y`/`--yes` non-interactively.** Without it, a scripted or agent-run delete reads the prompt from a closed stdin, prints `Are you sure you want to delete? [y/N]: Aborted.` and exits **without deleting**, leaving the studio (and its billing) alive. Confirm with the user first, then pass `-y`; there is no need to drop into the Python SDK for this.
- **The studios list endpoint is `/cloudspaces`, one word, and takes no `-F` fields.** The hyphenated `/v1/projects/{pid}/cloud-spaces` returns `HTTP 404 Not Found`. Once corrected, adding `-F limit=20` still fails with `HTTP 400 Bad Request`, because `lightning api` sends any request with `-f`/`-F` fields and no `-X` as a POST (the create call). Call it bare and slice with `-q`, or pass `-X GET` to send the fields as query params.
- Inside a Studio, `Studio()` with no args resolves to the current studio (via `LIGHTNING_CLOUD_SPACE_ID`).
- **`cloud=` / `--cloud` takes a cloud account *id*.** `Teamspace.cloud_accounts` returns display names such as `Lightning Cloud`, and `Studio(..., cloud="Lightning Cloud")` fails with `400 clusterID Lightning Cloud is invalid`. Use `Teamspace.cloud_account_objs[i].cluster_id` (here `lightning-baremetal`); see *Machine types* for listing each account's machines.
- **`uv run` fails in a fresh Studio** (and in jobs launched from one) with `error: failed to symlink file from /system/conda/miniconda3/uv/cache/… to /system/conda/miniconda3/uv/venvs/…: No such file or directory`. The Studio's `UV_LIGHTNING_VIRTUALENV_ROOT` points at a folder that doesn't exist yet. Run `env -u UV_LIGHTNING_VIRTUALENV_ROOT uv run …`.
- **`Running` is not ready.** Right after a start, commands can fail with `Error: We are still setting things up for you, please try again after the progress bar at the top of the Studio disappears.` `python`, `pip` and `uv` are shell aliases (`load_conda_and_run`) that refuse until setup is done, while a plain `true` succeeds much earlier, so poll with `studio.run_with_exit_code("python -c pass")` until that text is gone (see *Example workflows*; it took about 80 s on a restarted CPU Studio). When measured, a fresh H200 Studio took about 2 minutes, while another never finished setup across two starts and ~20 minutes of billed H200 time. If setup is still going after several minutes, stop the Studio and create a new one rather than starting it again.
- **`studio.switch_machine()` can raise and still switch.** A CPU → H200 switch on `lightning-baremetal` raised `ApiException (400) … "cannot switch to a Studio"`, and 14 s later `nvidia-smi` on the Studio showed the H200. Catch that error and poll `nvidia-smi` for the hardware, as in *Example workflows*; don't restart or re-switch on it.
- **`studio.start()` and `lightning studio start` can block well past readiness**, and in the stuck case above they never returned. Run them in the background and poll, as in the example, rather than waiting on them.
- **`studio.run*()` raises when the command's output isn't valid UTF-8.** Output cut through a multi-byte character — `tail -c N` on a log with `é` or a progress bar, `head -c` on a text file — fails in `cloud_space_service_get_long_running_command_in_cloud_space` (HTTP 500), even though the command itself succeeded. Read logs with `tail -n N`, or pipe through `iconv -c -f utf-8 -t utf-8`.
- **Upload after the Studio is ready, with `studio.upload_file`.** `lightning cp` into a running Studio goes through storage: a file copied during setup wasn't on the machine when it became usable, and appeared about 30 s later. `upload_file` on a ready Studio showed up at once. Check with `studio.run("ls ~")` before running anything that needs the file.
- In Python, `teamspace=` takes the same `"owner/teamspace"` string as the CLI `--teamspace` flag (for `Teamspace`, `Studio` and `Job`). The separate `org=`/`user=` arguments still work but are deprecated and emit a `DeprecationWarning`; passing both forms raises `ValueError`.
