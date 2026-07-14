---
name: lightning-artifacts
description: Publish a local file (HTML report, PDF, image, dataset sample, build output) to Lightning AI and get a durable, public lightning.ai/artifacts/<id> link that never expires and renders inline in the browser - entirely through the `lightning` CLI (uvx lightning-sdk), no code. Use when the user wants to share a file, a generated one-pager, or an agent-made artifact as a permanent URL, hand a file to a teammate or CI job, or asks to "get a public / shareable link for this file".
---

# Lightning AI Artifacts (durable shareable file links)

Publish any file to a teamspace and get back a **durable public URL** —
`https://lightning.ai/artifacts/<id>` — that anyone can open with no Lightning
login, renders inline in the browser (HTML/PDF/images), and **never expires**.
The control plane streams the bytes from storage on every request, so unlike a
presigned S3 URL there is no ~1h cap. Great for agent-generated one-pagers,
reports, dashboards, dataset samples, or build artifacts.

**This whole flow runs through the `lightning` CLI** (`uvx lightning-sdk`) — three
authenticated REST calls via `lightning api`. No Python, no SDK code.

## Setup & auth

```bash
uvx lightning-sdk --version          # the CLI; no install needed (uvx runs it ad-hoc)
export LIGHTNING_API_KEY=...          # required; the API key authenticates every call
# export LIGHTNING_USER_ID=...        # optional
# lightning login                     # interactive alternative to the env vars
```

Get a key from lightning.ai → user/org settings, or
`lightning api-key create --org <org> --name artifacts`. **Never hardcode the
key** — read it from the environment. To target a non-prod control plane, set
`LIGHTNING_CLOUD_URL` (default `https://lightning.ai`).

`lightning api` is a `gh api`-style raw HTTP client: `-X` method, `-f key=val`
string field, `-F key=val` typed field, `-H` header, `--input <file>` request
body, `-q` jq filter (needs the `jq` binary for `-q`), `-i` include response
headers. Fields are JSON body for POST/PUT-with-body and **query params** when the
request also has `--input` or is a GET.

## Resolve teamspace, project id, and cloud account (do this first)

Artifacts live in a teamspace (a "project" in the REST API). **Never guess.**
List memberships and, if more than one fits and none is configured, **ask the
user which to use**:

```bash
lightning api /v1/memberships -q '.memberships[] | [.ownerType, .name, .projectId] | @tsv'
```

Pick the row you want and capture its `projectId`, then resolve that project's
cloud account (the `clusterId`, required on upload + publish):

```bash
PID=<projectId-from-above>
CLUSTER=$(lightning api "/v1/projects/$PID/clusters" -q '.clusters[0].id')   # e.g. "aws-use1"
```

## Publish a durable link (the CLI flow)

Three steps: mint a short-lived upload token, `PUT` the file under the project's
`artifacts/` folder, then register it as a shared artifact. Copy-paste function:

```bash
# share <local-file> [remote-name] [content-type]  ->  prints the durable URL
share() {
  local FILE="$1" NAME="${2:-$(basename "$1")}" CT="${3:-$(file -b --mime-type "$1")}"
  # 1. short-lived token for the blob-upload endpoint (it doesn't accept basic auth yet)
  local TOKEN; TOKEN=$(lightning api /v1/auth/login -X POST -f apiKey="$LIGHTNING_API_KEY" -q .token | tr -d '"')
  # 2. upload the bytes under artifacts/<name>  (single PUT, files up to 100MB)
  lightning api "/v1/projects/$PID/artifacts/blobs/artifacts/$NAME" -X PUT \
    --input "$FILE" -f clusterId="$CLUSTER" -f token="$TOKEN" -H "Content-Type: $CT" --silent
  # 3. register it -> durable, no-expiry lightning.ai/artifacts/<id>
  lightning api "/v1/projects/$PID/shared-artifacts" -X POST \
    -f clusterId="$CLUSTER" -f filename="artifacts/$NAME" -f contentType="$CT" -F private=false -q .url
}

share report.html                                  # -> https://lightning.ai/artifacts/art_...
share dashboard.html reports/dash.html text/html   # custom remote name + explicit type
```

The upload path segment (`artifacts/$NAME`) and the publish `filename`
(`artifacts/$NAME`) must point at the same object — keep them identical. The
server confines shares to the `artifacts/` folder; if you pass a bare `filename`
(no `artifacts/` prefix) it prepends one, but matching the two explicitly is
clearest.

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

## Manage shared links

```bash
# revoke a link (the underlying file in the drive is left in place)
lightning api "/v1/projects/$PID/shared-artifacts/art_..." -X DELETE

# re-publish the same object later (e.g. after revoking) -> new id, new URL
lightning api "/v1/projects/$PID/shared-artifacts" -X POST \
  -f clusterId="$CLUSTER" -f filename="artifacts/report.html" -f contentType="text/html; charset=utf-8" -q .url
```

Update the contents behind a link by re-uploading (step 1–2) to the same
`artifacts/<name>`; existing shared ids keep serving the new bytes. To change the
served Content-Type, publish again (it re-reads `contentType`).

## Example workflows

Prompts this skill handles: *"share this HTML report as a permanent link"*,
*"give me a public URL for output.pdf"*, *"publish this one-pager so I can send
it"*, *"drop this file somewhere CI can curl it"*.

**Share an agent-generated HTML one-pager** (renders in the browser, no login):

```bash
share summary.html                                 # hand the printed URL to anyone
```

**Publish a folder of reports, one link each:**

```bash
for f in out/*.html; do echo "$f -> $(share "$f" "reports/$(basename "$f")" text/html)"; done
```

**Hand a file to another service / CI job:**

```bash
URL=$(share model-metrics.json)
# elsewhere:  curl -sL "$URL" -o metrics.json
```

## Gotchas

- **The blob-upload endpoint needs the `token` query param, not the API key.**
  `/v1/projects/{pid}/artifacts/blobs/...` returns 401 on plain basic auth; mint a
  token with `POST /v1/auth/login` (`-f apiKey=$LIGHTNING_API_KEY`) and pass it as
  `-f token=...`. The token is short-lived — mint a fresh one per session.
- **`clusterId` is required** on both the blob PUT and the publish call, or the
  storage layer errors. Resolve it from `/v1/projects/{pid}/clusters`.
- **Shares are confined to the `artifacts/` folder.** Publish only finds objects
  under `projects/{pid}/artifacts/...`; upload there (path `artifacts/blobs/artifacts/<name>`).
  Files under `Uploads/` or the `lightning_storage` Drive are a different backend
  and won't be found — `lightning cp` targets those, so it is **not** the upload to
  use here.
- **Content-Type is set at publish time**, not upload time — the serving handler
  uses the shared-artifact record's `contentType`, overriding the stored object.
  Set it on the publish call so HTML/PDF render inline.
- The public link is genuinely open — anyone with it can fetch the file with no
  auth. Use `-F private=true` for anything you don't want world-readable.
- `-q` (jq filtering) needs the `jq` binary installed; without it, drop `-q` and
  parse the JSON yourself.
- **Files >100MB** aren't covered by the single PUT here (they need a multipart
  upload) — rare for a shareable one-pager/report; split or compress if you hit it.
