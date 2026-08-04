---
name: lightning-blog
description: Write, edit, illustrate and publish posts on the Lightning AI blog (lightning.ai/blog) through the `lightning` CLI (uvx lightning-sdk) - create a draft, import a Markdown file with the bundled md2blocks.py converter, write the body as EditorJS blocks, turn SVG charts and diagrams into dark-mode PNGs and upload them, set title/description/slug/category/author/social image/published date, share a review link while still unpublished, then publish or unpublish. Requires the internal blog-admin flag on your Lightning account. Use when the user wants to draft, update, illustrate, chart, review, publish, unpublish, list, find or delete a lightning.ai blog post, asks to "post this on the Lightning blog", "turn this markdown/writeup into a blog post", "add a chart to the blog post", "fix the blog post title/slug/author", or "show me the blog drafts". Always confirm with the user before publishing (making a post non-draft).
---

# Lightning AI blog (author, edit and publish lightning.ai/blog posts)

A blog post on `lightning.ai/blog` is **two objects**:

| Object | Owns | Endpoint |
|---|---|---|
| **blog post** (`bp_…`) | title, description, category, social image, author, customer name/logo, published date, share-while-draft flag | `/v1/blog-posts` |
| **lit page** (`01k…`) | the post **body** (EditorJS block JSON), the URL slug, and the **published** flag | `/v1/lit-pages/{id}` |

Creating a blog post creates its lit page automatically. The public URL is
`https://lightning.ai/blog/<lit-page path>`.

**Two rules that matter more than anything else in this skill:**

1. **Never publish without explicit user confirmation.** Everything here is
   draft-first: create as a draft, share a review link, and only flip
   `published: true` after the user says so, in that message, for that post.
2. **`PUT /v1/lit-pages/{id}` replaces the whole record.** Always send
   `content` + `path` + `published` together, or you will blank the slug or
   silently unpublish a live post. (See Gotchas.)

## Setup & auth

```bash
uvx lightning-sdk --version           # the CLI; no install needed (uvx runs it ad-hoc)
lightning login                        # browser sign-in — enough for everything here
# or: export LIGHTNING_API_KEY=... LIGHTNING_USER_ID=...   # non-interactive (CI, agents)
```

`lightning api` (the `gh api`-style raw client) is the whole interface here —
there is no dedicated `lightning blog` command group. Flags: `-X` method,
`-f key=val` string field, `-F key=val` typed field, `-H` header, `--input
<file>` request body (`--input /dev/stdin` to pipe), `-q` jq filter (needs the
`jq` binary), `-i` include response headers, `--silent` suppress the body.
`-q` has no raw mode (there is no `-q -r`) — it prints jq's JSON output, so
pipe the response to `jq -r` whenever you need a bare string.

If the installed `lightning` is older than the `api` subcommand
(`Error: No such command 'api'`), call it through `uvx lightning-sdk api …`.
Set `LIGHTNING_CLOUD_URL` to target a non-prod control plane (default
`https://lightning.ai`).

### Check you can actually edit the blog (do this first)

Every write except create is gated on the internal blog-admin flag:

```bash
lightning api /v1/auth/user -q '{username, id, blogAdmin: .internalBlogAdmin}'
```

If `blogAdmin` is `false`, stop and tell the user: they can create a post but
every update, publish and delete will come back `403 unauthorized`, leaving an
orphan draft. The flag is granted by the platform team.

No org/teamspace resolution is needed — blog posts are global, not
project-scoped.

## What each control in the blog editor maps to

The in-app editor (the sidebar on `lightning.ai/blog/<slug>` in edit mode) is a
thin shell over these fields:

