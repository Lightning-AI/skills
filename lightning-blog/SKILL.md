---
name: lightning-blog
description: Write, edit, illustrate and publish posts on the Lightning AI blog (lightning.ai/blog) with the `lightning` CLI - create a draft, import Markdown with the bundled md2blocks.py, write the body as EditorJS blocks, rasterize SVG charts to dark-mode PNGs and upload them, set title/description/slug/category/author/social image/date, share a review link while unpublished, then publish or unpublish. Requires the internal blog-admin flag. Use when the user wants to draft, update, illustrate, review, publish, unpublish, list, find or delete a lightning.ai blog post, e.g. "post this on the Lightning blog", "turn this writeup into a blog post", "add a chart to the post", "fix the post's slug/author", "show me the blog drafts". Always confirm before publishing.
---

# Lightning AI blog (author, edit and publish lightning.ai/blog posts)

A blog post on `lightning.ai/blog` is **two objects**:

| Object | Owns | Endpoint | Update semantics |
|---|---|---|---|
| **blog post** (`bp_…`) | title, description, category, social image, author, customer name/logo, published date, share-while-draft flag | `/v1/blog-posts` | `PUT` is a **partial merge**: send only what changes |
| **lit page** (`01k…`) | the **body** (EditorJS JSON), the URL slug (`path`), the **published** flag | `/v1/lit-pages/{id}` | `PUT` is a **full replace**: omitting `published` unpublishes a live post, omitting `path` fails with `500` and loses the edit |

Creating a blog post creates its lit page. The public URL is `https://lightning.ai/blog/<path>`.

**Two rules above everything else:**

1. **Never publish without explicit confirmation**, in that message, for that post. Work draft-first
   and share a review link instead.
2. **Change the lit page only through `put_page` below**, which carries the current `path` and
   `published` through unless you mean to change them.

## Setup & auth

```bash
# Use the Lightning AI CLI from the current env; install or upgrade it there if it's missing or older than 2026.9.18
v=$(lightning --version 2>/dev/null | sed -n 's/^Lightning CLI version //p')
[ -n "$v" ] && [ "$(printf '%s\n' 2026.9.18 "$v" | sort -V | head -1)" = 2026.9.18 ] \
  || uv pip install -U lightning-sdk || python3 -m pip install -U lightning-sdk
lightning --version   # must print "Lightning CLI version …"; if not, see the table below
lightning login                        # browser sign-in — enough for everything here
# or: export LIGHTNING_API_KEY=... LIGHTNING_USER_ID=...   # non-interactive (CI, agents)
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

There is no `lightning blog` command group; `lightning api` (a `gh api`-style client) is the whole
interface. Flags: `-X` method, `-f key=val` string field, `-F key=val` typed field, `-H` header,
`--input <file>` body (`-` for stdin), `-q` jq filter, `-i` response headers, `--silent`. `-q` has no
raw mode, so pipe to `jq -r` for a bare string. **Any `-f`/`-F` field turns a request into a POST
unless you pass `-X GET`**: a filtered list without it tries to *create* a post. Set
`LIGHTNING_CLOUD_URL` for a non-prod control plane.

### Check you can edit the blog (do this first)

```bash
lightning api /v1/auth/user -q '{username, id, blogAdmin: .internalBlogAdmin}'
```

Create doesn't check the blog-admin flag, but every update, publish and delete does (`403
unauthorized`). If `blogAdmin` is `false`, stop before creating anything, or you leave an orphan
draft you can't edit or delete. The platform team grants the flag. Blog posts are global, so no
org or teamspace is needed.

## Editor controls → API fields

| Editor control | API field | Object |
|---|---|---|
| `+ Blog Post` / 🗑 delete | `POST` / `DELETE /v1/blog-posts/{id}` | blog post |
| **Show drafts** | `includeUnpublished=true` on the list | — |
| TITLE · DESCRIPTION (also the meta/OG description) | `title` · `description` | blog post |
| IMAGE URL (social/OG and list card) · CUSTOMER NAME · CUSTOMER LOGO URL | `imageUrl` · `customerName` · `customerLogoUrl` | blog post |
| CATEGORY · AUTHOR · PUBLISHED DATE | `category` · `authorId` (a user id) · `publishedAt` (RFC3339) | blog post |
| **Access via link while unpublished** | `unpublishedAccessViaLink` | blog post |
| PATH `/blog/<path>` · **Publish** · body | `path` · `published` · `content` (EditorJS JSON as a string) | **lit page** |

Valid `category` slugs (anything else renders raw in the filter bar): `build`, `ship`, `training`,
`inference`, `optimize`, `opinion`, `data-science`, `releases`, `news`, `case-studies`, `changelog`.

## Write a post (draft first)

### 1. Create the draft and load the helper

```bash
lightning api /v1/blog-posts -X POST \
  -f 'title=How we cut sandbox cold starts to 300 ms' \
  -f 'description=A short, punchy summary — this is also the OG/meta description.' \
  -f 'category=build' \
  -f 'imageUrl=https://assets.lightning.ai/app-2/default-social-preview.jpg'
