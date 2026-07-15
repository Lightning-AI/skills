---
name: lightning-artifacts
description: Publish a local file (HTML report, PDF, image, dataset sample, build output) to Lightning AI and get a durable, public lightning.ai/artifacts/<id> link that never expires and renders inline in the browser - plus list what's published, unpublish (revoke) links, and delete the files behind them - entirely through the `lightning` CLI (uvx lightning-sdk) with regular auth (`lightning login` or an API key), no code. Use when the user wants to share a file, a generated one-pager, or an agent-made artifact as a permanent URL, hand a file to a teammate or CI job, see or revoke existing shared links, or asks to "get a public / shareable link for this file".
---

# Lightning AI Artifacts (durable shareable file links)

Publish any file to a teamspace and get back a **durable public URL** —
`https://lightning.ai/artifacts/<id>` — that anyone can open with no Lightning
login, renders inline in the browser (HTML/PDF/images), and **never expires**.
The control plane streams the bytes from storage on every request, so unlike a
presigned S3 URL there is no ~1h cap. Great for agent-generated one-pagers,
reports, dashboards, dataset samples, or build artifacts.

**This whole flow runs through the `lightning` CLI** (`uvx lightning-sdk`) —
authenticated REST calls via `lightning api` against the project storage API,
plus one unauthenticated `curl` PUT to a presigned URL. No Python, no SDK code.

## Setup & auth

```bash
uvx lightning-sdk --version          # the CLI; no install needed (uvx runs it ad-hoc)
lightning login                       # interactive browser sign-in — enough for everything here
# or: export LIGHTNING_API_KEY=...    # non-interactive alternative (CI, agents)
```

Either credential works for **every call in this skill** — no token minting or
extra auth steps. Get a key from lightning.ai → user/org settings, or
`lightning api-key create --org <org> --name artifacts`. **Never hardcode the
key** — read it from the environment. To target a non-prod control plane, set
`LIGHTNING_CLOUD_URL` (default `https://lightning.ai`).

`lightning api` is a `gh api`-style raw HTTP client: `-X` method, `-f key=val`
string field, `-F key=val` typed field, `-H` header, `--input <file>` request
body (`--input /dev/stdin` to pipe one), `-q` jq filter (needs the `jq` binary
for `-q`), `-i` include response headers. Fields are JSON body for
POST/PUT-with-body and **query params** when the request also has `--input` or
is a GET.

## Resolve teamspace, project id, and cloud account (do this first)

Artifacts live in a teamspace (a "project" in the REST API). **Never guess.**
List memberships and, if more than one fits and none is configured, **ask the
user which to use**:

```bash
lightning api /v1/memberships -q '.memberships[] | [.ownerType, .name, .projectId] | @tsv'
```

Pick the row you want and capture its `projectId`, then resolve that project's
cloud account (the `clusterId`, required on the storage + publish calls):

```bash
PID=<projectId-from-above>
CLUSTER=$(lightning api "/v1/projects/$PID/clusters" -q '.clusters[0].id' | tr -d '"')   # e.g. "aws-use1"
```

## Publish a durable link (the CLI flow)

The project storage API does a standard multipart upload: start it, fetch a
presigned URL per part, `PUT` the bytes straight to cloud storage, finalize,
then register the object as a shared artifact. Copy-paste function:

```bash
# share <local-file> [remote-name] [content-type]
# Pretty status goes to stderr; the bare URL is the only thing on stdout, so
# URL=$(share file.html) captures cleanly while interactive use looks like:
#   ✅ Published report.html (text/html; charset=utf-8)
#   🔗 https://lightning.ai/artifacts/art_...
share() {
  local FILE="$1" NAME="${2:-$(basename "$1")}" CT="${3:-$(file -b --mime-type "$1")}"
  local KEY="artifacts/${NAME#artifacts/}"     # publish only finds objects under artifacts/
  # 1. start the upload
  local UP; UP=$(printf '{"clusterId":"%s","filename":"%s"}' "$CLUSTER" "$KEY" \
    | lightning api "/v1/projects/$PID/storage" -X POST --input /dev/stdin -q .uploadId | tr -d '"') || return 1
  # 2. presigned URL for part 1 (single part is fine up to 5 GiB)
  local PART_URL; PART_URL=$(printf '{"clusterId":"%s","filename":"%s","parts":[1]}' "$CLUSTER" "$KEY" \
    | lightning api "/v1/projects/$PID/storage/uploads/$UP" -X POST --input /dev/stdin -q '.urls[0].url' | tr -d '"') || return 1
  # 3. PUT the bytes straight to storage (no auth — the URL is presigned); keep the ETag
  local ETAG; ETAG=$(curl -sf -D - -o /dev/null -X PUT "$PART_URL" \
    -H "Content-Type: $CT" --data-binary @"$FILE" \
    | tr -d '\r"' | awk 'tolower($1)=="etag:"{print $2}') || return 1
  # 4. finalize the upload
  printf '{"clusterId":"%s","filename":"%s","uploadId":"%s","parts":[{"partNumber":1,"etag":"%s"}]}' \
    "$CLUSTER" "$KEY" "$UP" "$ETAG" \
    | lightning api "/v1/projects/$PID/storage/complete" -X POST --input /dev/stdin --silent || return 1
  # 5. register it -> durable, no-expiry lightning.ai/artifacts/<id>
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

The upload `filename` and the publish `filename` must point at the same object —
the `KEY` variable keeps them identical. The server confines shares to the
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

## See what's published, unpublish, delete

**List** every published link in the project (`GET /v1/projects/{pid}/shared-artifacts`,
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

```
🌐 artifacts/report.html  ·  ⬇ 42  ·  2026-07-14T12:39:56Z
   id: art_01kxgaep54zzs84arfns1j21wd
   🔗 https://lightning.ai/artifacts/art_01kxgaep54zzs84arfns1j21wd
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
upload). Note the query params inline in the path — see Gotchas:

```bash
lightning api "/v1/projects/$PID/storage?clusterId=$CLUSTER&filename=artifacts/report.html" -X DELETE
```

Re-publish the same object later (new id, new URL) by repeating the publish call
with the same `filename`. Update the contents behind an existing link by
re-uploading (steps 1–4 of `share`) to the same `artifacts/<name>` — published ids
keep serving the new bytes. To change the served Content-Type, publish again.

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

- **`clusterId` is required** on the storage and publish calls, or the storage
  layer errors. Resolve it from `/v1/projects/{pid}/clusters`.
- **Repeated/nested fields can't be expressed with `-f`** (`parts: [1]`,
  `parts: [{partNumber, etag}]`) — pipe a JSON body via `--input /dev/stdin`
  instead, as `share` does. Remember `-f` fields turn into query params when
  `--input` is present.
- **On DELETE, `-f` fields land in the request body**, which the storage API
  ignores — it wants query params. Inline them in the path:
  `"/v1/projects/$PID/storage?clusterId=…&filename=…"`.
- **The ETag header comes back wrapped in quotes** from cloud storage — strip
  them before the `complete` call (the `tr -d '\r"'` in `share` does).
- **Shares are confined to the `artifacts/` folder.** Publish only finds objects
  under `projects/{pid}/artifacts/...`, so the upload `filename` must start with
  `artifacts/`. Files under `Uploads/` or the `lightning_storage` Drive are a
  different backend and won't be found — `lightning cp` targets those, so it is
  **not** the upload to use here.
- **Content-Type is set at publish time**, not upload time — the serving handler
  uses the shared-artifact record's `contentType`, overriding the stored object.
  Set it on the publish call so HTML/PDF render inline.
- The public link is genuinely open — anyone with it can fetch the file with no
  auth. Use `-F private=true` for anything you don't want world-readable.
- `-q` (jq filtering) needs the `jq` binary installed; without it, drop `-q` and
  parse the JSON yourself.
- **The list endpoint is newer than the rest.** `GET
  /v1/projects/{pid}/shared-artifacts` returns HTTP 501 "Method Not Allowed" on
  control planes built before mid-July 2026 — publish/unpublish still work
  there; only `shares` is affected.
- **Files over 5 GiB** need more than one part: request them all in step 2
  (`"parts": [1, 2, …]`), PUT each chunk (every part except the last must be
  ≥ 5 MiB), and pass all `{partNumber, etag}` pairs to `complete`. A single part
  covers the typical report/one-pager by a wide margin.