| Editor control | API field | Where |
|---|---|---|
| `+ Blog Post` button | — | `POST /v1/blog-posts` |
| **Show drafts** toggle | `includeUnpublished=true` | `GET /v1/blog-posts` |
| TITLE | `title` | blog post |
| DESCRIPTION | `description` (also the meta/OG description) | blog post |
| PATH `/blog/<path>` | `path` | **lit page** |
| CUSTOMER NAME | `customerName` (case studies) | blog post |
| IMAGE URL | `imageUrl` — social/OG + list-card image | blog post |
| CUSTOMER LOGO URL | `customerLogoUrl` | blog post |
| CATEGORY | `category` (slug, see below) | blog post |
| AUTHOR | `authorId` (a Lightning user id) | blog post |
| PUBLISHED DATE | `publishedAt` (RFC3339) | blog post |
| **Publish** toggle | `published` | **lit page** |
| **Access via link while unpublished** | `unpublishedAccessViaLink` | blog post |
| body / blocks | `content` (EditorJS JSON as a string) | **lit page** |
| 🗑 delete | — | `DELETE /v1/blog-posts/{id}` |

Valid `category` slugs (anything else renders as the raw slug in the filter bar):
`build`, `ship`, `training`, `inference`, `optimize`, `opinion`,
`data-science`, `releases`, `news`, `case-studies`, `changelog`.

## Write a new post (the draft-first flow)

### 1. Create the draft

```bash
lightning api /v1/blog-posts -X POST \
  -f 'title=How we cut sandbox cold starts to 300 ms' \
  -f 'description=A short, punchy summary — this is also the OG/meta description.' \
  -f 'category=build' \
  -f 'imageUrl=https://assets.lightning.ai/app-2/default-social-preview.jpg'
```

Keep the ids from the response — you need both:

```bash
POST_ID=bp_…          # .id        → /v1/blog-posts/$POST_ID
PAGE_ID=01k…          # .litPageId → /v1/lit-pages/$PAGE_ID
SLUG=$(lightning api "/v1/blog-posts/$POST_ID" | jq -r '.litPage.path')
```

The slug is derived from the title at create time (lowercased, non
`[a-z0-9-]` stripped, `-`-joined; a short random suffix is appended if it
collides). Rename it in step 3 if you want something shorter — but only before
you share the link.

### 2. Write the body

The body is EditorJS block JSON, stored as a **string** inside the lit page.
Write the blocks to a file, then let `jq` do the embedding (never hand-escape):

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

# ALWAYS send content + path + published together — this PUT is a full replace.
jq -c --arg path "$SLUG" '{content:(.|tostring), path:$path, published:false}' \
  /tmp/post.json > /tmp/body.json
lightning api "/v1/lit-pages/$PAGE_ID" -X PUT --input /tmp/body.json \
  -q '.LitPage | {path, published, bytes: (.content|length)}'
```

Re-run the same two commands for every edit: fetch → edit `/tmp/post.json` →
`jq` → `PUT`. To edit a body you didn't write, pull it down first:

```bash
lightning api "/v1/blog-posts/$SLUG" | jq -r '.litPage.content' > /tmp/post.json
```

### 3. Metadata, slug and author

`PUT /v1/blog-posts/{id}` is a **partial merge** — send only what changes:

```bash
# title / description / category / social image
lightning api "/v1/blog-posts/$POST_ID" -X PUT \
  -f 'title=How we cut sandbox cold starts to 300 ms' \
  -f 'description=…' -f 'category=build'

# author: resolve the user id from an exact username or email first
AUTHOR=$(lightning api /v1/users/search -X GET -f 'query=karolis' | jq -r '.users[0].id')
lightning api "/v1/blog-posts/$POST_ID" -X PUT -f "authorId=$AUTHOR"

# published date (what the post displays; falls back to createdAt)
lightning api "/v1/blog-posts/$POST_ID" -X PUT -f 'publishedAt=2026-08-04T09:00:00Z'
```

The slug lives on the lit page, so rename it through the full-replace PUT:

```bash
SLUG=sandbox-cold-starts-300ms
jq -c --arg path "$SLUG" '{content:(.|tostring), path:$path, published:false}' \
  /tmp/post.json > /tmp/body.json
