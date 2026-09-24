# Lightning AI Agent Skills

Agent skills that teach AI coding agents (Claude Code, Cursor, and any agent that supports the [SKILL.md format](https://code.claude.com/docs/en/skills)) how to use the [Lightning AI](https://lightning.ai) platform: GPU Studios, batch jobs, model deployments, code-execution sandboxes, the LLM gateway, durable shareable artifact links, and up-front cost estimates for any of it.

## Skills

| Skill | What it covers |
|---|---|
| [`lightning-studios`](lightning-studios/SKILL.md) | Create, start, stop and manage cloud GPU Studios; switch machines, run commands, transfer files, SSH |
| [`lightning-jobs`](lightning-jobs/SKILL.md) | Launch and monitor batch jobs (single and multi-machine) on CPUs/GPUs, stream logs, SSH into running jobs or multi-machine workers, collect artifacts |
| [`lightning-deployments`](lightning-deployments/SKILL.md) | Deploy containers/APIs with autoscaling, manage releases, endpoints and auth |
| [`lightning-sandboxes`](lightning-sandboxes/SKILL.md) | Fast ephemeral VMs for safe code execution: run commands, background processes, file I/O, Docker (`docker` / `docker compose`) and public port URLs |
| [`lightning-llm-gateway`](lightning-llm-gateway/SKILL.md) | Call hosted LLMs (OpenAI, Anthropic, open models) through Lightning's models API |
| [`lightning-artifacts`](lightning-artifacts/SKILL.md) | Publish a file and get a durable, public `lightning.ai/artifacts/<id>` link that never expires and renders inline; list, revoke, and delete shares — entirely via the CLI with regular auth |
| [`lightning-cost-estimation`](lightning-cost-estimation/SKILL.md) | Quote what a training run, fine-tune or deployment costs: live per-hour GPU/CPU prices for every cloud, spot rates, multi-node fan-out, and Drive storage |

**Tutorial:** [teach a 4B model to call tools on a cloud GPU in under 10 minutes](examples/finetune-eval/README.md). It starts from a Mac or a Claude cloud session and one chat message.

All skills are built around the [`lightning-sdk`](https://pypi.org/project/lightning-sdk/) Python package and its `lightning` CLI, plus the raw `lightning api` escape hatch for anything the SDK doesn't wrap.

## Install

### Claude Code plugin

This repo is also a [plugin marketplace](https://code.claude.com/docs/en/plugin-marketplaces), so Claude Code can install all seven skills as one plugin and keep them updated:

```bash
/plugin marketplace add Lightning-AI/skills
/plugin install lightning@lightning-ai
```

Plugin skills are namespaced, so they appear as `/lightning:lightning-jobs`, `/lightning:lightning-studios`, and so on. They still fire on their own when a task calls for them.

### skills.sh (Claude Code, Cursor, Codex and others)

The [skills.sh](https://skills.sh) CLI installs into Claude Code, Cursor, Codex, and [many other agents](https://skills.sh):

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

### Manual copy

Or copy the skill folders straight into your agent's skills directory:

```bash
SKILLS="lightning-studios lightning-jobs lightning-deployments lightning-sandboxes lightning-llm-gateway lightning-artifacts lightning-cost-estimation"

# Claude Code (project-level)
mkdir -p .claude/skills && cp -r $SKILLS .claude/skills/

# Claude Code (user-level)
cp -r $SKILLS ~/.claude/skills/
```

## Prerequisites

- Python with `uv` or `pip`. Skills use the `lightning` CLI in your current environment, installing or upgrading `lightning-sdk` there if it's missing or too old (`uv tool install lightning-sdk` or `uvx` are fallbacks)
- A Lightning AI account: authenticate with `lightning login` or set `LIGHTNING_API_KEY` (plus `LIGHTNING_USER_ID`, optional)

## Conventions

- Skills never guess the organization or teamspace: when more than one is available and none is configured, they ask the user which one to use.
- Skills prefer the documented SDK/CLI surface and fall back to `lightning api <endpoint>` for raw REST calls.

## Contributing

Adding or editing a skill? See **[AUTHORING.md](AUTHORING.md)** for the `SKILL.md`
format, the house-style section layout, the conventions above, and how to
live-test every command against a control plane before opening a PR.