POST_ID=bp_…          # .id from the response
PAGE_ID=01k…          # .litPageId from the response
```

The slug is derived from the title (lowercased, `[a-z0-9-]` only, a random suffix on collision).
Every change to the lit page goes through one helper:

```bash
# put_page [path] [true|false] — full-replace PUT of the lit page, body from /tmp/post.json.
# An omitted argument keeps the page's current value, so an edit never renames or unpublishes it.
put_page() {
  local cur
  cur=$(lightning api "/v1/blog-posts/$POST_ID" | jq -ce '.litPage | {path, published}') || return 1
  jq -c --argjson cur "$cur" --arg path "${1:-}" --arg pub "${2:-}" '
    {content: tostring,
     path: (if $path == "" then $cur.path else $path end),
     published: (if $pub == "" then ($cur.published // false) else $pub == "true" end)}' \
    /tmp/post.json > /tmp/body.json || return 1
  lightning api "/v1/lit-pages/$PAGE_ID" -X PUT --input /tmp/body.json \
    -q '.LitPage | {path, published, bytes: (.content|length)}'   # capital L, unlike other responses
}
```

### 2. Write or edit the body

The body is EditorJS JSON (see *Content format*). Write it to `/tmp/post.json` and let `put_page`
embed it; never hand-escape it:

```bash
cat > /tmp/post.json <<'JSON'
{"blocks":[
 {"type":"header","data":{"text":"The problem","level":1}},
 {"type":"paragraph","data":{"text":"Cold starts dominated p99. Here is the <a href=\"https://lightning.ai/docs\">background</a>."}},
 {"type":"list","data":{"style":"unordered","items":[
   {"content":"snapshot restore was serialized","items":[]},
   {"content":"the page cache was cold","items":[]}]}},
 {"type":"code","data":{"code":"runsc restore --bundle /run/sandbox\n","language":"bash"}},
 {"type":"table","data":{"withHeadings":true,"content":[["","Before","After"],["p50","4.1 s","0.3 s"]]}},
 {"type":"delimiter","data":{}},
 {"type":"paragraph","data":{"text":"Try it in a <b>Lightning Sandbox</b> today."}}
]}
JSON
put_page
```

To edit a post you didn't write, pull its ids and body down first, then edit and `put_page`:

```bash
P=$(lightning api /v1/blog-posts/<slug-or-id>)
POST_ID=$(printf "%s" "$P" | jq -r .id); PAGE_ID=$(printf "%s" "$P" | jq -r .litPage.id)
printf "%s" "$P" | jq -r .litPage.content > /tmp/post.json
```

### 3. Metadata, author and slug

```bash
lightning api "/v1/blog-posts/$POST_ID" -X PUT -f 'description=…' -f 'category=build'   # partial merge

# author: create ignores authorId and uses the caller. Match the exact username; the search is fuzzy
AUTHOR=$(lightning api /v1/users/search -X GET -f 'query=karolis' \
  | jq -r --arg u karolis '.users[] | select(.username==$u) | .id')
lightning api "/v1/blog-posts/$POST_ID" -X PUT -f "authorId=$AUTHOR"

lightning api "/v1/blog-posts/$POST_ID" -X PUT -f 'publishedAt=2026-08-04T09:00:00Z'  # date readers see

put_page sandbox-cold-starts-300ms      # rename the slug
```

**Settle the slug before sharing.** Renaming leaves no redirect; the old path starts returning `500`.
A taken slug is rejected with `409 path already taken`. The blog-post `PUT` response shows
`litPage.path` as `""`; that's a response bug, so re-`GET` the post rather than "fixing" it.

### 4. Share the draft for review

A draft is invisible to everyone but blog admins. Offer a review link instead of publishing:

```bash
lightning api "/v1/blog-posts/$POST_ID" -X PUT -F 'unpublishedAccessViaLink=true'
echo "https://lightning.ai/blog/$(lightning api "/v1/blog-posts/$POST_ID" | jq -r .litPage.path)"
```

### 5. Publish, only after the user confirms

**Stop and ask**, showing what goes live:

```
Ready to publish:
  title      How we cut sandbox cold starts to 300 ms
  url        https://lightning.ai/blog/sandbox-cold-starts-300ms
  category   build · author karolis · date 2026-08-04
Publish this to the public blog now? (it appears immediately on lightning.ai/blog)
```

Only then:

```bash
lightning api "/v1/blog-posts/$POST_ID" -X PUT -f "publishedAt=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
put_page "" true
SLUG=$(lightning api "/v1/blog-posts/$POST_ID" | jq -r .litPage.path)
curl -s -o /dev/null -w 'anon GET: %{http_code}\n' "${LIGHTNING_CLOUD_URL:-https://lightning.ai}/v1/blog-posts/$SLUG"
```

Publishing is immediate and indexable; a future `publishedAt` only changes the displayed date. To
pull a post down, `put_page "" false`: that needs no confirmation, but republishing does.

## Content format: EditorJS blocks

`content` is `{"blocks":[…]}`. Every `text`/`content`/caption field is **HTML**, not Markdown: `<b>`,
`<i>`, `<a href="…">`, `<code class="inline-code">`, with `&`, `<`, `>` escaped. Block `id` and `time`
are optional.

| Block | `data` shape | Notes |
|---|---|---|
| `header` | `{"text":"…","level":1}` | blog sections use `level: 1`; headers become the outline nav |
| `paragraph` | `{"text":"…"}` | |
| `list` | `{"style":"unordered"\|"ordered","items":[{"content":"…","items":[]}]}` | `items` holds sub-bullets |
| `code` | `{"code":"…","language":"python"}` | defaults to `python`; `bash`, `javascript` highlighted |
| `image` | `{"file":{"url":"…"},"caption":"","withBorder":false,"stretched":false,"withBackground":false}` | upload first (below) |
| `table` | `{"withHeadings":true,"content":[["…","…"],["…","…"]]}` | row-major strings |
| `delimiter` | `{}` | section break |
| `embed` | `{"service":"youtube","source":"…","embed":"…","width":600,"height":300,"caption":""}` | `youtube`, `x`/`twitter`, `vimeo`, `github`, `codepen`, `loom`, `video` (direct file) |
| `studios` / `deployments` | `{"value":"https://lightning.ai/<org>/environments/<studio>"}` | Studio / AI Hub cards; one URL per line |
| `checklist` | `{"items":[{"text":"…","checked":false}]}` | upstream shape; no post uses it yet |

House style: a `header` per section, short paragraphs, a `list` instead of a run-on sentence, a
`code` block for anything a reader copies, a `table` for before/after numbers.

## Import a Markdown draft (`md2blocks.py`)

```bash
python3 md2blocks.py draft.md > /tmp/post.json     # next to this skill; "-" reads stdin
```

It converts headings (`##` → level-1 sections), paragraphs, nested lists, fenced code, GFM tables,
`---`, images, block quotes (→ emphasised paragraph) and inline bold/italic/code/links, escaping
HTML. It drops the leading `# Title` (`--keep-title` keeps it). On **stderr** it lists what needs a
human: images not yet on lightning.ai storage, and each `<figure>`/`<svg>`, which becomes an empty
`image` block to rasterize, upload and patch:

```
46 blocks: {'paragraph': 29, 'list': 3, 'header': 7, 'table': 1, 'image': 5, 'delimiter': 1}
TODO figure 1: rasterize the SVG, upload it, patch image block 8
```

Read the result before pushing it, especially tables and paragraphs that mixed Markdown with HTML.

## Images, charts and diagrams

### House style

Charts use a **dark canvas** a shade above the page, light text, one accent hue and a source line:

| Role | Colour |
|---|---|
| canvas | `#16161d` |
| primary text | `#f4f4f5` · secondary `#a1a1aa` |
| brand accent | `#8b5cf6` (violet) / `#a78bfa` light |
| categorical series | `#38bdf8` sky · `#22c55e` green · `#f97316` orange · `#a78bfa` violet |

If a `dataviz` skill is installed, follow it for chart design and this section for getting the
result onto the blog. Put the source in the block `caption` as well as in the chart.

### Author as SVG, upload as PNG

Rasterizers get web SVG wrong in three ways, so fix them first:
- **`currentColor` becomes black**, invisible on the dark canvas (also when an SVG is uploaded as-is
  and shown through `<img>`). Pin every colour to a literal.
- **No external CSS, webfonts or `<style>` rules**: presentation attributes and inline `style` only.
- **No Inter**, so text renders wider: pad the `viewBox` so edge labels don't clip.

```bash
# 1. pin colours and pad the viewBox (26px sides, 10px top/bottom)
python3 - chart.svg <<'EOF'
import re, sys, pathlib
p = pathlib.Path(sys.argv[1]); s = p.read_text().replace('currentColor', '#f4f4f5')
m = re.search(r'viewBox="([\d.\- ]+)"', s); x, y, w, h = map(float, m.group(1).split())
p.write_text(s[:m.start()] + f'viewBox="{x-26:g} {y-10:g} {w+52:g} {h+20:g}"' + s[m.end():])
EOF

# 2. rasterize at ~2x display width, on the dark canvas
rsvg-convert -w 1400 --background-color="#16161d" -o chart.png chart.svg
# brew install librsvg  (or: python3 -m pip install cairosvg && cairosvg -W 1400 …)
# no rsvg? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new \
#   --window-size=1400,1000 --default-background-color=16161d --screenshot=chart.png "file://$PWD/chart.svg"

# 3. look at the PNG before uploading, and keep it well under 500 KB
du -k chart.png
```

Screenshots and matplotlib output need none of this: check the width (≥1200 px) and the size.

### Upload and place

Uploads are keyed to the **lit page** and land in a **public** bucket, readable before the post is
published. The endpoint is multipart, which `lightning api` can't send, so use `curl` with the CLI's
credentials:

```bash
if [ -n "${LIGHTNING_USER_ID:-}" ]; then AUTH=(-u "$LIGHTNING_USER_ID:$LIGHTNING_API_KEY")
elif [ -n "${LIGHTNING_API_KEY:-}" ]; then AUTH=(-H "Authorization: Bearer $LIGHTNING_API_KEY")
else AUTH=(-u "$(jq -r '"\(.user_id):\(.api_key)"' "${LIGHTNING_CREDENTIAL_PATH:-$HOME/.lightning/credentials.json}")")
fi
UPLOAD_URL=$(curl -sf "${AUTH[@]}" \
  -F "file=@./diagram.png" \
  "${LIGHTNING_CLOUD_URL:-https://lightning.ai}/v1/media/lit_page/$PAGE_ID/image" | jq -er .url) \
  || echo "image upload failed — check auth and PAGE_ID before using UPLOAD_URL" >&2
lightning api "/v1/blog-posts/$POST_ID" -X PUT -f "imageUrl=$UPLOAD_URL"   # as the social image
```

Or reference `UPLOAD_URL` in an `image` block. To fill the empty image blocks `md2blocks.py` left,
upload in document order and patch them in the same order, then `put_page`:

```bash
# uploaded.txt: one "<name>\t<url>" line per figure, in document order
python3 - <<'EOF'
import json
blocks = json.load(open('/tmp/post.json'))
urls = [line.split('\t')[1].strip() for line in open('uploaded.txt')]
empty = [b for b in blocks['blocks'] if b['type'] == 'image' and not b['data']['file']['url']]
assert len(empty) == len(urls), (len(empty), len(urls))
for block, url in zip(empty, urls):
    block['data']['file']['url'] = url
json.dump(blocks, open('/tmp/post.json', 'w'), ensure_ascii=False)
EOF
```

JPEG, PNG, GIF, WebP and `.svg` are accepted; prefer PNG, since social previews need a raster
image. Stay under the editor's caps (500 KB social image, 100 KB customer logo) so the post stays
editable in the UI. The blog renders client-side, so `curl` shows nothing: check the rendered post
in a browser or with a headless screenshot:

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new \
  --window-size=1300,2400 --virtual-time-budget=15000 --screenshot=render.png \
  "https://lightning.ai/blog/<slug>"
```

## Find, list and delete posts

```bash
# published posts (works unauthenticated); limit/pageToken are ignored, so this returns every post
lightning api /v1/blog-posts | jq -r '.blogPosts[] | [.litPage.path, .title] | @tsv'

# drafts: blog admins only, and -X GET is required
lightning api /v1/blog-posts -X GET -F includeUnpublished=true \
  | jq -r '.blogPosts[] | select(.litPage.published != true) | [.litPage.path, .title, .author.username, .createdAt] | @tsv'

# one post, by slug or id
lightning api /v1/blog-posts/sandbox-cold-starts-300ms \
  -q '{id, title, category, author:.author.username, published:.litPage.published, publishedAt}'

lightning api /v1/blog-posts -X GET -f category=case-studies | jq -r '.blogPosts[].title'

# delete: removes the body and slug too, but not uploaded images. Irreversible; confirm first
lightning api "/v1/blog-posts/$POST_ID" -X DELETE
```

In the drafts list, skip `<Blog Post Title>` entries: they're abandoned drafts from the UI's create
button.

## Example workflows

- **"Turn this benchmark writeup into a blog post."** Check the admin flag → create the draft →
  convert to blocks (or `md2blocks.py`) → `put_page` → upload the chart as `imageUrl` → share the
  review link → **ask** before publishing.
- **"Post this Markdown file, it has charts in it."** As above, plus: fix and rasterize each figure
  the TODO list names, upload in document order, patch the image blocks, settle the slug with
  `put_page <slug>`, and screenshot the draft before handing it back.
- **"Fix the slug and author on the July recap, then publish it."** Pull the ids and body → set
  `authorId` → `put_page <new-slug>` → show the summary and **ask** → publish.

## Gotchas

- **A nonexistent slug or id returns `500`, not `404`** (`record not found`, wrapped). An unshared
  draft returns `404` to non-admins.
- **`includeUnpublished=true` needs the blog-admin flag**: `401` unauthenticated, `403 unauthorized to
  view unpublished posts` otherwise.
- **An unknown `authorId` is rejected with `400 author not found`**: the author must be an existing
  Lightning user.