lightning api "/v1/lit-pages/$PAGE_ID" -X PUT --input /tmp/body.json -q '.LitPage.path'
```

### 4. Share the draft for review

A draft is invisible to everyone but blog admins. Flip one flag to hand out a
review link **without publishing**:

```bash
lightning api "/v1/blog-posts/$POST_ID" -X PUT -F 'unpublishedAccessViaLink=true'
echo "https://lightning.ai/blog/$SLUG"
```

This is the right way to let the user see a post before it goes live — offer it
instead of publishing.

### 5. Publish — only after the user confirms

**Stop and ask.** Show the user what is about to go live and get an explicit
yes for this post:

```
Ready to publish:
  title      How we cut sandbox cold starts to 300 ms
  url        https://lightning.ai/blog/sandbox-cold-starts-300ms
  category   build
  author     karolis
  date       2026-08-04
  preview    https://lightning.ai/blog/sandbox-cold-starts-300ms  (draft link)
Publish this to the public blog now? (it appears immediately on lightning.ai/blog)
```

Only then:

```bash
lightning api "/v1/blog-posts/$POST_ID" -X PUT -f "publishedAt=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
jq -c --arg path "$SLUG" '{content:(.|tostring), path:$path, published:true}' \
  /tmp/post.json > /tmp/publish.json
lightning api "/v1/lit-pages/$PAGE_ID" -X PUT --input /tmp/publish.json \
  -q '.LitPage | {path, published}'
curl -s -o /dev/null -w 'anon GET: %{http_code}\n' "https://lightning.ai/v1/blog-posts/$SLUG"
```

Unpublishing is the same call with `published:false` — that one is safe to do
promptly if the user asks to pull a post.

## Content format: EditorJS blocks

`content` is `{"blocks":[…]}`. Every `text`/`content`/caption field is **HTML**,
not markdown: use `<b>`, `<i>`, `<a href="…">`, `<code class="inline-code">`,
and escape literal `&`, `<`, `>` as `&amp;` `&lt;` `&gt;`. Block `id` and `time`
are optional — the editor fills them in.

| Block | `data` shape | Notes |
|---|---|---|
| `header` | `{"text":"…","level":1}` | `level` 1–3; blog sections use `level: 1`, and headers become the right-hand outline nav |
| `paragraph` | `{"text":"…"}` | the workhorse |
| `list` | `{"style":"unordered"\|"ordered","items":[{"content":"…","items":[]}]}` | nested list plugin — `items` is for sub-bullets |
| `code` | `{"code":"…","language":"python"}` | `language` defaults to `python`; `bash`, `javascript` are highlighted |
| `image` | `{"file":{"url":"…"},"caption":"","withBorder":false,"stretched":false,"withBackground":false}` | upload first (below), then reference the returned URL |
| `table` | `{"withHeadings":true,"content":[["…","…"],["…","…"]]}` | row-major array of strings |
| `delimiter` | `{}` | section break |
| `embed` | `{"service":"youtube","source":"…","embed":"…","width":600,"height":300,"caption":""}` | `youtube`, `x`/`twitter`, `vimeo`, `github`, `codepen`, `loom`, `video` (direct `.mp4`/`.webm`/`.mov`) |
| `studios` | `{"value":"https://lightning.ai/<org>/environments/<studio>"}` | renders a Studio card; one URL per line for several cards |
| `deployments` | `{"value":"https://lightning.ai/<org>/models/<id>"}` | AI Hub deployment cards, same newline-separated format |
| `checklist` | `{"items":[{"text":"…","checked":false}]}` | upstream plugin shape; no existing post uses it |

Prose that actually reads like the Lightning blog: a `header`-per-section, short
paragraphs, a `list` where you'd otherwise write a run-on sentence, a `code`
block for anything a reader would copy, and a `table` for before/after numbers.

## Import a Markdown draft (`md2blocks.py`)

Most posts start life as a Markdown file. `md2blocks.py`, next to this skill,
converts one into blocks so you don't hand-build the JSON:

```bash
python3 md2blocks.py draft.md > /tmp/post.json     # or "-" for stdin
```

It handles headings (`##` → `header` level 1 sections), paragraphs, nested
bullet/numbered lists, fenced code with its language, GFM tables, `---` rules,
images, block quotes (→ emphasised paragraph, since the editor has no quote
tool), and inline `**bold**` / `*italic*` / `` `code` `` / links — escaping HTML
as it goes. The leading `# Title` is dropped (it belongs in the post metadata;
`--keep-title` overrides).

