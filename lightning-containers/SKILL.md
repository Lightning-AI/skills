---
name: lightning-containers
description: Push Docker images to a teamspace's private Lightning AI registry (litcr.io) with `lightning container`, then run them as jobs, deployments or sandboxes, or `docker pull` them anywhere. Lists, pulls and deletes images and tags. Use when the user wants to bake code or an environment into an image, "push this image to the lightning registry", run a job or deployment from a custom image, clean up registry images, or asks why an image can't be pushed or pulled.
license: Apache-2.0
compatibility: Requires Python with uv or pip, the lightning CLI from the lightning-sdk package (installed on demand), a running local Docker daemon, network access to lightning.ai and litcr.io, and a Lightning AI account.
---

# Lightning AI container registry

Every teamspace has a private Docker registry. Images live at:

```
litcr.io/lit-container/<owner>/<teamspace>/<name>:<tag>
```

`<owner>` is the org name or, for a personal teamspace, the username. Push a local image with
`lightning container upload`. From then on, anything in the teamspace can run it by that URL:
jobs, deployments, sandboxes, or plain `docker` on any machine.

**Docker must be running locally for every `lightning container` command**, even `list` and
`delete`, which never touch an image. `lightning api` (see *Raw API fallback*) needs no Docker.

## Setup & auth

```bash
export DEBUG=0 LIGHTNING_DEBUG=0   # an inherited DEBUG=1 logs the Authorization header; set this in every new shell
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

Also check the Docker daemon: `docker version` must print a `Server:` section.

## Resolving org and teamspace (do this first)

Images live in a teamspace owned by an organization or a user. **Never guess.** Use the user's
`owner/teamspace`, the env vars `LIGHTNING_ORG` / `LIGHTNING_TEAMSPACE`, or the config default
(`lightning config get teamspace`). If none is set, list the options and **ask the user which to
use**:

```bash
lightning api /v1/memberships -q '.memberships[] | [.ownerType, .name, .projectId, .ownerId] | @tsv'
```

The image URL needs the owner's *name*, and the raw API needs the *project id*. The membership
gives only the owner's id, so resolve the name by `ownerType`:

```bash
TSNAME=<name>; PID=<projectId>; OWNER_ID=<ownerId>
if [ "<ownerType>" = organization ]; then
  OWNER=$(lightning api "/v1/orgs/$OWNER_ID" | jq -r .name)
else   # a personal teamspace; the search is fuzzy, so match the id exactly
  OWNER=$(lightning api /v1/users/search -X GET -f "query=$OWNER_ID" \
    | jq -r --arg id "$OWNER_ID" '.users[] | select(.id==$id) | .username')
fi
TS="$OWNER/$TSNAME"
```

**Always pass `--teamspace "$TS"`.** Without it, `upload` falls back to the teamspace of the Studio
you are in, which may not be the one the user means.

## CLI reference

```bash
# push: the local image must exist as <name>:<tag>; --tag defaults to latest
lightning container upload <name> --tag <tag> --teamspace "$TS"

lightning container list --teamspace "$TS" [--json]   # one row per repository, with its latest tag only
lightning container download <name> --tag latest --teamspace "$TS"   # only `latest` works, see Gotchas
lightning container delete <name> --teamspace "$TS" [-y]   # the whole repository: EVERY tag
```

`upload` details:

- **It pushes under the last path segment of the name.** `upload ghcr.io/acme/trainer --tag v3`
  lands at `.../<teamspace>/trainer:v3`.
- **If the image isn't local, it pulls it first** (as `--platform`, default `linux/amd64`), so
  `lightning container upload nginx --tag 1.27 --teamspace "$TS"` mirrors a public image into the
  registry.
- **`--platform` only applies to that pull.** It does not rebuild a local image for another
  architecture. Build for amd64 yourself (see *Build the image*).
- **`--cloud-account <id>` pushes to a different registry**, `litcr.io/lit-container-<id>/...`, for
  a BYOC cloud account. Leave it off unless the user asks for it (see Gotchas).

**The image URL** that jobs, deployments and `docker` take is
`IMAGE="litcr.io/lit-container/$TS/<name>:<tag>"`. To read it back from the registry, which also
proves the push landed (read both `latestArtifact` and `artifacts`; see Gotchas):

```bash
lightning api "/v1/projects/$PID/lit-registry/<name>/artifacts" \
  -q '([.latestArtifact] + (.artifacts // []))[] | select(.) | [.tagName, .location, .size, .pushTime] | @tsv'
