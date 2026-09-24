---
name: lightning-cost-estimation
description: Estimate and compare what a workload costs on Lightning AI - fetch live per-hour GPU/CPU machine prices from the accelerator catalog for every cloud (Lightning Cloud, AWS, GCP, Lambda Labs, Nebius, Voltage Park), then quote a training run, a fine-tune, a serving deployment or a data-prep job for hours, days, weeks or months, including spot/interruptible rates, multi-node fan-out and Drive storage at $0.10/GB/month. Use when the user asks "how much would it cost to train/serve X", "what's the price of an H100/B200 on lightning", "how much for 32 8xH100 nodes for 2 weeks", "which cloud is cheapest for this", "what will my monthly bill be", or wants hardware sized for a model before pricing it.
---

# Lightning AI cost estimation

Every machine price on [lightning.ai/pricing](https://lightning.ai/pricing) comes from one
public endpoint. **Always fetch live prices, and fetch the SKU slugs too — never quote either
from memory.** Prices, spot rates, capacity *and slugs* change. This file deliberately carries no
price snapshot: one went stale and cost a real run 59 minutes — see **From a quote to a
launch** below.

**1 Lightning credit = $1 USD.** Machines bill **per second** while allocated; storage bills
daily. Docs: [billing FAQ](https://lightning.ai/docs/platform/overview/faq/billing.md) ·
[manage costs](https://lightning.ai/docs/platform/team-management/organizations/manage-costs.md).

## Setup & auth

**The accelerator catalog needs no auth** — plain `curl` works, which makes this skill usable
before login:

```bash
curl -s "https://lightning.ai/v1/core/accelerators?cloudProvider=MACHINE" | jq '.accelerator | length'
```

The same call through the CLI (query string goes in the path, quoted). `lightning api` always
sends credentials, so this route needs `lightning login` or an API key first; only `curl` works
logged out:

```bash
# Use the Lightning AI CLI from the current env; install or upgrade it there if it's missing or older than 2026.9.18
v=$(lightning --version 2>/dev/null | sed -n 's/^Lightning CLI version //p')
[ -n "$v" ] && [ "$(printf '%s\n' 2026.9.18 "$v" | sort -V | head -1)" = 2026.9.18 ] \
  || uv pip install -U lightning-sdk || python3 -m pip install -U lightning-sdk
lightning --version   # must print "Lightning CLI version …"; if not, see the table below
lightning api "/v1/core/accelerators?cloudProvider=MACHINE"
```

Auth is only needed for the *optional* step of checking which clouds a teamspace can actually
reach (`lightning login`, or `LIGHTNING_API_KEY`, optionally with `LIGHTNING_USER_ID`). `jq` is required for
every recipe here.

This uses, and if needed installs into, the environment the agent runs in (the user's project
venv, a conda env, …). Then call plain `lightning …` everywhere. If setup doesn't go cleanly:

| Symptom | Fix |
|---|---|
| Both installs fail: no active env, or pip refuses with `externally-managed-environment` | Install it on its own instead: `uv tool install lightning-sdk` (or `pipx install lightning-sdk`), then call `"$(uv tool dir --bin)/lightning"` if that dir isn't on `PATH` |
| `lightning --version` still prints no `Lightning CLI version` line after installing | `command -v lightning` shows which one runs. Another tool owns the name (PyTorch Lightning also installs a `lightning` command), or the env you installed into isn't on `PATH`: activate it (`source .venv/bin/activate`) or call its `bin/lightning` by full path |
| `No such command`, `No such option` or `unexpected extra argument` | The CLI is older than this skill expects: `uv pip install -U lightning-sdk` (or `pip install -U lightning-sdk`) in the env `command -v lightning` points into; `uv tool upgrade lightning-sdk` for a uv tool install |
| The upgrade fails on a version conflict with the project's own pins | Don't fight the pins: install it outside the project with `uv tool install lightning-sdk` and call `"$(uv tool dir --bin)/lightning"` |
| Installing is blocked (read-only env or home, agent sandbox) | Skip it and run each command as `UV_CACHE_DIR="${TMPDIR:-/tmp}/uv" uvx lightning-sdk …` |
| Every call fails on SSL/certificates, even `lightning --version` (`Could not find a suitable TLS CA certificate bundle`), only inside an agent sandbox | A `**/*.pem` read-deny rule is hiding certifi's public CA bundle (`site-packages/certifi/cacert.pem`). Allow that one path; don't disable the sandbox |

## Resolving org and teamspace (only for availability checks)

Pricing is global — you do **not** need a teamspace to quote. You need one to check which
cloud accounts the user can launch on. **Never guess it**; resolve, and ask if ambiguous:

```bash
lightning api /v1/memberships | jq -r '.memberships[] | [.ownerType, .name, .projectId] | @tsv'

PID=$(lightning api /v1/memberships | jq -r '.memberships[] | select(.name=="<teamspace>") | .projectId' | head -1)
lightning api "/v1/projects/$PID/clusters" | jq -r '.clusters[].id'
```

Map the returned cluster ids to catalog providers with the table under *Cloud providers*. A
cloud missing from that list can't be launched on by this teamspace, so don't quote it as the
answer — mention it only as "available if you enable that cloud account".

## The accelerator catalog

`GET https://lightning.ai/v1/core/accelerators?cloudProvider=<PROVIDER>` →
`{"accelerator": [ ... ]}`. Fields that matter for costing:

| Field | Meaning |
|---|---|
| `cost` | **On-demand USD per hour for the whole instance** (not per GPU). `9` on `gpu-h100-2x` = $9/hr for both GPUs. |
| `spotPrice` | Interruptible/spot USD per hour. **`0` means the provider has no spot** — do not read it as free. It is also *not always cheaper* than `cost`; compare, don't assume. |
| `slugMultiCloud` | Cross-cloud canonical name (`lit-h100-80gb-8`). **Use this to compare the same SKU across clouds.** |
| `slug` / `instanceId` | Provider-specific names (`gpu-h100-8x` / `lit-h100-80gb-8` on Lightning, `p5.48xlarge` on AWS). |
| `family` | `CPU`, `DATA-PREP`, `T4`, `L4`, `L40S`, `RTXP`, `A100`, `H100`, `H200`, `B200`, `TPU`. The SDK spells the RTX family `RTX PRO`, so filter with a loose regex (`RTX`). |
| `resources.gpu` | GPUs per instance (0 on CPU SKUs — use `resources.cpu` there). |
| `resources.gpuType` | e.g. `nvidia-h100-80gb`, `nvidia-h200-141gb`, `nvidia-b200-180gb`. Sometimes omits VRAM (`nvidia-b200`) — see the VRAM table. |
| `resources.memoryMb`, `resources.cpu`, `resources.storageGb` | Host RAM / vCPU / attached disk. Occasionally wrong (AWS `lit-h200x-8` reports `141000000` MB) — sanity-check before quoting RAM. |
| `outOfCapacity` | `true` = currently unavailable. **A cheap SKU that is out of capacity is not a real quote.** |
| `availableInSeconds` / `availableInSecondsSpot` | Estimated wait to get the machine. Values in the 10⁴–10⁶ range mean "effectively unobtainable right now". |
| `dwsOnly` / `dwsCost` | GCP Dynamic Workload Scheduler only — queued/batch allocation, not on-demand. |
| `reservable` / `capacityBlockPrice` | AWS capacity blocks: a **materially cheaper** reserved rate for long runs (H100×8 $43.61 vs $64.96 on-demand). Read it per SKU — the discount ranges ~18–55%. |
| `isTierRestricted` | Needs a paid plan tier. |
| `enabled`, `availableZones`, `clusterId` | Whether it's live, where, and which cloud account it belongs to. |

### Cloud providers

| `cloudProvider=` | What it is | Cluster id | When to use |
|---|---|---|---|
| `MACHINE` | **Lightning Cloud** (Lightning's own bare metal) | `lightning-baremetal` | **Default and preferred.** Cheapest generally-available H100s and near-zero queue times. |
| `AWS` | AWS | `lightning-public-prod` | Widest SKU range (T4/L4/L40S/RTXP/A100/H100/H200), capacity blocks. |
| `GCP` | GCP | `gcp-lightning-public-prod` | Only source of TPUs; B200/H200 via DWS. |
| `LAMBDA_LABS` | Lambda Labs | `lightning-lambda-prod` | Secondary — cheap H100/B200 when in capacity. |
| `NEBIUS` | Nebius | `lightning-nebius-prod` | Secondary — good 1×B200 / 1×H200 availability. |
| `VOLTAGE_PARK` | Voltage Park | `lightning-voltagepark-prod` | Secondary — cheapest H100 list price, frequently out of capacity. |
| `VULTR` | Vultr | — | Niche (A16/A40/A100/L40S). |

Quote from `MACHINE` first, then AWS/GCP, and only bring up the secondary clouds when they win
on price *and* are in capacity.

### From a quote to a launch

The `--machine` / `Machine.<NAME>` values used by `lightning-jobs`, `lightning-studios` and
`lightning-deployments` map onto catalog SKUs like this — quote the SKU, launch with the name:

| `--machine` | `slugMultiCloud` | | `--machine` | `slugMultiCloud` |
|---|---|---|---|---|
| `CPU_SMALL` | `cpu-2` | | `A100` … `A100_X_8` | `lit-a100-40gb-N` or `lit-a100-80gb-N` (by cloud) |
| `CPU` | `cpu-4` | | `H100` … `H100_X_8` | `lit-h100-80gb-1` … `-8` |
| `CPU_X_8` / `CPU_X_16` | `cpu-8` / `cpu-16` | | `H200` … `H200_X_8` | **varies by cloud — look it up** |
| `DATA_PREP` / `_MAX` / `_ULTRA` | `data-prep-mid` / `-max-large` / `-ultra-extra-large` | | `B200` / `B200_X_8` | **varies by cloud — look it up** |
| `T4` / `L4` / `L40S` (`_X_N`) | `lit-t4-N` / `lit-l4-N` / `lit-l40s-N` | | `RTXP_6000` (`_X_N`) | look it up (family `RTXP`) |

`A100_40GB*` / `A100_80GB*` pin the memory size but are hidden from CLI `--machine` (the CLI
rejects them); they work only as `Machine.A100_80GB_X_8` in Python or with `studio switch`.
`T4_SMALL` also exists.

Add `--cloud <cluster-id>` (e.g. `--cloud lightning-baremetal`) to pin the cloud you priced;
without it the job lands on the teamspace default and may bill a different rate.

**The same `--machine` value maps to different slugs on different clouds, so never hardcode one.**
H200 is `lit-h200x-1` on Nebius but `lit-h200-141gb-1` on Lightning Cloud, and B200 has shown up as
`lit-b200x-8`, `lit-b200-180gb-8` and `lit-b200-8`. The SDK (2026.9.18+) treats all of these as the
same `H200*` / `B200*` machine (canonical `lit-h200-N` / `lit-b200-N`), but the catalog may still
return any of them. Resolve it live:

```bash
# every SKU on Lightning Cloud whose name mentions the accelerator you want
curl -s "https://lightning.ai/v1/core/accelerators?cloudProvider=MACHINE" \
| jq -r '.accelerator[] | select((.displayName + .family + .slugMultiCloud) | test("h200"; "i"))
         | [.slugMultiCloud, .displayName, .cost, .availableInSeconds] | @tsv'
```

Match on the accelerator's display name and family as well as the slug (there is no `name` field; `null + string` in jq silently drops it). Filtering on a slug you assumed makes a
machine that exists look unavailable — which is exactly how a run ended up queued 59 minutes for
an H100 while an idle H200 sat in the same pool at the same price.

## Price lookup recipes

Drop these into the shell once, then use them for every quote.

```bash
# _litfetch <PROVIDER> — catalog JSON. Fails LOUDLY: a silently dropped cloud
# would make litcompare name the wrong winner.
_litfetch() {
  curl -sf --retry 3 --retry-delay 1 --max-time 20 \
    "https://lightning.ai/v1/core/accelerators?cloudProvider=$1" \
  || { echo "ERROR: could not fetch $1 catalog — do not quote a comparison" >&2; return 1; }
}

# litprice <PROVIDER> [family-regex] — live catalog, one row per SKU
litprice() {
  _litfetch "${1:-MACHINE}" \
  | jq -r --arg re "${2:-.}" '
      ["SKU","FAMILY","N","USD/HR","SPOT/HR","RAM_GB","WAIT_S","STATUS"],
      (.accelerator[]
       | select(.family|test($re;"i"))
       | [ .slugMultiCloud, .family,
           (if .acceleratorType=="GPU" then .resources.gpu else .resources.cpu end),
           .cost,
           (if .spotPrice > 0 then (.spotPrice*100|round/100) else "n/a" end),
           ((.resources.memoryMb|tonumber)/1024|round),
           .availableInSeconds,
           ([ (if .outOfCapacity then "OUT-OF-CAPACITY" else empty end),
              (if .dwsOnly then "DWS-ONLY" else empty end),
              (if .isTierRestricted then "TIER-RESTRICTED" else empty end),
              (if .reservable then "RESERVABLE" else empty end) ]
            | if length==0 then "ok" else join(",") end )
         ])
      | @tsv' | column -t
}

# litcompare <slugMultiCloud> — same SKU priced across every cloud, cheapest first
litcompare() {
  { printf 'CLOUD\tSLUG\tUSD/HR\tSPOT/HR\tWAIT_S\tSTATUS\n'
    for p in MACHINE AWS GCP LAMBDA_LABS NEBIUS VOLTAGE_PARK VULTR; do
      _litfetch "$p" \
      | jq -r --arg p "$p" --arg s "$1" '.accelerator[] | select(.slugMultiCloud==$s)
          | [ $p, .slug, .cost,
              (if .spotPrice > 0 then (.spotPrice*100|round/100) else "n/a" end),
              .availableInSeconds,
              ([ (if .outOfCapacity then "OUT-OF-CAPACITY" else empty end),
                 (if .dwsOnly then "DWS-ONLY" else empty end),
                 (if .isTierRestricted then "TIER-RESTRICTED" else empty end),
                 (if .reservable then "RESERVABLE" else empty end) ]
               | if length==0 then "ok" else join(",") end )
            ] | @tsv'
    done | sort -k3 -n; } | column -t
}

# litquote <usd_per_hour> <instances> <hours> [storage_gb] [storage_days]
litquote() {
  jq -n --argjson r "$1" --argjson n "$2" --argjson h "$3" \
        --argjson gb "${4:-0}" --argjson d "${5:-0}" '
    ($r*$n*$h) as $c | (([$gb-10,0]|max) * 0.10 * ($d/30)) as $s
    | { rate_per_hour: $r, instances: $n, hours: $h,
        cluster_per_hour: ($r*$n*100|round/100),
        compute_usd: ($c*100|round/100),
        storage_usd:  ($s*100|round/100),
        total_usd:   (($c+$s)*100|round/100) }'
}
```

```bash
litprice MACHINE                 # everything on Lightning Cloud
litprice GCP 'B200|H200'         # just the Blackwell/Hopper-refresh SKUs on GCP
litcompare lit-h100-80gb-8       # who has the cheapest 8×H100 right now
litquote 36 32 336               # 32 nodes × 8×H100 × 2 weeks
```

## Cost formulas

Durations: 1 day = 24 h · 1 week = 168 h · 1 month = 730 h (or `days × 24`) · 1 year = 8760 h.

```
compute_usd  = cost_per_hour × instances × hours          # billed per second, no rounding up
spot_usd     = spotPrice     × instances × hours          # only if spotPrice > 0
storage_usd  = max(0, drive_gb − 10) × $0.10 × months     # first 10 GB free, billed daily
total_usd    = compute_usd + storage_usd
```

- **`cost` is per instance, not per GPU.** For a per-GPU-hour figure divide by `resources.gpu`.
- **Multi-node**: total = per-node `cost` × `num_machines`. A multi-machine job with `--num-machines 32`
  on `H100_X_8` bills 32 × the 8×H100 rate.
- **Serving**: total = `cost` × replicas × hours. With autoscaling (`--min-replicas` /
  `--max-replicas`), quote a **range**: `min_replicas` is the floor you pay 24/7 (0 if
  scale-to-zero), `max_replicas` the ceiling. For per-token economics:

  ```
  usd_per_1M_tokens = (cost_per_hour × replicas) / (tokens_per_sec × 3600 / 1e6)
  ```

  `tokens_per_sec` is the *measured* aggregate output throughput of your server at your batch
  size — never guess it; benchmark, or give a range. If the user only wants to *call* a model
  rather than own the GPU, compare against the per-token hosted models in the
  `lightning-llm-gateway` skill: a dedicated GPU only wins above a fairly high steady load.
- **Spot / interruptible** (`--interruptible`, `interruptible=True`) is typically 15–60% off, but
  can be preempted — only recommend it for checkpointed training, never for a serving SLA.
- **Billing covers the whole machine-allocation window, not just your workload.** The bill runs
  from `startedAt` to `stoppedAt`, which includes the image pull — and the SDK reports the job as
  `Pending` for most of that pull. Budget for it: a large image can add several minutes of billed
  time before your first line of code runs. Quote container startup as a separate line item, or
  give the estimate as a range.
- Every account gets **one free 4-CPU Studio**; additional machines bill at list rate.
- **Folders are billed, connections are not — and the API calls both "data connections".** Data
  *added* to a teamspace as a **folder** is internal storage and bills like Drive. Data
  *connected* from outside (an S3/GCS bucket you already own and pay for) is **not** billed by
  Lightning. Both appear under `/v1/projects/<pid>/data-connections`, so the endpoint name tells
  you nothing — go by `isBillableFolder: true`. `Teamspace.new_folder()` creates the **billed**
  kind, so a "just put the dataset somewhere reusable" step is a storage line item, not free.

Always state assumptions with the number: rate, instance count, hours, spot-or-on-demand,
storage, and the date you fetched prices.

## Sizing hardware for a model (do this before pricing)

When the user names a model instead of a machine, size it first, state the assumptions, then
price it. VRAM per GPU (the catalog doesn't always carry this):

| Family | VRAM/GPU | Family | VRAM/GPU |
|---|---|---|---|
| T4 | 16 GB | H100 | 80 GB |
| L4 | 24 GB | H200 | 141 GB |
| A100 | 40 or 80 GB (check `gpuType`) | B200 | 180 GB |
| L40S | 48 GB | RTXP (RTX PRO 6000) | 96 GB |
| A40 | 48 GB | TPU v6e | 32 GB HBM/chip |

**Inference / serving memory**

```
weights_gb ≈ params_B × bytes_per_param        # BF16/FP16 = 2, FP8 = 1, INT4 = 0.5
kv_cache_gb ≈ 2 × layers × kv_heads × head_dim × bytes × ctx_len × concurrency / 1e9
vram_needed ≈ weights_gb × 1.15 + kv_cache_gb   # ~15% for activations/fragmentation
```

Pick the smallest SKU where `vram_needed ≤ gpus × vram_per_gpu`, keeping tensor-parallel degree
a power of 2. A 70B model in BF16 ≈ 140 GB → 2×H100, or 1×B200 with room for KV cache.

**Training memory** — full fine-tune with AdamW mixed precision ≈ **16–18 bytes/param**
(weights + grads + fp32 master + optimizer states) plus activations, sharded across GPUs with
FSDP/ZeRO-3. LoRA/QLoRA ≈ quantized weights + a few % for adapters, so it usually drops a 70B
job from 8 GPUs to 1–2.

**Training time** (this is what turns into money):

```
flops       = 6 × params × tokens                       # fwd + bwd, dense transformer
gpu_hours   = flops / (peak_flops × mfu × 3600)
wall_hours  = gpu_hours / (num_gpus × scaling_efficiency)
cost        = wall_hours × nodes × node_cost_per_hour
```

Dense BF16 peak (no sparsity): A100 312 TFLOPS · H100/H200 989 TFLOPS · B200 ≈ 2250 TFLOPS ·
L40S 362 TFLOPS · L4 121 TFLOPS. Use **MFU 0.35–0.5** (0.4 is a fair default) and **scaling
efficiency ~0.9 at 8–16 nodes, ~0.8 at 32+**. These are estimates — say so, and give a range.

## Example workflows

Prompts this skill handles: *"how much to train on 32 8×H100 nodes for 2 weeks?"*, *"what
would it cost to serve a model on B200?"*, *"cheapest way to fine-tune Llama-70B"*, *"what's my
monthly bill if I keep 2 A100s and 5 TB of data?"*.

**1. 32 nodes of 8×H100 for 2 weeks (and 2 months)**

```bash
litcompare lit-h100-80gb-8          # -> MACHINE 36.00/hr, spot 30.56, wait ~145s  (cheapest in capacity)
litquote 36 32 336                  # 2 weeks:  $1,152/hr -> $387,072
litquote 36 32 1440                 # 2 months: $1,152/hr -> $1,658,880
litquote 30.56 32 336               # same on spot         -> $328,581
litquote 36 32 336 20480 14         # + 20 TB of checkpoints for the fortnight -> +$955
```

Report it as: *"32 × 8×H100 on Lightning Cloud is $36/node/hr = **$1,152/hr**; 2 weeks (336 h) =
**~$387k** on-demand, **~$329k** on spot. Add ~$955 if you keep 20 TB of checkpoints in the
Drive for those two weeks. Prices fetched <date>."*

**2. Serve a model on B200**

```bash
litprice NEBIUS 'B200'; litprice GCP 'B200'; litprice LAMBDA_LABS 'B200'
```

Lightning Cloud has no B200 today, so quote the secondary clouds and flag the caveats: Nebius
1×B200 $9.86/hr → **~$7,100/month** at 24/7 (720 h); 8×B200 $78.87/hr → ~$56.8k/month. GCP's
8×B200 is `dwsOnly` (queued, not on-demand) and Lambda's is currently `outOfCapacity`. If the
deployment scales to zero, quote the range instead: `$0 → replicas × rate × hours`.

**3. LoRA fine-tune a 7B model**

Sizing: 7B in BF16 ≈ 14 GB weights + LoRA adapters → fits one H100 80 GB comfortably.

```bash
litprice MACHINE 'H100'             # lit-h100-80gb-1 -> $4.50/hr
litquote 4.5 1 6                    # ~6 h run -> $27
```

**4. Steady-state monthly bill**

```bash
litquote 5.68 2 730 5120 30         # 2 × 1×A100-80GB on GCP, 24/7, + 5 TB Drive
```

→ compute $8,292.80 + storage $511 ≈ **$8,804/month**. If the machines only run 8 h/day, pass
`243` hours instead and the compute drops to ~$2,760.

## Raw API fallback

```bash
# full record for one SKU (all capacity/quota/reservation fields)
curl -s "https://lightning.ai/v1/core/accelerators?cloudProvider=AWS" \
| jq '.accelerator[] | select(.slugMultiCloud=="lit-h100-80gb-8")'

# AWS reserved capacity-block rate vs on-demand
curl -s "https://lightning.ai/v1/core/accelerators?cloudProvider=AWS" \
| jq -r '.accelerator[] | select(.reservable) | [.slugMultiCloud, .cost, .capacityBlockPrice] | @tsv'

# what a finished job actually cost (needs auth) — ground-truth to check an estimate against
lightning job inspect <job-name> --teamspace <owner>/<teamspace>
```

`job.total_cost` gives the realized USD for a finished job (see the `lightning-jobs` skill), but
**wait for it to settle** — see the Gotchas below. The `Machine.<NAME>` constants carry no prices
(`.cost` and `.interruptible_cost` are `None`). With auth, `Teamspace("owner/teamspace").list_machines()`
returns `Machine` objects with `cost`, `interruptible_cost`, `wait_time` and `provider` filled in
from this same catalog, limited to machines in capacity on the teamspace's cloud accounts. For
cross-cloud comparisons and quotes before login, use the REST catalog above.

## Gotchas

- **`Machine.<NAME>.cost` and `.interruptible_cost` are `None` for every constant.** `.slug` is
  populated, so the object looks hydrated and the `None` reads like a bug. Prices come from the
  REST accelerator catalog, or from `Teamspace(...).list_machines()` (needs auth), not the constants.
- **`job.total_cost` is provisional when a job first reports terminal, and keeps climbing.** It is
  not flagged as incomplete. A job that settled at `$0.16257313` read `$0.15243042` the moment
  `job.wait()` returned, and rose for ~2.5 minutes before stabilising; another read exactly `0.0`
  for 45–85s after finishing. **Never quote the first value.** Poll until it is unchanged across
  two or three reads before reporting a realized cost, and treat `0.0` on a just-finished job as
  "not computed yet", not "free".
- **The image pull is billed, and it is billed as `Pending`.** Billing spans
  `startedAt`→`stoppedAt`. A 1200-second training run on a `pytorch/pytorch` image billed 1683
  seconds — a 397-second image pull, during which the SDK reported `Pending`, plus shutdown. At
  spot `$0.34775/h` that is `1683 × 0.34775 / 3600 = $0.16257`, exactly the settled bill, versus
  `$0.1159` for the training time alone. **Estimating from workload duration alone under-quotes
  by ~40% on a short job.** The smaller the job, the worse the ratio.
- **`cost` is per instance, not per GPU.** `gpu-h100-8x` at `36` is $36/hr for all 8 GPUs
  ($4.50/GPU-hr). Quoting it as per-GPU inflates an estimate 8×.
- **`spotPrice: 0` means "no spot on this provider"** (Lambda, Nebius, Voltage Park all report
  0), not "free". Only use `spotPrice` when it is `> 0`.
- **Spot is not always cheaper than on-demand.** Figures below are illustrations of the
  pattern, not current rates. GCP `lit-h200x-8` was $47.16 on-demand vs
  $52.74 spot; AWS `lit-l40s-1` $2.89 vs $3.02; GCP `lit-l4-1` $0.48 vs $0.71. Take
  `min(cost, spotPrice)` and say which one you quoted.
- **Unknown `cloudProvider` values silently return the AWS catalog.** `AZURE`, `KUBERNETES` and
  `TENSORDOCK` all echo AWS's 23 SKUs byte-for-byte — they are not real Azure prices. Only the
  seven providers in the Cloud providers table have been checked. The API's provider enum also
  lists `CUDO`, `MITHRIL`, `THUNDER_CAT`, `CLOUDFLARE`, `RAFAY`, `SLURM` and aggregate values;
  before quoting from one, diff its response against the AWS catalog to rule out the echo.
- **Never price from `cloudProvider=LIGHTNING` or `DGX`.** They return a single internal cluster
  entry with placeholder costs of `1` and `2`. The Lightning Cloud catalog is `MACHINE`.
- **The SDK's `CloudProvider` enum says `LIGHTNING`, the pricing API says `MACHINE`** — same
  platform, different spelling. To actually launch on it pass the cluster id:
  `--cloud lightning-baremetal`.
- **Don't join the catalog to `Machine` on `slug`.** `Machine.H100.slug` is `lit-h100-1` but the
  catalog's `slugMultiCloud` is `lit-h100-80gb-1`. Join on `family` + GPU count, which is what
  the SDK's own equality does.
- **Check `outOfCapacity` and `availableInSeconds` before naming a winner.** Voltage Park's
  $15.92 8×H100 is the cheapest number in the catalog and is currently unobtainable; GCP's
  1×H100 reports a wait of ~1.5M seconds. Cheapest-on-paper ≠ cheapest-you-can-run.
- **GCP B200/H200 are `dwsOnly`** — allocated through Dynamic Workload Scheduler (queued batch),
  so they are not a drop-in for an always-on serving endpoint.
- **AWS H100/H200/A100 are `reservable`** with a `capacityBlockPrice` well below on-demand, and
  the discount varies a lot by SKU (H100×8 $43.61 vs $64.96 = −33%; H200×8 −18%; A100-40GB×8
  $12.39 vs $27.67 = −55%). For any run measured in weeks, surface that number instead of the
  on-demand one — but read it per SKU, don't apply a flat percentage. The same three are
  `isTierRestricted` — a free-tier account can't launch them at any price.
- Storage is **$0.10/GB/month above the first 10 GB, billed daily** — trivial for code, material
  for training: 20 TB of checkpoints is ~$2,047/month. Always ask about checkpoint/dataset size
  for multi-week training quotes. Count teamspace **folders** in that figure (see the folder vs
  connection note above); an externally connected bucket is billed by its own cloud, not here.
- **Stored bytes are not observable from the platform.** `currentStorageBytes` on
  `/v1/memberships` does not move when a folder is created, filled or deleted — it read
  byte-identical before, during and after storing 1.078 GB in a folder flagged
  `isBillableFolder: true`. Size the storage line from what you know you uploaded; you cannot
  reconcile it against the platform afterwards.
- Estimates for model training are **estimates**. MFU, dataloader stalls, restarts and multi-node
  scaling losses move the real number by tens of percent — give a range and list the assumptions
  rather than a single confident figure.
- Confirm with the user before *launching* anything you priced; this skill only quotes.
