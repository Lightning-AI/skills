# Lightning AI Agent Skills

Agent skills that teach AI coding agents (Claude Code, Cursor, and any agent that supports the [SKILL.md format](https://code.claude.com/docs/en/skills)) how to use the [Lightning AI](https://lightning.ai) platform: GPU Studios, batch jobs, model deployments, code-execution sandboxes, the LLM gateway, and durable shareable artifact links.

## Skills

| Skill | What it covers |
|---|---|
| [`lightning-studios`](lightning-studios/SKILL.md) | Create, start, stop and manage cloud GPU Studios; switch machines, run commands, transfer files, SSH |
| [`lightning-jobs`](lightning-jobs/SKILL.md) | Launch and monitor batch jobs (single and multi-machine) on CPUs/GPUs, stream logs, SSH into running jobs/MMTs, collect artifacts |
| [`lightning-deployments`](lightning-deployments/SKILL.md) | Deploy containers/APIs with autoscaling, manage releases, endpoints and auth |
| [`lightning-sandboxes`](lightning-sandboxes/SKILL.md) | Fast ephemeral VMs for safe code execution: run commands, background processes, file I/O |
| [`lightning-llm-gateway`](lightning-llm-gateway/SKILL.md) | Call hosted LLMs (OpenAI, Anthropic, open models) through Lightning's models API |
| [`lightning-artifacts`](lightning-artifacts/SKILL.md) | Publish a file and get a durable, public `lightning.ai/artifacts/<id>` link that never expires and renders inline; list, revoke, and delete shares — entirely via the CLI with regular auth |

All skills are built around the [`lightning-sdk`](https://pypi.org/project/lightning-sdk/) Python package and its `lightning` CLI (runnable via `uvx`), plus the raw `lightning api` escape hatch for anything the SDK doesn't wrap.

## Install

The easiest way is the [skills.sh](https://skills.sh) CLI, which installs into Claude Code, Cursor, Codex, and [many other agents](https://skills.sh):

```bash
# interactive: pick skills and target agents
npx skills add Lightning-AI/skills

# install everything without prompts
npx skills add Lightning-AI/skills --all -y

# install a specific skill, e.g. just sandboxes
npx skills add Lightning-AI/skills -s lightning-sandboxes

# user-level (global) instead of the current project
npx skills add Lightning-AI/skills -g
```

Or copy the skill folders manually into your agent's skills directory:

```bash
# Claude Code (project-level)
mkdir -p .claude/skills
cp -r lightning-studios lightning-jobs lightning-deployments lightning-sandboxes lightning-llm-gateway .claude/skills/

# Claude Code (user-level)
cp -r lightning-* ~/.claude/skills/
```

## Prerequisites

- `uv` installed (skills invoke the CLI via `uvx lightning-sdk`)
- A Lightning AI account: authenticate with `lightning login` or set `LIGHTNING_API_KEY` + `LIGHTNING_USER_ID`

## Conventions

- Skills never guess the organization or teamspace: when more than one is available and none is configured, they ask the user which one to use.
- Skills prefer the documented SDK/CLI surface and fall back to `lightning api <endpoint>` for raw REST calls.

## Contributing

Adding or editing a skill? See **[AUTHORING.md](AUTHORING.md)** for the `SKILL.md`
format, the house-style section layout, the conventions above, and how to
live-test every command against a control plane before opening a PR.
