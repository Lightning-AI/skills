---
name: lightning-artifacts
description: Publish a local file (HTML report, PDF, image, dataset sample, build output) to Lightning AI and get a durable, public lightning.ai/artifacts/<id> link that never expires and renders inline in the browser - plus list what's in the artifacts drive, unpublish (revoke) links, and delete the files behind them - entirely through the `lightning` CLI (from the `lightning-sdk` package) with regular auth (`lightning login` or an API key), no code. Use when the user wants to share a file, a generated one-pager, or an agent-made artifact as a permanent URL, hand a file to a teammate or CI job, see or revoke existing shared links, or asks to "get a public / shareable link for this file".
---

# Lightning AI Artifacts (durable shareable file links)

Publish any file to a teamspace and get back a **durable public URL** —
`https://lightning.ai/artifacts/<id>` — that anyone can open with no Lightning
login, renders inline in the browser (HTML/PDF/images), and **never expires**.
The control plane streams the bytes from storage on every request, so unlike a
presigned S3 URL there is no ~1h cap. Great for agent-generated one-pagers,
reports, dashboards, dataset samples, or build artifacts.

**This whole flow runs through the `lightning` CLI** (from the `lightning-sdk` package):
`lightning cp` / `ls` / `rm` handle the files, and `lightning api` — a
`gh api`-style raw HTTP client — makes the two publish calls around them. No
Python, no SDK code, not even a `curl`.

## Setup & auth

```bash
# Use the Lightning AI CLI from the current env; install or upgrade it there if it's missing or older than 2026.9.18
v=$(lightning --version 2>/dev/null | sed -n 's/^Lightning CLI version //p')
[ -n "$v" ] && [ "$(printf '%s\n' 2026.9.18 "$v" | sort -V | head -1)" = 2026.9.18 ] \
  || uv pip install -U lightning-sdk || python3 -m pip install -U lightning-sdk
lightning --version   # must print "Lightning CLI version …"; if not, see the table below
lightning login                       # interactive browser sign-in — enough for everything here
# or: export LIGHTNING_API_KEY=...    # non-interactive alternative (CI, agents)
```

Either credential works for **every call in this skill** — no token minting or
extra auth steps. Get a key from lightning.ai → user/org settings, or
`lightning api-key create --org <org> --name artifacts`. **Never hardcode the
key** — read it from the environment. To target a non-prod control plane, set
`LIGHTNING_CLOUD_URL` (default `https://lightning.ai`).

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
| Every call fails with `NameResolutionError` ("Failed to resolve 'lightning.ai'"), only inside an agent sandbox | The sandbox's network allowlist doesn't include Lightning. Ask the user to allow `lightning.ai` and `*.lightning.ai` for the sandbox (Claude Code: `/sandbox`); don't disable the sandbox. Anything you run in the background or poll with runs in the same sandbox and fails the same way, often silently |

`lightning api` flags: `-X` method, `-f key=val` string field, `-F key=val`
typed field, `-H` header, `--input <file>` request body (`--input -` to pipe
one), `-q` jq filter (needs the `jq` binary for `-q`), `-i` include response
headers, `--silent` no output. Fields with no `-X` make it a POST. Fields are JSON body for POST/PUT-with-body and **query
params** when the request also has `--input` or is a GET.

## Resolve the teamspace (do this first)

Artifacts live in a teamspace (a "project" in the REST API). You need three
values: the **owner name** and **teamspace name** (for `lit://` upload URLs)
and the **project id** (for the REST calls). **Never guess.** List memberships
and, if more than one fits and none is configured, **ask the user which to
use**:

```bash
lightning api /v1/memberships -q '.memberships[] | [.name, .projectId, .ownerType, .ownerId] | @tsv'
```

Capture the row's `projectId` and resolve the owner's name. The membership
only carries the owner's id, and the lookup depends on `ownerType`:

```bash
PID=<projectId-from-above>
TSNAME=<name-from-above>
OWNER_ID=<ownerId-from-above>
if [ "<ownerType-from-above>" = organization ]; then
  OWNER=$(lightning api "/v1/orgs/$OWNER_ID" | jq -r .name)
else   # a personal teamspace: yours, or another user's you were added to
  OWNER=$(lightning api /v1/users/search -X GET -f "query=$OWNER_ID" \
    | jq -r --arg id "$OWNER_ID" '.users[] | select(.id==$id) | .username')
fi
[ -n "$OWNER" ] && [ "$OWNER" != null ] || echo "could not resolve the owner of $TSNAME — ask the user" >&2
```

