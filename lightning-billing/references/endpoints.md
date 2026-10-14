# Billing API endpoint reference

Every endpoint of the control plane's `BillingService`, grouped by what it does. Call them with
`lightning api`; for GETs, pass parameters as `-X GET -f key=value` (repeat a key for a list, dot
into nested fields). JSON field names are camelCase.

**Status** says how each was checked against production `lightning.ai` (October 2026):

- **live**: called and the response matched what's described here.
- **not run**: not called, because it spends or moves money or writes a record; the fields come
  from the API definition.
- **worker only**: answers `401 invalid worker key` to user credentials.

## Contents

- [Balances](#balances)
- [Usage reports](#usage-reports)
- [Usage report filters](#usage-report-filters)
- [Plans and subscriptions](#plans-and-subscriptions)
- [Coding plan](#coding-plan)
- [Moving credits and paying](#moving-credits-and-paying)
- [Internal endpoints](#internal-endpoints)
- [Enums](#enums)

## Balances

| Method & path | Status | Params | Returns |
|---|---|---|---|
| `GET /v1/users/billing/balance` | live | `includeTransactions` | `accountId`, `balance`, `totalSpent` (lifetime), `transactions[]` |
| `GET /v1/orgs/{orgId}/billing/balance` | live | `includeTransactions` | `accountId`, `balance`, `balanceLimit`, `transactions[]` (`projectId` is empty) |
| `GET /v1/projects/{projectId}/billing/balance` | live | `includeTransactions` | `projectId`, `accountId`, `balance`, `balanceLimit`, `transactions[]` |

`balanceLimit` is how low the balance may go before workloads are shut down; an org on a credit
line shows a negative number. A transaction is `{createdAt, amount, type, displayName,
projectName}`; types seen include `auto-transfer`.

## Usage reports

Times are RFC 3339 timestamps (`2026-10-01T00:00:00Z`). Costs are credits.

### `GET /v1/billing/usage-report-rollup` (live)

The per-resource spend list, from the daily rollup table. Rows are per resource over the whole
range, not per day.

| Param | Notes |
|---|---|
| `orgId` | **required**, even with `projectIds` (without it: `403`) |
| `projectIds`, `userIds`, `resourceTypes`, `resourceIds`, `clusterIds` | repeatable filters |
| `from`, `to` | range |
| `limit` | page size; always set it |
| `searchAfter`, `searchAfterResourceId`, `searchAfterResourceType` | cursor: copy all three from the previous response |
| `subfilters.*` | see [subfilters](#subfilters) |

Returns `totalCost`, `totalSavedCost`, `usage[]`, `dailyUsage[]`, `hasMore` and the three cursor
fields. `usage[]` rows: `id` (type-prefixed, e.g. `job_…`, `msg_…`), `userId`, `projectId`,
`clusterId`, `resourceType`, `resourceName`, `createdAt`, `deletedAt`, `billedTimeSeconds`
(string), `cost`, `savedCost`, `bucketStart` (the range start). `dailyUsage[]`: `day`,
`totalCost`, `totalSavedCost`, `totalPromptTokens`, `totalCompletionTokens`, `totalNumMessages`.

### `GET /v1/billing/usage-report-rollup/group-totals` (live)

Cost summed per group over the whole range. Same filters as the rollup, without pagination, plus
**required** `groupBy` (see [enums](#enums)). Returns `groups[]` of `{groupValue, cost,
savedCost}`, most expensive first, plus `totalCost` and `totalSavedCost`. `groupValue` is the
resource type, user id or project id; unassigned usage groups under `""` (teamspace) or the nil
UUID (user).

### `GET /v1/billing/usage-report-rollup/group-daily-totals` (live)

As `group-totals`, split by UTC day. Returns `dailyTotals[]` of `{day, groupValue, cost,
savedCost}`, newest day first; days with no usage are left out.

### `GET /v1/billing/usage-details` (live)

Per-machine-type sessions behind one resource.

| Param | Notes |
|---|---|
| `projectId`, `resourceId`, `resourceType` | `resourceId` is the prefixed id from the rollup |
| `sessionStartedAt` | picks one Studio session (all share the Studio's id) |
| `sessionEndedAt` | picks one teamspace-storage session |

Returns `details[]`: `instanceType`, `instanceCount`, `startedAt`, `endedAt`,
`billedTimeSeconds`, `hourlyPrice` (can be `0`), `cost`, `savedCost`, `spot`, `free`,
`billedBytes`, `readBytes`, `writtenBytes`, `resourceName`, `userId`. Counters are strings.

### `GET /v1/billing/usage-report` (live, slow)

The original V1 report, from raw billing sessions: one row per **usage session**, not per
resource. Params: `orgId`, `projectId`, `clusterId`, `resourceType`, `resourceId`, `userId`,
`from`, `to`, `timeZone` (UTC offset), `limit`, the same three `searchAfter*` cursor fields,
`includeUnassignedProject`, `includeUnassignedUser`. Returns `totalCost`, `totalSavedCost`,
`totalUsers`, `usage[]` (adds `sessionStartedAt`, `sessionEndedAt`, `free`, `spot`,
`instanceType` and token counts to the rollup's fields), `dailyUsage[]`, `hasMore` and the cursor.
An org-wide 30-day call timed out after 60 s; prefer the rollup.

### `GET /v1/billing/assistant-session` (live)

LLM/model API spend (`assistant_message`) per day. Params: `orgId`, `projectId`, `userId`,
`from`, `to`, `details` (true splits rows by user and model). Returns `totalCost`, `totalUsers`,
`dailyUsage[]` (for a chart) and `usage[]` (grouped by name, for a table). Rows: `day`,
`projectId`, `userId`, `orgId`, `parentId`, `name`, `modelId`, `totalCost`,
`totalPromptTokens`, `totalCompletionTokens`, `numMessages`, `totalCacheReadTokens`,
`totalCacheCreationTokens`, `totalCacheCreationTokens1h`. Cache tokens are not part of
`totalPromptTokens`.

### `GET /v1/billing/sandbox-usage/activity` (live)

Sandbox activity per 15-minute bucket. Params: `orgId`, `projectId`, `userId`, `from`, `to`.
Returns `buckets[]` of `{bucket, activeCount, totalCost}`.

### `GET /v1/billing/monthly-summary` (live)

Credits per calendar month. Params: `orgId` and a **required** time filter, exactly one of:

- `timeFilter.rangeFilter.rangeStart` + `timeFilter.rangeFilter.rangeEnd`
- `timeFilter.pivotFilter.pivotDirection` (`BEFORE` or `AFTER`) + `timeFilter.pivotFilter.pivot`

The range may span at most two years. Returns `orgId` and `monthlySummaries[]` of
`{periodStart, periodEnd, totalCreditsPurchased, totalCreditsRemaining}`. There is no consumed
field; the first period starts when the org was created.

### `GET /v1/projects/{projectId}/billing/compute` (live, legacy)

Per-day Studio and experiment costs. Params: `from`, `to` (default today), `resourceId`. Returns
`usage[]` of `{date, totalCostSessions, totalCostExperiments, usage[]}`. Predates the rollup and
misses newer resource types.

## Usage report filters

### `GET /v1/billing/usage-report/filter-values` (live)

Values the reports can be filtered by. Params: `orgId` (**required**, even with `projectId`),
`projectId` (scopes to one teamspace). Returns `projectIds`, `userIds`, `resourceTypes`,
`clusterIds` and `subfilters`:

```json
{"job": {"resourceType": "Job", "tags": [{"id": "…", "name": "data"}]},
 "multiMachineJob": {"resourceType": "Multi-machine job", "tags": […]},
 "deployment": {"resourceType": "Deployment", "tags": […]},
 "assistantMessage": {"apiKeyIds": [{"id": "…", "name": "CI key"}]}}
```

### `GET /v1/billing/usage-report/filter-values/resource-names` (live)

Searchable, paginated resource ids with display names, for Studios, jobs, deployments and
multi-machine jobs. Params: `orgId`, `projectId`, `searchQuery`, `pageSize`, `pageToken`.
Returns `resourceIds[]` of `{id, name}` and `nextPageToken`.

### Subfilters

Accepted by `usage-report-rollup`, `group-totals` and `group-daily-totals`:

| Key | Matches |
|---|---|
| `subfilters.job.tagIds`, `subfilters.job.matchAllTags` | job rows with any (or all) of the tags |
| `subfilters.multiMachineJob.tagIds`, `….matchAllTags` | multi-machine job rows |
| `subfilters.deployment.tagIds`, `….matchAllTags` | deployment rows |
| `subfilters.assistantMessage.apiKeyIds` | LLM spend billed through these API keys |

Several set: a row matching any of them is kept. Once any is set, rows of resource types with no
subfilter are dropped.

## Plans and subscriptions

### `GET /v1/billing/subscription` (live)

Params: `orgId` (**required**), `projectId`. Returns `name` (plan), `amount`, `period`, `seats`,
`status`, `periodEnd`, `canceledAt`, `stripeManaged`, `cardType`, `cardLast4`, `cardVerified`,
`cardVerificationError`, `creditCardUnlockGranted`, and `features[]` of `{key, currentUsage,
limit, hitLimit, upgradePath}`. No `limit` with `hitLimit: false` is unlimited; with
`hitLimit: true`, the plan lacks the feature.

### `GET /v1/billing/subscription/quote` (live)

Params: `orgId` (`""` for a personal account), `tier` (see [enums](#enums)). Returns, in **USD
cents**: `monthlyAmount`, `annualAmount`, `proratedAmount`, `proratedTax`, `monthlyTax`,
`annualTax`; plus `trialDays`, `eligible` and `features[]`. With the `orgId` of a non-Stripe
(Enterprise) org it returns `500 subscription is not managed by stripe`; pass only `tier` for list
prices.

### `GET /v1/billing/annual-upsell` (live: errors on non-Stripe plans)

Price to switch a monthly plan to annual. Params: `orgId`. Returns `annualAmount`,
`discountedAmount`, `additionalMonths`, `includedCredits`, `academic`. Returns `500` for Free and
Enterprise orgs.

### `PUT /v1/billing/subscription` (not run, charges the card)

Body: `orgId`, `seats`, `status`, `billingPeriod` (see [enums](#enums)), `cancellationMetadata`
`{reason, message}`. Returns the updated subscription.

### `POST /v1/billing/annual-upsell` (not run, charges the card)

Body: `orgId`. Returns `paymentSucceeded`.

## Coding plan

### `GET /v1/billing/coding-usage` (live)

No params. Returns `plans[]`, the caller's personal plan first, then every org they belong to:
`orgId`, `orgName`, `personal`, `planName`, `monthlyAllowanceCredits`, `unlimited`, `enforced`
(false while usage is measured but not limited), `usageCreditsEnabled`, `creditBalance` (personal
only), `windows[]` of `{kind, limitCredits, usedCredits, startedAt, resetsAt}`. Also
`modelWeights[]` of `{model, weight}`: how much a credit of list-price usage on each model counts
against the allowance.

### `PUT /v1/billing/coding-usage/settings` (not run, enables pay-as-you-go)

Body: `orgId`, `usageCreditsEnabled`. When true, requests past a spent window keep going and are
paid from credits instead of being rejected. Returns `usageCreditsEnabled`.

## Moving credits and paying

All **not run**. Each spends or moves real money; get the user's explicit go-ahead first.

| Method & path | Body | Returns |
|---|---|---|
| `POST /v1/orgs/{orgSrcId}/billing/transfer` | `projectDstId`, `amount` (omit for all) | `{}` |
| `POST /v1/projects/{projectSrcId}/billing/transfer` | `projectDstId` **or** `orgDstId`, `amount` | `projectSrcBalance`, `projectDstBalance` |
| `POST /v1/billing/checkout` | `amount`, `walletType` (`Project` default, or `Org`), `projectId` or `orgId`, `redirectUrlSucceeded`, `redirectUrlCancelled`, `transactionType`, `captureMethod` | `url` (Stripe Checkout) |
| `POST /v1/orgs/{orgId}/billing/checkout` | `amount`, `transactionType`, `captureMethod` | `url`. **Deprecated**: use `/v1/billing/checkout` with `walletType=Org` |
| `POST /v1/billing/subscription/checkout` | `orgId`, `tier`, `billingPeriod`, `seats`, `redirectUrlSucceeded`, `redirectUrlFailed`, `upgradeFeature`, `productId` | `url` (Stripe Checkout) |
| `POST /v1/billing/portal-session` | `orgID`, `returnURL`, `flowType` (capitalised `ID`/`URL`) | `url` (Stripe customer portal) |

The three `checkout`/`portal` calls only mint a link; nothing is charged until the user finishes
in the browser.

## Internal endpoints

Don't call these for a user.

| Method & path | Status | What it is |
|---|---|---|
| `GET /v1/billing/account-balance` | worker only | the model proxy's balance and coding-plan check |
| `GET /v1/billing/inference-rate-limit` | worker only | the model proxy's per-caller rate limit |
| `POST /v1/billing/inference-usage` | not run (worker only) | the model proxy reporting token usage, which bills it |
| `POST /v1/billing/{userId}/upgrade-trigger` | not run | web UI telemetry: records which feature limit prompted an upgrade |

## Enums

Pass enum values by name.

| Enum | Values |
|---|---|
| `groupBy` | `USAGE_GROUP_BY_RESOURCE_TYPE`, `USAGE_GROUP_BY_USER`, `USAGE_GROUP_BY_TEAMSPACE` |
| `tier` | `Free`, `Pro`, `Teams`, `Enterprise`, `Academic` |
| `billingPeriod` | `Month`, `Year` |
| `walletType` | `Project`, `Org` |
| coding window `kind` | `CODING_USAGE_WINDOW_KIND_FIVE_HOUR`, `…_WEEKLY`, `…_MONTHLY` |
| `pivotDirection` | `BEFORE`, `AFTER` |
