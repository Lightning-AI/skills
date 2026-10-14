---
name: lightning-billing
description: Read and manage Lightning AI billing - credit balances for a user, org or teamspace (with transaction history), spend reports broken down by resource type, user, teamspace, day or single resource (jobs, Studios, deployments, sandboxes, storage, LLM/assistant tokens), monthly credits purchased vs remaining, the org's subscription plan with feature limits, plan price quotes and coding plan allowances, plus credit transfers between org and teamspace wallets and Stripe checkout or billing-portal links, all through `lightning api` on the billing REST API. Use when the user asks "how many credits do I have left", "what did we spend last month", "who or what is burning our credits", "break down costs by teamspace / user / resource type", "how much did this job cost", "what plan are we on", "how much is Pro", "move credits into this teamspace", or wants a link to top up or manage the card.
license: Apache-2.0
compatibility: Requires Python with uv or pip, the lightning CLI from the lightning-sdk package (installed on demand), jq, network access to lightning.ai, and a Lightning AI account. Org-wide reports need org billing read rights.
---

# Lightning AI billing

Balances, spend and plans for a Lightning AI user, org or teamspace. **This skill reports what was
spent and what's left**; to quote what a workload *would* cost, use `lightning-cost-estimation`.

**1 Lightning credit = $1 USD.** Machines bill per second while allocated, storage daily. Every
cost and balance field below is in credits, except the subscription price quotes, which are in
**USD cents** (`5000` = $50). Docs: [billing FAQ](https://lightning.ai/docs/platform/overview/faq/billing.md) ·
[manage costs](https://lightning.ai/docs/platform/team-management/organizations/manage-costs.md).

There is no `lightning billing` command: everything goes through `lightning api` against the
`BillingService` endpoints. [references/endpoints.md](references/endpoints.md) lists all 30 of
them with every parameter and response field; this file covers the ones you'll use.

## Setup & auth

```bash
export DEBUG=0 LIGHTNING_DEBUG=0   # an inherited DEBUG=1 logs the Authorization header; set this in every new shell
# Use the Lightning AI CLI from the current env; install or upgrade it there if it's missing or older than 2026.9.18
v=$(lightning --version 2>/dev/null | sed -n 's/^Lightning CLI version //p')
[ -n "$v" ] && [ "$(printf '%s\n' 2026.9.18 "$v" | sort -V | head -1)" = 2026.9.18 ] \
  || uv pip install -U lightning-sdk || python3 -m pip install -U lightning-sdk
lightning --version   # must print "Lightning CLI version …"; if not, see the table below
lightning login                       # interactive browser sign-in
# or: export LIGHTNING_API_KEY=...    # non-interactive alternative (CI, agents)
```

Either credential works for every call in this skill. **Never hardcode the key**; read it from the
environment. To target a non-prod control plane, set `LIGHTNING_CLOUD_URL`. The recipes need `jq`.

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

**Every billing read is a GET, so pass `-X GET` whenever you use `-f`.** Without it, `-f` turns the
request into a POST and the server answers `HTTP 501`. With `-X GET`, each `-f key=value` becomes a
query parameter, a repeated key becomes a list (`-f resourceTypes=job -f resourceTypes=sandbox`),
and dotted keys reach nested fields (`-f subfilters.job.tagIds=<tag-id>`). **Add `-i` when a call
fails**: without it you see only `Error: HTTP 400`, with it you also get the server's message.

## Resolving org and teamspace (do this first)

Wallets and reports hang off an **org id** or a **teamspace (project) id**. **Never guess either.**

```bash
lightning api /v1/memberships \
  | jq -r '.memberships[] | [.name, .projectId, .ownerType, .ownerId, .balance] | @tsv' | sort -u
```

`ownerType=organization` rows give the org id in `ownerId`; name it with
`lightning api "/v1/orgs/$ORG" | jq -r .name`. `ownerType=user` rows are personal teamspaces. If the
user belongs to several orgs and didn't say which, **ask**. `/v1/billing/coding-usage` (below) is a
quick way to list every org the user can bill against, by name.

```bash
ORG=<org-id>       # org wallet, subscription, org-wide reports
PID=<project-id>   # one teamspace
```

**Pass `orgId` on every report call, including one scoped to a teamspace with `projectIds`.**
Without it the usage reports return `403 unauthorized`, which looks like a permissions problem but
isn't. Reading reports needs billing read rights, not admin: the default org Member role has them
for org-wide reports, and any member of a teamspace can read that teamspace's reports.

## Balances

```bash
lightning api /v1/users/billing/balance                    # your personal wallet; .totalSpent = lifetime spend
lightning api "/v1/orgs/$ORG/billing/balance"              # org wallet; .balanceLimit may be negative (credit line)
lightning api "/v1/projects/$PID/billing/balance"          # one teamspace's wallet
```

Add `-X GET -f includeTransactions=true` for the ledger (top-ups, transfers, refunds) newest first:

```bash
lightning api "/v1/orgs/$ORG/billing/balance" -X GET -f includeTransactions=true \
  | jq -r '.transactions[:20][] | [.createdAt[:10], .amount, .type, .projectName] | @tsv'
```

Transactions are money **moving between wallets**, not per-workload spend: for "what used the
credits", use the usage reports below. Workloads in a teamspace draw from its wallet, and the
org's `auto-transfer` rows refill it.

## Spend reports

All time ranges are **full RFC 3339 timestamps** (`2026-10-01T00:00:00Z`); a bare date is a 400.
Rollups bucket by whole UTC days.

```bash
FROM=2026-09-01T00:00:00Z TO=2026-10-01T00:00:00Z
```

### Totals by resource type, user or teamspace

`group-totals` is the fastest way to answer "where did the money go". `groupBy` is required:
`USAGE_GROUP_BY_RESOURCE_TYPE`, `USAGE_GROUP_BY_USER` or `USAGE_GROUP_BY_TEAMSPACE`.

```bash
lightning api /v1/billing/usage-report-rollup/group-totals -X GET \
  -f orgId=$ORG -f from=$FROM -f to=$TO -f groupBy=USAGE_GROUP_BY_RESOURCE_TYPE \
  | jq -r '"total\t\(.totalCost)", (.groups[] | [.groupValue, .cost, .savedCost] | @tsv)'
```

Groups come back most expensive first. `savedCost` is what spot/discounts saved. Group by
`USAGE_GROUP_BY_TEAMSPACE` and you get project ids; by `USAGE_GROUP_BY_USER`, user ids (unassigned
usage shows as an empty teamspace id or the nil UUID).

**Per day** (for a chart or "when did it spike"), same filters, newest day first:

```bash
lightning api /v1/billing/usage-report-rollup/group-daily-totals -X GET \
  -f orgId=$ORG -f from=$FROM -f to=$TO -f groupBy=USAGE_GROUP_BY_TEAMSPACE \
  | jq -r '.dailyTotals[] | [.day[:10], .groupValue, .cost] | @tsv'
```

**Filters** (all repeatable, all optional, on these two endpoints and `usage-report-rollup`):
`projectIds`, `userIds`, `resourceTypes`, `resourceIds`, `clusterIds`, plus tag/API-key subfilters.
Get the valid values first:

```bash
lightning api /v1/billing/usage-report/filter-values -X GET -f orgId=$ORG \
  | jq '{resourceTypes, clusterIds, users: (.userIds|length), teamspaces: (.projectIds|length),
         jobTags: [.subfilters.job.tags[]?.name], apiKeys: [.subfilters.assistantMessage.apiKeyIds[]?.name]}'
```

Resource types seen live: `job`, `multi_machine_job`, `deployment`, `lightning_code` (Studios),
`lightning_app`, `sandbox`, `instance` (VMs), `capacity_block`, `assistant_message` (LLM/model API
tokens), `project_storage`, `container_storage`, `efs_file_system`, `filestore_file_system`,
`vast_file_system`.

Spend on jobs tagged `data`, per resource type:

```bash
TAG=$(lightning api /v1/billing/usage-report/filter-values -X GET -f orgId=$ORG \
  | jq -r '.subfilters.job.tags[] | select(.name=="data") | .id')
lightning api /v1/billing/usage-report-rollup/group-totals -X GET \
  -f orgId=$ORG -f from=$FROM -f to=$TO -f groupBy=USAGE_GROUP_BY_RESOURCE_TYPE \
  -f subfilters.job.tagIds=$TAG -q .groups
```

Once any subfilter is set, rows of resource types **without** a subfilter are dropped. Subfilter
keys: `subfilters.job.tagIds`, `subfilters.multiMachineJob.tagIds`, `subfilters.deployment.tagIds`
(add `….matchAllTags=true` to require every tag), `subfilters.assistantMessage.apiKeyIds`.

### Per-resource list (top spenders, one row per resource)

```bash
lightning api /v1/billing/usage-report-rollup -X GET \
  -f orgId=$ORG -f from=$FROM -f to=$TO -f resourceTypes=job -f limit=200 \
  | jq -r '.usage | sort_by(-.cost)[:20][] | [.resourceName, .resourceType, .projectId, .cost, (.billedTimeSeconds|tonumber/3600)] | @tsv'
```

The response also carries `totalCost`, `totalSavedCost` and `dailyUsage` (one entry per day).
**Always pass `limit`.** Rows are not sorted by cost, so the top 20 of one page is only the true
top 20 when `hasMore` is false. Otherwise page with the cursor it returns, then sort:

```bash
R=$(lightning api /v1/billing/usage-report-rollup -X GET -f orgId=$ORG -f from=$FROM -f to=$TO -f limit=500)
while :; do
  echo "$R" | jq -c '.usage[]'
  [ "$(echo "$R" | jq -r .hasMore)" = true ] || break
  R=$(lightning api /v1/billing/usage-report-rollup -X GET -f orgId=$ORG -f from=$FROM -f to=$TO -f limit=500 \
    -f searchAfter="$(echo "$R" | jq -r .searchAfter)" \
    -f searchAfterResourceId="$(echo "$R" | jq -r .searchAfterResourceId)" \
    -f searchAfterResourceType="$(echo "$R" | jq -r .searchAfterResourceType)")
done | jq -s 'sort_by(-.cost)[:20]'
```

To find a resource id by name (Studios, jobs, deployments, multi-machine jobs), search the
paginated name list:

```bash
lightning api /v1/billing/usage-report/filter-values/resource-names -X GET \
  -f orgId=$ORG -f searchQuery=devbox -f pageSize=50 -q .resourceIds   # page with -f pageToken=<nextPageToken>
```

### One resource's cost breakdown

"How much did this job cost?" Use the rollup row's `id` and `projectId`:

```bash
lightning api /v1/billing/usage-details -X GET \
  -f projectId=$PID -f resourceId=job_01m4r1e23np9nftk2pmfs37hdh -f resourceType=job \
  | jq -r '.details[] | [.instanceType, .instanceCount, .startedAt, .endedAt, (.billedTimeSeconds|tonumber), .cost, .spot] | @tsv'
```

The `resourceId` carries its type prefix (`job_…`) exactly as the rollup returns it; the bare
job id returns `{"details": []}`, not an error.

### LLM / model API spend, sandboxes, monthly credits

```bash
# LLM tokens and cost per day (assistant_message spend); details=true splits by user and model
lightning api /v1/billing/assistant-session -X GET -f orgId=$ORG -f from=$FROM -f to=$TO \
  | jq -r '"total\t\(.totalCost)", (.dailyUsage[] | [.day[:10], .totalCost, .numMessages, .totalPromptTokens, .totalCompletionTokens] | @tsv)'

# sandboxes: active count and cost per 15-minute bucket (keep the range short)
lightning api /v1/billing/sandbox-usage/activity -X GET -f orgId=$ORG -f from=$FROM -f to=$TO -q .buckets

# credits purchased and remaining per calendar month; a time filter is required
lightning api /v1/billing/monthly-summary -X GET -f orgId=$ORG \
  -f timeFilter.rangeFilter.rangeStart=2026-01-01T00:00:00Z -f timeFilter.rangeFilter.rangeEnd=$TO \
  | jq -r '.monthlySummaries[] | [.periodStart[:7], .totalCreditsPurchased, .totalCreditsRemaining] | @tsv'
# or everything before a date: -f timeFilter.pivotFilter.pivotDirection=BEFORE -f timeFilter.pivotFilter.pivot=$TO
```

`monthly-summary` has no "consumed" field. Spend for a month is the `group-totals` total for it.

## Plan and limits

```bash
# current plan, seats, renewal, card on file, and every feature limit with current usage
lightning api /v1/billing/subscription -X GET -f orgId=$ORG \
  | jq '{name, status, period, seats, periodEnd, card: "\(.cardType) \(.cardLast4)",
         atLimit: [.features[] | select(.hitLimit) | .key]}'

# list price of a tier (USD cents) and what it includes: tier = Free | Pro | Teams | Enterprise | Academic
lightning api /v1/billing/subscription/quote -X GET -f tier=Pro \
  | jq '{monthly: (.monthlyAmount/100), annual: (.annualAmount/100), trialDays,
         limits: [.features[] | {key, limit}]}'

# coding plan (code.lightning.ai) allowance and window usage, for every org the user is in
lightning api /v1/billing/coding-usage \
  | jq -r '.plans[] | [.orgName, .orgId, .planName, .monthlyAllowanceCredits, (.windows|map("\(.kind|sub("CODING_USAGE_WINDOW_KIND_";"")):\(.usedCredits)/\(.limitCredits)")|join(" "))] | @tsv'
```

The personal org's subscription is the user's own plan (`Free`, `Pro`, …). A feature with no
`limit` is unlimited when `hitLimit` is false, and not included in the plan when it's true. `subscription` needs `orgId`; `projectId` alone is a 400.

## Actions that move money (confirm first)

**Every call in this section spends, moves or commits real money. Only run one when the user asked
for that exact action in this request, and repeat the amount and the source and destination
wallets back before you do.** None are reversible by the agent.

```bash
# move credits from the org wallet into a teamspace wallet (omit amount to move all)
lightning api "/v1/orgs/$ORG/billing/transfer" -X POST -f projectDstId=$PID -F amount=500

# teamspace → teamspace, or back to the org (set exactly one of projectDstId / orgDstId)
lightning api "/v1/projects/$PID/billing/transfer" -X POST -f projectDstId=$OTHER_PID -F amount=100
lightning api "/v1/projects/$PID/billing/transfer" -X POST -f orgDstId=$ORG -F amount=100
```

These give the user a **Stripe link to finish in the browser** (nothing is charged until they
complete it there). Show the returned `.url` on its own line:

```bash
# buy credits for a teamspace (walletType Project, default) or the org (walletType Org)
lightning api /v1/billing/checkout -X POST -F amount=100 -f walletType=Org -f orgId=$ORG -q .url
# subscribe / change plan
lightning api /v1/billing/subscription/checkout -X POST -f orgId=$ORG -f tier=Pro -f billingPeriod=Year -F seats=1 -q .url
# Stripe customer portal: card, invoices, cancel (note the capitalised field names)
lightning api /v1/billing/portal-session -X POST -f orgID=$ORG -f returnURL=https://lightning.ai -q .url
```

Changing seats, billing period or cancelling (`PUT /v1/billing/subscription`), buying the annual
upsell (`POST /v1/billing/annual-upsell`) and turning on pay-as-you-go for coding usage
(`PUT /v1/billing/coding-usage/settings`) charge the card on file **directly, with no browser
step**. Prefer the portal link; if the user insists, see
[references/endpoints.md](references/endpoints.md) for their fields.

## Example workflows

Prompts this skill handles: *"how many credits does the research teamspace have?"*, *"what did
our org spend in September, by team?"*, *"which jobs cost the most this week?"*, *"how much have we
spent on LLM tokens?"*, *"what plan are we on and what limits are we hitting?"*, *"move 500
credits to the ml-infra teamspace"*.

**Last month's spend by teamspace, with names:**

```bash
FROM=2026-09-01T00:00:00Z TO=2026-10-01T00:00:00Z
NAMES=$(lightning api /v1/memberships | jq '[.memberships[] | {(.projectId): .name}] | add')
lightning api /v1/billing/usage-report-rollup/group-totals -X GET \
  -f orgId=$ORG -f from=$FROM -f to=$TO -f groupBy=USAGE_GROUP_BY_TEAMSPACE \
  | jq -r --argjson n "$NAMES" '.groups[] | [(if .groupValue == "" then "(unassigned)" else ($n[.groupValue] // .groupValue) end), (.cost*100|round/100)] | @tsv'
```

Teamspaces the user isn't a member of keep their id; say so rather than guessing a name.

**Is a teamspace about to run dry?** Compare its wallet with the last 7 days' burn:

```bash
BAL=$(lightning api "/v1/projects/$PID/billing/balance" -q .balance)
WEEK=$(lightning api /v1/billing/usage-report-rollup/group-totals -X GET -f orgId=$ORG -f projectIds=$PID \
  -f from=$(date -u -d '7 days ago' +%Y-%m-%dT00:00:00Z) -f to=$(date -u +%Y-%m-%dT00:00:00Z) \
  -f groupBy=USAGE_GROUP_BY_TEAMSPACE -q .totalCost)
echo "balance $BAL, last 7 days $WEEK, ~$(jq -n "$BAL / ($WEEK / 7) | floor") days left"
```

(`date -d` is GNU; on macOS use `date -u -v-7d +%Y-%m-%dT00:00:00Z`.) The org's auto-transfer may
refill the wallet, so check the org balance too before warning.

## Gotchas

- **`-f` without `-X GET` turns a read into a POST, which fails with `HTTP 501`.** Put `-X GET` on
  every billing read that uses `-f`, or write the query string into the path yourself.
- **Errors print only the status unless you pass `-i`.** `Error: HTTP 400 Bad Request` hides
  messages like `orgId is required for billing operations`, `group_by is required` or
  `time_filter is a required argument`. Rerun with `-i` to read them.
- **A missing `orgId` returns `403`, not `400`, on the usage reports.** `usage-report-rollup` and
  its `group-*` endpoints with only `projectIds` return `403 unauthorized`; `filter-values` without
  `orgId` returns `400 org_id is required`. Always pass `orgId`, adding `projectIds` to narrow it.
  Check `orgId` before concluding the user lacks access. The same `403` also comes back when the
  org doesn't have the new billing activity pages switched on; that's a server-side setting, so
  tell the user rather than retrying.
- **Dates must be full timestamps.** `from=2026-10-01` is a 400; use `2026-10-01T00:00:00Z`. Daily
  buckets are UTC, and a `to` at midnight still returns that day's bucket.
- **64-bit counters come back as JSON strings.** `billedTimeSeconds`, `instanceCount`,
  `billedBytes`, `totalCacheReadTokens` are `"893"`, not `893`: wrap them in `tonumber` before
  doing maths in `jq`.
- **`usage-report-rollup` rows are per resource over the whole range, not per day.** Their
  `bucketStart` is just the range start. Use `dailyUsage` or `group-daily-totals` for days.
- **The old `GET /v1/billing/usage-report` (V1) is slow.** An org-wide, 30-day call timed out
  after 60 s; a one-day, one-teamspace call returned at once. Use `usage-report-rollup` instead,
  and keep any V1 call short and scoped to a teamspace.
- **`usage-details` needs the prefixed id** (`job_…`) from the rollup. The bare id gives an empty
  list, not an error. Its `hourlyPrice` can be `0` while `cost` is right; trust `cost`.
- **`subscription/quote` with an `orgId` returns `500 subscription is not managed by stripe`** for
  Enterprise orgs. Pass just `tier` for list prices. `annual-upsell` likewise 500s unless the org
  has a Stripe-managed monthly plan.
- **Quote amounts are USD cents; everything else is credits (= USD).** `monthlyAmount: 5000` is
  $50.
- **`account-balance`, `inference-rate-limit` and `inference-usage` are not for agents.** They
  belong to the model-proxy edge worker and answer `401 invalid worker key` to user credentials.
  `POST /v1/billing/{userId}/upgrade-trigger` is the web UI's upsell telemetry; don't call it.
- **`/v1/projects/{id}/billing/compute` is legacy** (its own comment asks whether to deprecate it).
  It only knows Studios and old-style experiments; use the rollup endpoints.