```

## Build the image

```bash
docker build --platform linux/amd64 -t trainer:"$TAG" .
```

- **Build for `linux/amd64`.** Lightning machines are amd64. An image built on an Apple Silicon Mac
  without `--platform` pushes fine and then fails to pull on the machine.
- **Use a unique tag per build**, such as a version or commit hash (`TAG=$(git rev-parse --short
  HEAD)`), not `latest`, so you know exactly which image ran.
- **Order layers by how often they change:** base image and system packages first, then
  dependency files and their install, then your code. A rebuild then reuses the cached layers, and
  the push only uploads what changed. Layers that are already in the registry are skipped.
- **Keep it small.** Use a tight `.dockerignore` (`.git`, data, checkpoints, venvs), and don't
  bake in large data that the run can read from teamspace storage.
- **Never bake in secrets.** Pass them as environment variables or secrets when the image runs.
  Anyone with access to the teamspace can pull the image.

For GPU work, start from an image that already has CUDA and your framework, such as
`pytorch/pytorch:2.4.1-cuda12.1-cudnn9-runtime`, rather than installing CUDA yourself.

## Using a pushed image

| Where | How | Credentials |
|---|---|---|
| Jobs | `lightning job run --image "$IMAGE" ...`, or `Job.run(image=IMAGE, ...)` (see `lightning-jobs`) | Added automatically. Don't pass `--image-credentials` |
| Deployments | `lightning deployment create ... --image "$IMAGE"` (see `lightning-deployments`) | Added automatically |
| Sandboxes | `lightning sandbox create --image "$IMAGE" --image-secret-ref <secret>` (see `lightning-sandboxes`) | **Required**: a Docker-registry secret, below |
| Any machine with Docker (laptop, Studio, CI) | `docker login litcr.io`, then `docker pull "$IMAGE"` | Your username and API key |

**Jobs and deployments pull as the user who launches them**, using that user's username and API
key. Any teamspace that user can access works, not only the one that runs the workload. The user
needs at least one API key; without one the launch fails with `user has no api key for litCR
auth`.

**Pull with plain Docker:**

```bash
# the username of the login you're using; --password-stdin keeps the key out of the process list
U=$(lightning api /v1/auth/user -q .username)
printf '%s' "$LIGHTNING_API_KEY" | docker login litcr.io -u "$U" --password-stdin
docker pull "$IMAGE"
```

The password is your user API key: lightning.ai → avatar → **Global Settings → Keys**. `docker
login` saves it in `~/.docker/config.json` on that machine. On a shared machine, run `docker logout
litcr.io` when you're done.

**Sandbox registry secret.** Sandboxes get no automatic credentials, so a litcr.io image needs a
`SECRET_TYPE_DOCKER_REGISTRY` secret in the teamspace. Its value is the base64 of a JSON object
with `registry`, `username` and `password`. The secret name may contain only letters, digits and
underscores, and must not start with a digit:

```bash
VALUE=$(jq -nc --arg u "$U" --arg p "$LIGHTNING_API_KEY" \
  '{registry: "litcr.io", username: $u, password: $p}' | base64 | tr -d '\n')
lightning api "/v1/projects/$PID/secrets" \
  -f name=LITCR_PULL -f type=SECRET_TYPE_DOCKER_REGISTRY -f "value=$VALUE" --silent
lightning api "/v1/projects/$PID/secrets" -q '.secrets[] | [.name, .type] | @tsv'   # check it
```

The secret holds a real API key, readable by anything in the teamspace that can use secrets.
**Ask before creating it.**

## Example workflows

Prompts this skill handles: *"bake my training code into an image and run it on an A100"*, *"push
this image to the lightning registry"*, *"pull our registry image onto my laptop"*, *"clean up old
images in the teamspace"*.

**Build, push, check, then run as a job:**

```bash
TAG=$(git rev-parse --short HEAD)
docker build --platform linux/amd64 -t trainer:"$TAG" .
docker run --rm trainer:"$TAG" python -c "import torch, mypkg; print(torch.__version__)"   # check locally first

lightning container upload trainer --tag "$TAG" --teamspace "$TS"
lightning api "/v1/projects/$PID/lit-registry/trainer/artifacts" \
  -q '([.latestArtifact] + (.artifacts // []))[] | select(.) | .tagName' \
  | grep -qx "\"$TAG\"" || echo "push failed: tag $TAG is not in the registry" >&2

IMAGE="litcr.io/lit-container/$TS/trainer:$TAG"
# one small real run first; read its log before launching the full run
lightning job run --name trainer-smoke-$TAG --teamspace "$TS" --image "$IMAGE" --machine CPU \
  --command "python train.py --max-steps 10"
lightning job logs trainer-smoke-$TAG --teamspace "$TS"
```

**Clean up old images.** List them, show the user, and delete only what they agree is unused:

```bash
lightning container list --teamspace "$TS" --json | jq -r '.[] | [.repository, .latest_tag, .created] | @tsv'
# drop one old version of a repository and keep the rest
DIGEST=$(lightning api "/v1/projects/$PID/lit-registry/trainer/artifacts" \
  -q '([.latestArtifact] + (.artifacts // []))[] | select(.tagName=="old-tag") | .fullHashDigest' | tr -d '"')