It prints a TODO list on **stderr** for everything needing a human: each
`![](…)` whose URL isn't a lightning.ai storage URL yet, and each
`<figure>`/`<svg>` — those become empty `image` blocks (caption taken from the
SVG `<title>`) for you to rasterize, upload and patch:

```
46 blocks: {'paragraph': 29, 'list': 3, 'header': 7, 'table': 1, 'image': 5, 'delimiter': 1}
TODO figure 1: rasterize the SVG, upload it, patch image block 8
```

Read the converted blocks before pushing them — check the tables, and re-check
any paragraph that mixed Markdown with raw HTML.

## Images, charts and diagrams

### House style

Existing posts use **dark-canvas** charts: a `#16161d` panel that sits a shade
above the page's near-black background, light text, one accent hue, and a source
line. Reuse that palette so a new chart doesn't look pasted in:

| Role | Colour |
|---|---|
| canvas | `#16161d` |
| primary text | `#f4f4f5` · secondary `#a1a1aa` |
| brand accent | `#8b5cf6` (violet) / `#a78bfa` light |
| categorical series | `#38bdf8` sky · `#22c55e` green · `#f97316` orange · `#a78bfa` violet |

If a `dataviz` skill is installed, follow it for chart *design* (form choice,
palette rules, labelling) and this section for getting the result onto the blog.
Put the source attribution in the block `caption` as well as in the chart, so it
survives if someone re-uses the image.

### Author the chart as SVG, then rasterize

Upload **PNG**. Write the chart as SVG (hand-written, or exported from
matplotlib/plotly/d3) and rasterize it — but SVG written for a web page needs
three fixes first, or the PNG comes out wrong:

- **No `currentColor`.** Rasterizers resolve it to **black**, which is invisible
  on a dark canvas. Pin every `currentColor` to a literal (`#f4f4f5`).
- **No external CSS, webfonts, or `<style>` rules** — only presentation
  attributes and `style="…"` on the element. The renderer has no Inter, so it
  substitutes a wider fallback: leave slack around edge-anchored text.
- **Pad the `viewBox`** rather than moving coordinates, so that wider fallback
  text doesn't clip at the border.

```bash
# 1. fix colours and pad the viewBox (26px sides, 10px top/bottom)
python3 - chart.svg <<'EOF'
import re, sys, pathlib
p = pathlib.Path(sys.argv[1]); s = p.read_text().replace('currentColor', '#f4f4f5')
m = re.search(r'viewBox="([\d.\- ]+)"', s); x, y, w, h = map(float, m.group(1).split())
p.write_text(s[:m.start()] + f'viewBox="{x-26:g} {y-10:g} {w+52:g} {h+20:g}"' + s[m.end():])
EOF

# 2. rasterize at ~2x display width for retina, on the dark canvas
rsvg-convert -w 1400 --background-color="#16161d" -o chart.png chart.svg
# brew install librsvg  (or: python3 -m pip install cairosvg && cairosvg -W 1400 …)

# no rsvg? headless Chrome works too:
# "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new \
#   --window-size=1400,1000 --default-background-color=16161d \
#   --screenshot=chart.png "file://$PWD/chart.svg"

# 3. LOOK at it before uploading — clipped labels and black-on-black text are
#    invisible in the JSON but obvious in the PNG.
du -k chart.png     # keep well under 500 KB (the editor's cap for the social image)
```

Screenshots, photos and matplotlib output need none of this — just check the
width (≥1200 px reads well) and the file size.

### Upload and place

Images (both the social/list image and inline `image` blocks) are uploaded to
the post's **lit page**. This endpoint is multipart, which `lightning api`
doesn't do — use `curl` with basic auth (`user_id:api_key`, the same pair the
CLI stores in `~/.lightning/credentials.json`):

```bash
UPLOAD_URL=$(curl -s -u "$LIGHTNING_USER_ID:$LIGHTNING_API_KEY" \
  -F "file=@./diagram.png" \
  "https://lightning.ai/v1/media/lit_page/$PAGE_ID/image" | jq -r .url)
# → https://storage.googleapis.com/lightning-avatars/litpages/<page-id>/<uuid>.png
```

