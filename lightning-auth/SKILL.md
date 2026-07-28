---
name: lightning-auth
description: Check who you are and what you're allowed to do on Lightning AI - `lightning auth whoami` reports your identity (and, for a scoped API key, the org / teamspace / role it is bound to), while `lightning auth roles` / `lightning auth role` list a teamspace's roles and describe exactly which actions each one permits. Use when the user asks "who am I", "what org/teamspace/role is this API key", "what am I allowed to do here", or is debugging a permission / access-denied error.
---

# Lightning AI Identity & Access

`lightning auth` answers two questions: **who am I** (`whoami`) and **what am I allowed to do** (`roles`, `role`). It works for both a personal login and a scoped API key; a scoped key additionally reports the org, teamspace, and role it is bound to.

## Setup & auth

```bash
uvx lightning-sdk --version         # CLI without installing; `lightning` == `lightning-sdk`
lightning login                     # browser flow; or headless:
export LIGHTNING_USER_ID=... LIGHTNING_API_KEY=...   # both required (Basic auth)
```

## whoami — who am I

```bash
lightning auth whoami            # human-readable table
lightning auth whoami --json     # machine-readable (same fields)
```

Reports `auth_type` (`user` or `scoped-api-key`), `user_id`, `username`, `email`. For a **scoped API key** it also reports the binding: `org_id`, `project_id` (the teamspace), `role_id`, and `api_key_id`. It's the quickest way to confirm which org/teamspace an agent is operating in, and the `role_id` feeds straight into `lightning auth role` below.

For a personal login the org/teamspace/role fields are empty — a user isn't bound to a single one; resolve the teamspace with `lightning config get teamspace` or by listing `lightning auth roles --teamspace ...`.

## roles — what roles exist, and which are mine

```bash
lightning auth roles --teamspace owner/teamspace          # table; marks the roles you hold
lightning auth roles --teamspace owner/teamspace --json
```

Lists every role defined in the teamspace with a permission count, and marks (`✓` / `"yours": true`) the ones assigned to you. Roles are **per teamspace** — pass `--teamspace owner/teamspace` or rely on the configured default (`lightning config get teamspace`).

## role — what a role allows

```bash
lightning auth role <ROLE_ID> --teamspace owner/teamspace
lightning auth role <ROLE_ID> --teamspace owner/teamspace --json
```

Describes a role's permissions as rows of **resource × actions × effect × condition**, using user-facing names (`Studios`, `Teamspaces`, `Multi-machine training (MMT)`, …). `effect` is `Allow` or `Deny`; `condition` narrows a rule (e.g. `own resources only`). Get the `ROLE_ID` from `lightning auth roles`, or — for a scoped key — from `lightning auth whoami`'s `role_id`.

## Example workflows

Prompts this skill handles: *"who am I / what account is this key"*, *"what teamspace and role does this API key have"*, *"what am I allowed to do here"*, *"why am I getting a permission error"*.

**Confirm a scoped key's scope, then see what it can do:**

```bash
lightning auth whoami --json                                  # -> org_id, project_id, role_id
lightning auth role <role_id> --teamspace owner/teamspace     # what that role permits
```

**Debug an access-denied error — check whether your role grants the action:**

```bash
lightning auth roles --teamspace owner/teamspace                       # find your role (marked "yours")
lightning auth role <your-role-id> --teamspace owner/teamspace --json  # inspect its rules
```

## Gotchas

- `whoami` only fills org/teamspace/role for a **scoped API key**; a personal login leaves them empty (a user isn't bound to one org).
- `roles` and `role` are **teamspace-scoped** — always tied to an `owner/teamspace`, not org-wide. `--teamspace` takes the `owner/teamspace` form and falls back to the configured default.
- Permissions render with **user-facing resource names** (`Studios` = cloudspaces, `Teamspaces` = projects, `MMT` = multi-machine jobs); the raw internal enum names aren't shown, and unspecified/sentinel entries are filtered out.
- A role can carry `Deny` rules and conditional rules (`own resources only`) — read the `Effect` and `Condition` columns, not just `Actions`.
