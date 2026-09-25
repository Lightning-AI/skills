# AGENTS.md

Agent skills for Lightning AI: each `lightning-<area>/SKILL.md` teaches an agent one part of the
platform, and a few skills ship helper scripts next to their `SKILL.md`. This file covers the dev
environment: setting it up, the checks every change must pass, and the rules for scripts. How to
write and test a skill itself is in [AUTHORING.md](AUTHORING.md).

## Layout

| Path | What it is |
|---|---|
| `lightning-*/SKILL.md` | the skills |
| `lightning-jobs/progress.py`, `lightning-jobs/job_progress/` | live job progress: poller, Monitor feed, status line |
| `lightning-blog/md2blocks.py` | Markdown to blog blocks |
| `examples/` | end-to-end tasks for trying the skills; each declares its own dependencies |
| `tests/` | unit tests for the scripts |
| `pyproject.toml` | settings for ruff, pyright, pydoclint and Import Linter (nothing to install) |
| `.pre-commit-config.yaml` | the checks and their pinned versions |
| `.github/workflows/checks.yml` | CI: the same checks, plus the tests on Python 3.9 and 3.13 |

## Set up

You need `git`, [uv](https://docs.astral.sh/uv/) and Python 3.9 or newer.

```bash
uv tool install pre-commit --with pre-commit-uv   # pre-commit, building hook envs with uv
pre-commit install                                # run the checks on every commit
pre-commit run --all-files                        # first run downloads the tools
python3 -m unittest discover -s tests
```

To skip the install, prefix pre-commit commands with `uvx --with pre-commit-uv`, e.g.
`uvx --with pre-commit-uv pre-commit run --all-files`.

## Checks

Each hook is also a separate CI check. Run one with `pre-commit run <hook> --all-files`.

| Hook | What it checks |
|---|---|
| `ruff-check` | lint errors and import order; fixes what it can |
| `ruff-format` | formatting (Python files only: the skill docs' examples are aligned by hand) |
| `pyright` | types, with `lightning-sdk` installed so the poller's SDK calls are checked too |
| `pydoclint` | docstrings whose Args/Returns sections disagree with the signature |
| `import-linter` | the `job_progress` layers, and that only the poller imports `lightning_sdk` |

The tests need no dependencies. To run them on the oldest supported Python, as CI does:

```bash
uv run --no-project --python 3.9 python -m unittest discover -s tests
```

**From a sandboxed agent:** the first `pre-commit run` needs network access to `github.com`
(hook repos), `pypi.org` and `files.pythonhosted.org` (tools), and `registry.npmjs.org`
(pyright), and it writes to `~/.cache/pre-commit` and `~/.cache/uv`. Claude Code's sandbox has
been seen to abort pre-commit's `git fetch` even with `github.com` allowed, so run that first
install outside the sandbox. Later runs reuse the cached environments.

## Rules for scripts

- **They run as plain files.** Users run them with whatever Python they have, and nothing is
  installed. Use the standard library only. The exception is `lightning_sdk`, which only the
  poller (`job_progress/watch.py`) imports, and only inside functions; Import Linter enforces
  this.
- **Python 3.9 is the floor.** It is macOS's system `python3`. Ruff targets 3.9, so newer
  syntax fails the lint, and CI runs the tests on 3.9.
- **They must work on every platform Claude Code runs on**: macOS, Linux and Windows. Avoid a
  hard-coded `python3` command, `sh -c`, `os.kill(pid, 0)` (on Windows, signal 0 is Ctrl+C),
  `os.execv`, and printing non-ASCII text without UTF-8 output. `progress.py` still has some of
  these; don't add more.
- **Keep the `job_progress` layers.** From the top down:
  - `watch`, `events` and `statusline` are the commands, and never import each other;
  - `tracker` and `settings` sit below them;
  - `store`, then `core`, at the bottom.

  `tracker` is pure logic, with no files and no network. `progress.py` imports a command's module
  only when that command runs.
- **Tests import the package from its folder.** `tests/test_progress.py` puts `lightning-jobs/`
  on `sys.path`, and so does Pyright's config.

## Commits and PRs

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/), e.g.
`fix(jobs): …` or `ci: …`. Open PRs as [AUTHORING.md](AUTHORING.md#open-a-pr) describes. The
Checks workflow runs on every PR; keep it green.