Then use it as the social image:

```bash
lightning api "/v1/blog-posts/$POST_ID" -X PUT -f "imageUrl=$UPLOAD_URL"
```

or as an inline block:

```json
{"type":"image","data":{"file":{"url":"…"},"caption":"Cold-start breakdown","withBorder":false,"stretched":false,"withBackground":false}}
```

To fill in several figures at once — e.g. the empty `image` blocks `md2blocks.py`
left behind — upload in document order and patch them in the same order:

```bash
# uploaded.txt: one "<name>\t<url>" line per figure, in the order they appear
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

Accepted: JPEG, PNG, GIF, WebP — plus `.svg`, which the endpoint takes based on
the file extension (verified). Prefer PNG anyway: it's what every existing post
uses, social/OG previews need a raster image, and an SVG loaded through `<img>`
gets no page CSS, so it needs the same explicit-colour treatment as a rasterized
one. The editor caps the social image at 500 KB and the customer logo at 100 KB —
stay under those so the post can still be edited in the UI.

Finally, **look at the rendered post**, not just the JSON — the blog renders
client-side, so `curl` shows nothing:

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new \
  --window-size=1300,2400 --virtual-time-budget=15000 --screenshot=render.png \
  "https://lightning.ai/blog/$SLUG"
```

## Find, list and delete posts

```bash
# published posts (works unauthenticated)
lightning api /v1/blog-posts | jq -r '.blogPosts[] | [.litPage.path, .title] | @tsv'

# include drafts — blog admins only ("Show drafts" in the UI). -X GET is REQUIRED.
lightning api /v1/blog-posts -X GET -F includeUnpublished=true \
  | jq -r '.blogPosts[] | select(.litPage.published==false) | [.litPage.path, .title] | @tsv'

# one post, by slug or by id
lightning api /v1/blog-posts/sandbox-cold-starts-300ms \
  -q '{id, title, category, author:.author.username, published:.litPage.published, publishedAt}'

# filter by category
lightning api /v1/blog-posts -X GET -f category=case-studies | jq -r '.blogPosts[].title'

# delete (also deletes the body/lit page — irreversible, confirm with the user)
lightning api "/v1/blog-posts/$POST_ID" -X DELETE
```

## Example workflows

**"Turn this benchmark writeup into a Lightning blog post."**
Check `internalBlogAdmin` → `POST /v1/blog-posts` (title, description,
`category=build`) → convert the writeup to blocks (`header`/`paragraph`/
`list`/`code`/`table`) → PUT the lit page with `published:false` → upload the
chart PNG and set it as `imageUrl` → set `unpublishedAccessViaLink=true` and
hand back the draft URL → **ask** before publishing.

**"Post this Markdown file, it has charts in it."**
`md2blocks.py draft.md > /tmp/post.json` → create the post → for each figure the
TODO list names: fix `currentColor`, pad the `viewBox`, `rsvg-convert -w 1400
--background-color="#16161d"`, eyeball the PNG → upload all of them in document
order, patch the empty `image` blocks, add captions with the source line → PUT
the body with the shortened slug → pick the strongest chart as `imageUrl` →
screenshot the draft URL to confirm it renders → hand back the link and **ask**.

**"Fix the slug and author on the July recap, then publish it."**
`GET /v1/blog-posts/<slug>` for the ids and current content → resolve the author
via `/v1/users/search` → `PUT /v1/blog-posts/{id}` with `authorId` → PUT the lit
page with the new `path` + existing `content` + `published:false` → show the
summary and **ask** → publish (`published:true` + `publishedAt=now`) → verify the
anonymous `GET` returns 200.

**"What blog drafts are open right now?"**
`GET /v1/blog-posts -X GET -F includeUnpublished=true`, filter
`.litPage.published==false`, print slug + title + author + `createdAt`. Skip the
`<Blog Post Title>` placeholders — those are abandoned drafts from the UI's
create button.