lightning api "/v1/projects/$PID/lit-registry/trainer/artifacts/$DIGEST" -X DELETE
# or drop the whole repository, every tag in it
lightning container delete trainer --teamspace "$TS" -y
```

## Raw API fallback

None of these need Docker. `$PID` is the teamspace's project id.

```bash
lightning api "/v1/projects/$PID/lit-registry"                    # repositories (first 100)
lightning api "/v1/projects/$PID/lit-registry" -X GET -f filterForRepo=trainer
lightning api "/v1/projects/$PID/lit-registry/trainer"            # one repository: artifactCount, pullCount, latestArtifact
lightning api "/v1/projects/$PID/lit-registry/trainer/artifacts"  # versions: latestArtifact + artifacts (first 100)
lightning api "/v1/projects/$PID/lit-registry/trainer/artifacts/<sha256:...>" -X DELETE   # one version, with all its tags
lightning api "/v1/projects/$PID/lit-registry/trainer" -X DELETE  # the whole repository
```

Add `-X GET -f clusterId=<id>` to reach a cloud account's registry instead of the default one.

## Gotchas

- **A failed `upload` still exits 0.** A missing image, a failed pull or a push error is printed
  as `Error: ...` on the progress line, and the exit code is still 0. Check the output for
  `Container pushed!`, and confirm the tag with the artifacts call before using it. A missing local
  image reads `Error: Container <name>:<tag> does not exist`.
- **`download` only works for the `latest` tag.** For any other tag it pulls the image, then fails
  with `ImageNotFound: 404 Client Error ... /images/litcr.io/lit-container/.../<name>/tag?tag=<tag>`
  and exits 1. The pull itself succeeded: the image is in local Docker as the full
  `litcr.io/lit-container/...:<tag>` name, not as `<name>:<tag>`. Use `docker login` and `docker
  pull "$IMAGE"` instead, which works for every tag.
- **`docker pull` without `docker login` fails** with `unauthorized: unauthorized to access
  repository: <id>-<id>/<name>, action: pull`, even right after a successful `lightning container
  upload`. The CLI logs in only inside its own process and writes nothing to Docker's config.
- **A misspelt owner or teamspace in the URL also returns `401 unauthorized`**, with `unable to
  find path https://lightning.ai/<owner>/<teamspace>/containers`. The error looks like bad
  credentials, so check the URL against the artifacts call's `location` first.
- **`delete` takes a name, not a tag, and removes every tag.** Deleting a repository that had `v1`
  and `latest` removes both. To drop one version, delete its digest through the raw API. List first,
  and delete only images the user agrees are unused.
- **Use the teamspace's default registry.** Leave `--cloud-account` off. Machines on any cloud
  account pull from the default registry with automatic credentials. An image pushed with
  `--cloud-account <id>` gets automatic credentials only on jobs and deployments that run on that
  same cloud account; elsewhere the pull is unauthorized. That registry only exists for BYOC AWS
  and GCP accounts, and pushing to it can fail as unauthorized depending on the account.
- **The newest version is missing from `.artifacts`.** The artifacts call returns it separately as
  `latestArtifact`, so a check that reads only `.artifacts` reports a push that just landed as
  missing. Read both.
- **`list` without `--cloud-account` lists the teamspace's default cloud account's registry.**
  When that default is a BYOC account, `list` shows that account's images rather than the default
  registry `upload` pushes to. Read the default registry with the raw API, which defaults to it.
- **An image without a `linux/amd64` variant fails at pull time on the machine**, with `This image
  was built with the wrong architecture. Re-build it for amd64 (--platform linux/amd64)`. The push
  itself succeeds, so this only shows when the job or deployment starts.
- **Jobs and deployments need the exact tag to exist.** A missing tag fails at submit with `tag X not
  found in repository Y, available tags: [...]`. An image URL with no tag means `:latest`, which
  fails when the repository has no `latest` tag.
- **Images count toward teamspace storage.** The first 10 GB of teamspace storage is free, then it
  is billed per GB per month (see `lightning-billing`). Nothing deletes old tags for you, so clean up
  images from abandoned experiments.
- **Listings stop at 100.** The registry API returns at most 100 repositories per teamspace and 100
  versions per repository, and does not paginate. The oldest beyond that don't appear.
- **Check an image before using it at scale.** First run it locally (`docker run --rm "$IMAGE" python
  -c "import ..."`). Then do one small real run, such as one job on a small input, and read its log.
  A broken import found in a 64-GPU job costs the provisioning time of every machine.