Search by id and match on `.id` exactly (the search is fuzzy); this is the lookup
the SDK itself uses. Never fall back to your own username: a wrong owner in the
`lit://` path uploads into a different teamspace.

Teamspaces you can access through org-level permissions (rather than direct
membership) don't appear in `/v1/memberships` — if the user names one you
can't find, ask them for the `<owner>/<teamspace>` pair and the project id.
There is no list-projects-by-name endpoint (`/v1/projects?name=…` isn't in the
API), so don't try to look it up.

## Publish a durable link (the CLI flow)

Three calls: `lightning cp` the file into the `artifacts/` drive, read back
which storage cluster it landed on, then register the object as a shared
artifact. Copy-paste function:

```bash
# share <local-file> [remote-name] [content-type]
# Pretty status goes to stderr; the bare URL is the only thing on stdout, so
# URL=$(share file.html) captures cleanly while interactive use looks like:
#   ✅ Published report.html (text/html; charset=utf-8)
#   🔗 https://lightning.ai/artifacts/art_...
share() {
  local FILE="$1" NAME="${2:-$(basename "$1")}" CT="${3:-$(file -b --mime-type "$1")}"
  local KEY="artifacts/${NAME#artifacts/}"     # publish only finds objects under artifacts/
  # 1. upload; the server picks where to store it (see Gotchas)
  lightning cp "$FILE" "lit://$OWNER/$TSNAME/$KEY" >&2 || return 1
  # 2. the blob's clusterId from the listing is the storage cluster the
  #    publish call needs (see Gotchas — it is not always the cluster the
  #    upload went through)
  local CLUSTER; CLUSTER=$(lightning ls --json "lit://$OWNER/$TSNAME/$KEY" | jq -r '.[0].clusterId')
  # 3. register it -> durable, no-expiry lightning.ai/artifacts/<id>
  local LINK; LINK=$(lightning api "/v1/projects/$PID/shared-artifacts" -X POST \
    -f clusterId="$CLUSTER" -f filename="$KEY" -f contentType="$CT" -F private=false -q .url | tr -d '"')
  echo "✅ Published $NAME ($CT)" >&2
  printf '🔗 ' >&2
  echo "$LINK"
}

share report.html                                  # -> https://lightning.ai/artifacts/art_...
share dashboard.html reports/dash.html text/html   # custom remote name + explicit type
```

When you publish something for the user, always show them the full URL on its
own line (terminals make it clickable) — never just say "done".

The upload path and the publish `filename` must point at the same object — the
`KEY` variable keeps them identical. The server confines shares to the
`artifacts/` folder; if you pass a bare `filename` (no `artifacts/` prefix) to
publish it prepends one, but matching the two explicitly is clearest.

Set `-F private=true` to require an authorized project reader to open the link
(good for internal-only shares); the default `false` is genuinely public.

### Content types that render inline

