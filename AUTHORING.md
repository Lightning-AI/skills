# Authoring & managing Lightning AI skills

How to add, update, and maintain the skills in this repo. Each skill teaches an
AI coding agent how to drive one part of the Lightning AI platform, so the bar is:
**every command in a skill must actually run** against a real control plane, and
the agent must never have to guess.

## What a skill is

One directory per skill, containing a single `SKILL.md`:

```
lightning-<area>/
└── SKILL.md
```

`SKILL.md` is Markdown with a YAML frontmatter header (the
[SKILL.md format](https://code.claude.com/docs/en/skills)):

```markdown
---
name: lightning-artifacts
description: <one paragraph — WHAT it does + "Use when the user ..." triggers>
---

# Human-readable title

Body: how to actually do the thing, in copy-pasteable commands.
```

Only `name` and `description` are required. `name` must match the directory name.
Keep a skill self-contained in its `SKILL.md` — no extra files unless a skill
genuinely needs a script or template asset.

### Writing the `description`

The description is the **only thing an agent sees when deciding whether to load
the skill**, so it does double duty:

1. **What** the skill does, concretely (name the nouns: "durable public
   `lightning.ai/artifacts/<id>` link", "autoscaled HTTPS endpoint").
2. **When** to use it — trigger phrases in the user's words: *"Use when the user
   wants to share a file … or asks to 'get a public link for this file'."*

Look at the existing skills for the register and length. One dense paragraph.

## House style (keep skills consistent)

The platform skills follow a standard section order. Reuse it unless a skill
has a reason not to:

| Section | Purpose |
|---|---|
| `## Setup & auth` | `uvx lightning-sdk`, required env vars / `lightning login`, how to get a key |
| `## Resolving org and teamspace (do this first)` | never guess — resolve via `lightning api /v1/memberships`, ask the user if ambiguous |
| `## CLI reference` | the `lightning <group> <cmd>` surface the skill uses |
| `## Python SDK` | the equivalent `lightning_sdk` calls (**omit if the skill is CLI-only** — e.g. `lightning-artifacts`) |
| `## Example workflows` | 2–4 real prompts this skill handles, each with the commands |
| `## Raw API fallback` | `lightning api <endpoint>` for anything the CLI/SDK doesn't wrap |
| `## Gotchas` | the sharp edges you hit while testing — this is the highest-value section |

### Conventions every skill follows

- **CLI-first.** Prefer `uvx lightning-sdk` and the `lightning` CLI. `lightning
  api /path …` (a `gh api`-style raw client: `-X`, `-f`/`-F`, `-H`, `--input`,
  `-q`) is the escape hatch for endpoints the CLI doesn't wrap — reach for it
  before dropping to Python. Only add a Python section when it adds something the
  CLI can't do.
- **Never guess org / teamspace / cluster.** Resolve from `/v1/memberships` (and
  `/v1/projects/{id}/clusters` for the cloud account). If more than one fits and
  none is configured, the skill instructs the agent to **ask the user**.
- **Never hardcode secrets.** Read `LIGHTNING_API_KEY` from the environment; show
  `export LIGHTNING_API_KEY=...`, not a literal.
- **Document what you verified, including failure modes.** A gotcha like "this
  endpoint 401s on basic auth — mint a token first" is worth more than three
  paragraphs of prose. Every gotcha in these skills came from a real error.

## Test a skill before committing it (required)

Skills are only useful if their commands run. Verify against a control plane —
production `lightning.ai` or a local/dev one — before opening a PR.

```bash
# point the CLI at the control plane (omit LIGHTNING_CLOUD_URL for prod lightning.ai)
export LIGHTNING_CLOUD_URL=http://localhost:9800     # dev control plane, if testing locally
export LIGHTNING_API_KEY=<key>
export LIGHTNING_USER_ID=<user-id>                   # optional

uvx lightning-sdk --version                          # CLI is reachable
uvx lightning-sdk api /v1/memberships -q '.memberships[] | [.name, .projectId] | @tsv'
```

Then **run every command in the skill top to bottom** with real values and
confirm the result — not just that it exits 0, but that it does the thing (fetch
the URL you minted, list the resource you created, etc.). When you hit an error,
fix the command and record the cause as a gotcha. The commit history here is full
of `… (live-verified)` / `Fix skills from live testing …` for exactly this
reason — treat live testing as part of authoring, not an afterthought.

Tips:
- `LIGHTNING_CLOUD_URL` retargets the whole CLI, so the same commands test a
  local control plane and prod.
- Prefer resolving ids at runtime (`PID=$(lightning api … -q .…)`) in examples so
  they're copy-paste-safe rather than pinned to one account.
- `-q` needs the `jq` binary; note that where you rely on it.

## Add, update, or remove a skill

**Add:** create `lightning-<area>/SKILL.md`, write and test it, then add a row to
the table in `README.md`. If it introduces a new capability worth calling out,
mention it in the README intro too.

**Update:** edit the `SKILL.md`. Re-run the affected commands against a control
plane before committing — the API may have changed under you. Keep the README row
in sync if the scope changed.

**Remove:** delete the directory and its `README.md` row.

Keep changes scoped to one skill per PR where you can; it makes review and
`npx skills add` selection cleaner.

## Open a PR

```bash
git checkout -b <area>-skill
git add lightning-<area>/SKILL.md README.md
git commit -m "Add lightning-<area> skill (live-verified against <control plane>)"
git push -u origin HEAD
gh pr create --fill
```

In the PR description, say **what control plane you tested against** and paste a
line or two of evidence (the URL you minted, the resource you listed). That's what
tells a reviewer the skill actually works.

## Install / try a skill locally

```bash
npx skills add Lightning-AI/skills -s lightning-<area>   # via skills.sh
# or copy the folder into your agent's skills dir:
cp -r lightning-<area> ~/.claude/skills/
```
