# Lightning AI Agent Skills

Agent skills that teach AI coding agents (Claude Code, Cursor, and any agent that supports the [SKILL.md format](https://code.claude.com/docs/en/skills)) how to use the [Lightning AI](https://lightning.ai) platform: GPU Studios, batch jobs, model deployments, code-execution sandboxes, and the LLM gateway.

## Skills

| Skill | What it covers |
|---|---|
| [`lightning-studios`](lightning-studios/SKILL.md) | Create, start, stop and manage cloud GPU Studios; switch machines, run commands, transfer files, SSH |
| [`lightning-jobs`](lightning-jobs/SKILL.md) | Launch and monitor batch jobs (single and multi-machine) on CPUs/GPUs, stream logs, collect artifacts |
| [`lightning-deployments`](lightning-deployments/SKILL.md) | Deploy containers/APIs with autoscaling, manage releases, endpoints and auth |
| [`lightning-sandboxes`](lightning-sandboxes/SKILL.md) | Fast ephemeral VMs for safe code execution: run commands, background processes, file I/O |
| [`lightning-llm-gateway`](lightning-llm-gateway/SKILL.md) | Call hosted LLMs (OpenAI, Anthropic, open models) through Lightning's models API |

All skills are built around the [`lightning-sdk`](https://pypi.org/project/lightning-sdk/) Python package and its `lightning` CLI (runnable via `uvx`), plus the raw `lightning api` escape hatch for anything the SDK doesn't wrap.

## Install

Copy the skill folders you want into your agent's skills directory:

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