`file -b --mime-type` guesses most cases; pass an explicit type when the
extension is ambiguous. The **publish** call's `contentType` is what the browser
sees on every request (it overrides the stored object's type), so getting it
right there is what makes HTML/PDF render instead of download.

| File | Content-Type |
| --- | --- |
| `.html` | `text/html; charset=utf-8` |
| `.pdf` | `application/pdf` |
| `.svg` | `image/svg+xml` |
| `.png` / `.jpg` | `image/png` / `image/jpeg` |
| `.json` / `.txt` / `.csv` | `application/json` / `text/plain` / `text/csv` |
| anything to force-download | `application/octet-stream` |

## List, see what's published, unpublish, delete

**List what's in the drive** with `lightning ls` — one level by default,
`-r` for every file underneath, `--json` for entries with `size` and the
`clusterId` the publish call needs. Listings follow the server's pages
automatically, so folders with many thousands of files come back complete:

```bash
lightning ls "lit://$OWNER/$TSNAME/artifacts/"              # one level; folders end in /
lightning ls -r "lit://$OWNER/$TSNAME/artifacts/reports"    # full relative file paths
lightning ls --json "lit://$OWNER/$TSNAME/artifacts/reports"
```

**List every published link** in the project (`GET /v1/projects/{pid}/shared-artifacts`,
newest first — id, filename, content type, public/private, download count, URL):

```bash
# shares  ->  one block per published artifact
shares() {
  lightning api "/v1/projects/$PID/shared-artifacts" | jq -r '.artifacts // [] | .[] |
    "\(if .private then "🔒" else "🌐" end) \(.filename)  ·  ⬇ \(.downloads // 0)  ·  \(.createdAt // "")
   id: \(.id)
   🔗 \(.url)"'
}
```

**Unpublish** (revoke the link; the file itself stays in the drive):

```bash
# unshare <artifact-id>
unshare() {
  lightning api "/v1/projects/$PID/shared-artifacts/$1" -X DELETE --silent \
    && echo "🗑️  Unpublished $1 — link is dead, file kept in the drive" >&2
}

unshare art_01kxgaep54zzs84arfns1j21wd
```

**Delete the file itself** (after unpublishing, or to clean up an abandoned
upload) — no cluster id needed; the server removes it wherever it is stored:

```bash
lightning rm "lit://$OWNER/$TSNAME/artifacts/report.html"
lightning rm -r "lit://$OWNER/$TSNAME/artifacts/reports"     # a folder and everything under it
```

`rm` fails on a path that doesn't exist (pass `-f` to ignore) and refuses a
folder without `-r`.

Re-publish the same object later (new id, new URL) by repeating the publish call
with the same `filename`. Update the contents behind an existing link by
re-running `lightning cp` to the same `artifacts/<name>` — published ids keep
serving the new bytes. To change the served Content-Type, publish again.

## Example workflows

Prompts this skill handles: *"share this HTML report as a permanent link"*,
*"give me a public URL for output.pdf"*, *"publish this one-pager so I can send
it"*, *"drop this file somewhere CI can curl it"*.

**Share an agent-generated HTML one-pager** (renders in the browser, no login):

```bash
share summary.html                                 # hand the printed URL to anyone
```

**Publish a folder of reports, one link each** (`share` prints each name + URL):

```bash
for f in out/*.html; do share "$f" "reports/$(basename "$f")" "text/html; charset=utf-8"; done
```

**Hand a file to another service / CI job:**

```bash
URL=$(share model-metrics.json)
# elsewhere:  curl -sL "$URL" -o metrics.json
```

## Gotchas

- **Publish wants the blob's *storage* cluster, not the cluster the upload
  went through.** A teamspace can be bound to compute clusters that store
  their files under a parent cluster's bucket; publishing with such a compute
  cluster's id fails with HTTP 500. The artifacts tree listing reports each
  blob's real `clusterId` — always read it from there (the `share` function
  does). Deletes don't take a cluster at all.
- **`lightning cp` needs no cluster flag** — the server picks the storage.
  Only if it rejects the upload for a missing cluster does `cp` choose one
  itself (`LIGHTNING_CLUSTER_ID` first, which is set inside a Studio, then the
  teamspace's only or default cloud account) and warn `No cloud account
  specified. Using cloud account: <id>.` Pass
  `--cloud-account <id>` only to steer placement deliberately, and pick a
  cluster whose `status.phase` is `CLUSTER_STATE_RUNNING`
  (`/v1/projects/$PID/clusters`) — bound-but-unusable clusters make the
  upload fail with a drive error that says nothing about cluster health.
- **Unpublish reports success for links that don't exist.**
  `DELETE /v1/projects/{pid}/shared-artifacts/{id}` returns `HTTP 200` with `{}` for an
  id that was already revoked or simply mistyped — do not treat exit code 0 as
  proof; verify by checking the public URL 404s. (`lightning rm`, by contrast,
  fails on a missing path unless you pass `-f`.)
- **The served HTML is not byte-identical to what you uploaded.** Cloudflare injects a
  Browser-Insights RUM beacon (`static.cloudflareinsights.com/beacon.min.js`) into HTML
  responses — a ~350-byte delta. Harmless for viewing, but don't promise a
  bit-exact document, and don't checksum the response against the source file.
- **Shares are confined to the `artifacts/` folder.** Publish only finds objects
  under `projects/{pid}/artifacts/...`, so the upload must target
  `lit://<owner>/<teamspace>/artifacts/...`. Files under `uploads/` or
  `lightning_storage/` are reachable with `lightning cp` too, but publish
  won't find them there.
- **Content-Type is set at publish time**, not upload time — the serving handler
  uses the shared-artifact record's `contentType`, overriding the stored object.
  Set it on the publish call so HTML/PDF render inline.
- The public link is genuinely open — anyone with it can fetch the file with no
  auth. Use `-F private=true` for anything you don't want world-readable.
- `-q` (jq filtering) and the `share`/`shares` helpers need the `jq` binary
  installed; without it, parse the JSON yourself.
- **The list endpoint is newer than the rest.** `GET
  /v1/projects/{pid}/shared-artifacts` returns HTTP 501 "Method Not Allowed" on
  control planes built before mid-July 2026 — publish/unpublish still work
  there; only `shares` is affected.