**"Pull that post down, we got a number wrong."**
PUT the lit page with `published:false` (keeping `content` + `path`) — no
confirmation needed to unpublish — then fix the body and re-confirm before
republishing.

## Gotchas

Every item here is a real error hit while testing against production
`lightning.ai`.

- **`-f`/`-F` on a GET endpoint silently makes it a POST.** The CLI defaults to
  POST as soon as any field is present. `lightning api /v1/blog-posts -F
  includeUnpublished=true` tried to *create* a post (it 500'd on the empty
  title, but with a title present it would have created a stray post). **Always
  pass `-X GET` when filtering a list.**
- **`PUT /v1/lit-pages/{id}` is a full replace, not a patch.** Omitted fields
  are reset: leaving out `published` **unpublishes a live post**, and leaving out
  `path` makes the call fail with `500 can't get lit page by path` (nothing is
  saved, so the body edit is lost too). Always send `content` + `path` +
  `published`. Verified: a PUT that dropped an unrelated field came back with it
  emptied.
- **`PUT /v1/blog-posts/{id}` is the opposite** — a partial merge (wrapper
  types), so sending only `description` leaves title/category/image intact. Two
  endpoints, two semantics; don't generalize from one to the other.
- **The lit-page update response nests under `LitPage`, capital L**
  (`.LitPage.path`), unlike every other field in the API. `.litPage` works on
  blog-post responses.
- **A nonexistent slug/id returns `500`, not `404`** (`record not found`
  wrapped as internal). A slug that exists but is an unshared draft returns
  `404` to non-admins. Don't treat 500 as "the API is broken".
- **`limit` and `pageToken` on the list endpoint are ignored** — pagination is a
  TODO server-side, so `GET /v1/blog-posts` returns every post (~170 today).
  Filter client-side with `jq`.
- **`includeUnpublished=true` needs the blog-admin flag**: `401` unauthenticated,
  `403 unauthorized to view unpublished posts` otherwise.
- **Slugs are `[a-z0-9-]` only, and renaming breaks links.** There is no
  redirect from the old path — it starts 500ing immediately. Settle the slug
  before sharing or publishing. A duplicate slug is rejected with
  `409 path already taken`.
- **Create ignores `authorId` and uses the caller.** Set the real author with a
  follow-up `PUT /v1/blog-posts/{id}`. An unknown id is rejected with
  `400 author not found` — the author must be an existing Lightning user, so
  resolve it via `/v1/users/search` (exact username or email).
- **Create does *not* check the blog-admin flag, but every update/publish/delete
  does.** With the wrong account you can create a draft you then cannot edit or
  remove. Check `internalBlogAdmin` before you create anything.
- **`publishedAt` is the date readers see** (it falls back to `createdAt` when
  unset). Set it explicitly when back- or forward-dating; the UI stamps "now" on
  the first publish.
- **Publishing takes effect immediately** — the post shows up in the public
  `/blog` list and is indexable. There is no scheduled publish: a future
  `publishedAt` only changes the displayed date, not visibility.
- **Deleting a post deletes its lit page** (body and slug) but **not** the
  images already uploaded to storage.
- **The blog renders client-side**, so `curl`ing the page URL tells you nothing
  about the content. Check the API response, or open the URL in a browser, to
  verify how a post looks.
- **`PUT /v1/blog-posts/{id}` returns `litPage.path` as `""`** — the response
  builder drops it. The slug is fine; re-`GET` the post if you need to read it
  back. Don't "fix" the empty value by PUTting the lit page with a guess.
- **`currentColor` in an SVG rasterizes to black** — invisible on the dark
  canvas, and equally invisible when the SVG is uploaded as-is and shown through
  `<img>`. Every colour in a chart destined for the blog must be a literal.
- **Rasterizers don't have Inter**, so text renders wider than in the browser and
  edge-anchored labels clip at the `viewBox` border. Pad the viewBox and look at
  the PNG before uploading.
- **Uploads are keyed to the lit page** (`/v1/media/lit_page/{PAGE_ID}/image`),
  not the post id, and the bucket is public — anything uploaded is world-readable
  immediately, before the post is published.
