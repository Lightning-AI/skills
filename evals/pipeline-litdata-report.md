# Pipeline and LitData agent evaluation

Status: initial evaluation complete; two empty folder deletions pending, 2026-10-14. [PR #60](https://github.com/Lightning-AI/skills/pull/60).

## Objective and task board

Help a fresh agent complete useful work with fewer tool calls and tokens. Prioritize
compact dataset preparation and pipeline composition through `lightning_storage`.
Compare efficiency only after checking the output. Resume and scheduling are
secondary coverage.

| Task | Acceptance criteria | Original skill | Revised skill |
|---|---|---|---|
| LitData: compact photo dataset | Preserve 48 JPEGs, metadata and splits; load decoded training batches with two workers; measure source, optimized and raw-pixel bytes | Passed | Passed; final trial used fewer calls/tokens |
| Pipelines: prepare → consume | Separate jobs exchange 100 records and a generated marker through managed storage; download verified summary/logs | Failed; missing managed folder, recovery stopped early | Passed workflow and file cleanup; registration deletion pending |
| Production cleanup | Remove only test pipelines, jobs, schedules and storage files; read back absence | Compute and data removed; empty registration remains | Compute and data removed; empty registration remains |
| PR integration | Publish prompts, measurements, findings and checks alongside skills | — | Report, task definitions and metrics added; live CI status is on the PR |

Task prompts and assertions live in [LitData evals](../lightning-litdata/evals/evals.json)
and [Pipeline evals](../lightning-pipelines/evals/evals.json).

## Primary results: GPT-6 Luna, medium reasoning

| Task / skill variant | Result | Tool calls / operations | Seconds | Accumulated tokens |
|---|---|---:|---:|---:|
| Photo: original | Passed | 13 / 13 | 77.08 | 613,817 |
| Photo: first revision | Passed; efficiency regression | 19 / 19 | 101.23 | 880,530 |
| Photo: final revision | Passed | 10 / 10 | 58.53 | 444,275 |
| Storage pipeline: original | Failed | 93 / 93 | 814.94 | 8,129,362 |
| Storage pipeline: revision | Workflow and file cleanup passed; registration pending | 41 / 41 | 491.87 | 2,418,241 |

| Variant | Input tokens | Cached input | Uncached input | Output tokens |
|---|---:|---:|---:|---:|
| Photo: original | 608,974 | 557,568 | 51,406 | 4,843 |
| Photo: first revision | 873,994 | 844,800 | 29,194 | 6,536 |
| Photo: final revision | 441,001 | 425,216 | 15,785 | 3,274 |
| Storage pipeline: original | 8,091,231 | 7,937,536 | 153,695 | 38,131 |
| Storage pipeline: revision | 2,404,665 | 2,342,400 | 62,265 | 13,576 |

The final photo trial used 23% fewer tool calls, 28% fewer accumulated tokens and
24% less elapsed time than the original. These are individual observations, not
an established average speedup. The first revision's lower uncached input did
**not** make it more efficient: calls, total tokens and time all increased.

### Photo evidence and trace findings

All three trials chose `Image(path=...)`, 64 MB chunk targets and no extra chunk
compression. Source JPEGs total 678,356 bytes; decoded RGB totals 9,437,184 bytes.
Optimized chunks plus indexes total 683,366 / 683,548 / 683,549 bytes respectively.
The output is slightly larger than the already compressed JPEGs and about 92.8%
smaller than decoded RGB. This fixture measures storage, not throughput.

Independent grading read every output record: all 48 original JPEG byte sequences
remain in the chunks, decoded pixels match the originals, captions/labels/splits
match, and the outputs contain 36 training and 12 validation IDs without duplicates.
Each agent executed a two-worker training loader. The final agent's own checks
covered training batches and metadata; independent grading also checked every
pixel and the validation output.

The first revision still sent the agent into the long media cookbook. It guessed
the private `index.json` schema, hit a list/dictionary mismatch, and needed another
output directory. Other errors involved the working directory, Python shim and
BSD `find`. The final revision makes extra media references conditional and uses
`len(StreamingDataset(path))` for counts. Its agent read no reference files and
recorded no failed commands. General shell mistakes were not added to the skill.

### Pipeline evidence and trace findings

The original-skill producer used `mkdir` under `lightning_storage` without creating
a managed folder. It printed 100 records and a marker, but the separate consumer
failed with `FileNotFoundError`. Creating a managed folder before the second
submission made the producer's data downloadable. The agent stopped that recovery
while the pipeline still showed the consumer as not started; no summary was
produced. This is a failed task, not a successful storage handoff.

The revised skill creates the managed folder before submission, shows explicit
`prepare` → `consume` dependencies, captures the submitted run ID, and provides
CLI downloads and exact-ID cleanup. It explains that job completion can precede
pipeline completion. The retest completed on its first submission: two distinct
jobs finished, the consumer checked all 100 records/values, and the downloaded
summary and both logs contained the producer's runtime-generated marker. The
parent independently checked the completed API state and matching artifacts.

The revised agent still guessed an unsupported log-download option, attempted
to export consumer logs before upload, and found that recursive removal of a
managed folder left its files readable. It recovered by removing the three exact
files. The final cleanup reference now distinguishes removing a run subdirectory
from deleting the managed folder itself. This clarification follows the retest;
it does not claim an additional fresh-agent trial.

Both trials removed their pipeline records and jobs, and read back empty storage
prefixes. Two empty managed-folder registrations remain visible after accepted
DELETE requests. The local backend implementation queues managed-folder deletion
under storage retention; the production deletion deadline was not verified.
Registration removal remains pending, so this report does not claim every cloud
resource is gone. No test job is running and no test data remains readable.

The pipeline baseline did not meet the task requirements, so its larger tool/token
counts are diagnostic observations, not a successful-task speedup comparison.

## Measurement method and limits

Each primary task uses GPT-6 Luna with medium reasoning, a fresh context and its
own directory. The task prompt and input are unchanged across variants. Baseline
skills come from commit `3c90141`; each revision is frozen before its run. Agents
receive no development conversation or sibling outputs. These are isolated
contexts/workspaces, **not an OS-enforced sandbox**.

Metrics come from the actual session JSONL: final `token_count` totals, tool-call
records and timestamps. Tool calls count assistant-to-tool round trips; operations
also count nested calls in `functions.exec`. Communication with the parent is
excluded. Tokens accumulate across model calls, including repeated harness/context
input; they are not distinct context tokens. Cached and uncached input are shown
separately. [Machine-readable measurements](pipeline-litdata-metrics.json) retain
exact totals and skill snapshot hashes. Time includes model generation, commands and cloud waits. Parent
setup, independent grading and documentation work are outside agent measurements.

Environment: photo tasks used Python 3.14.0, LitData 0.2.76, PyTorch 2.14.1,
Pillow 12.3.0 and NumPy 2.5.4. Pipeline tasks used small CPU image jobs on
`lightning-baremetal` in `lightning-ai/general`, with no GPU or customer data.
The baseline agent upgraded the shared SDK environment before submission to
2026.10.13; the revised trial starts with that version already installed. This
setup difference prevents treating pipeline timing as a controlled speed comparison.

Detailed reproductions, reports, tool traces and API evidence stay in
`/tmp/lightning-skill-eval-20261014`. Public reports omit credentials and production
resource identifiers. Fresh-agent activation accuracy, compaction recovery,
advanced modalities and distributed/cloud LitData processing were not evaluated.

## Secondary coverage

The initial tests focused on checkpoint/resume and schedule replacement before
the user prioritized first-run data preparation and storage-based composition.

| Task / variant | Result | Calls / operations | Uncached input | Output tokens | Seconds |
|---|---|---:|---:|---:|---:|
| LitData resume, no skill | Passed | 10 / 16 | 38,242 | 11,920 | 344.71 |
| LitData resume, original skill | Passed | 11 / 19 | 37,053 | 12,302 | 334.92 |
| LitData resume, added recipe | Passed; recipe removed | 13 / 26 | 39,256 | 13,192 | 386.57 |
| Pipeline schedule, original | Replacement and updated immediate run passed; trigger test stopped after scope change; cleanup verified | — | — | — | — |
| Pipeline schedule, ID revision | Submission passed; scope changed before update; cleanup verified | — | — | — | — |

Resume checks preserved 64 records and compared a fresh-process continuation with
an uninterrupted shuffled run. The original skill did not reduce calls against
the no-skill control. The control's web search returned an incidental skill-reference
snippet, which it did not open or use. The added recipe increased effort and was
removed. Secondary trials used the parent model rather than Luna.

A scheduling tick passed while the immediate run was active with parallel runs
disabled, so no child appeared. Future trigger tests must wait for the immediate
run to finish. Initial GCP machine creation failed before workload execution;
primary storage comparisons consistently use the authorized Lightning Cloud.
Earlier production validation of SDK PR #201 separately verified a real scheduled
child on the updated version; the interrupted agent trials do not repeat that proof.

## Progress log (UTC, 2026-10-14)

- 13:34: launched initial resume/schedule cases with frozen original skills.
- 13:49: changed primary tasks to compact images and storage handoff; selected Luna.
- 13:51: original photo case passed; began revising first-use guidance.
- 13:55: first photo revision passed but used more tools/time; inspected the trace.
- 14:02: final photo revision passed with 10 calls and no failed commands.
- 14:04: original storage case ended without a completed consumer; compute/data cleanup verified.
- 14:04: launched revised storage case with managed-folder creation and handoff guidance.
- 14:10: revised pipeline completed; downloaded summary and both logs proved the handoff.
- 14:12: revised test jobs/pipeline/files removed; two empty registrations remain pending.

## Next checks

1. Verify eventual removal of the two empty managed-folder registrations; do not resubmit deletes.
2. Track current CI on [PR #60](https://github.com/Lightning-AI/skills/pull/60).
3. For stronger efficiency claims, repeat the same primary tasks with a warmed, fixed environment.

Completed local checks: skill format validation, README/plugin consistency, plugin
manifest validation, Python/bash snippet syntax, local link targets, eval JSON,
changed-file pre-commit hooks and whitespace checks. The fixture generator below
reproduces the 48 source images and manifest byte-for-byte. All 15 upstream LitData
references remain unchanged. Current entrypoints are 192 lines / 2,084 body tokens
for LitData and 225 lines / 2,833 body tokens for Pipelines (`o200k_base`). These
measure context size, not prompt-cache behavior.

## Repeat these tasks

Freeze each skill revision in a separate directory. Give a fresh agent the same
task from `evals.json`, the skill path, the prepared environment and an empty output
workspace. Keep prior conversations, reports and sibling outputs out of its context.
Use the same model/reasoning setting and runtime versions. For a no-skill control,
omit the skill path. Retain stdout/stderr, the runnable solution, API readbacks and
the agent transcript; grade artifacts independently before comparing efficiency.

Generate the photo input below with the Pillow/NumPy versions recorded above in
each task's input directory. Copy the same fixture between variants; do not
regenerate it with different library versions. Binary images are not committed.

```python
import json
from pathlib import Path

import numpy as np
from PIL import Image

Path("images").mkdir(exist_ok=True)
rows = []
y, x = np.mgrid[:256, :256]
for i in range(48):
    rng = np.random.default_rng(7000 + i)
    rgb = np.stack([(x + i * 11) % 256, (y * 2 + i * 7) % 256,
                    ((x + y) // 2 + i * 13) % 256], axis=-1)
    rgb = np.clip(rgb + rng.normal(0, 8, rgb.shape), 0, 255).astype(np.uint8)
    filename = f"images/photo-{i:03d}.jpg"
    Image.fromarray(rgb).save(filename, quality=83)
    rows.append({"id": i, "image": filename, "caption": f"Synthetic photo {i}",
                 "label": i % 4, "split": "train" if i < 36 else "validation"})
Path("manifest.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
```

Pipeline trials need explicit authorization for the selected teamspace. Constrain
them to disposable `skill-eval-storage-` resources, small CPU image jobs,
`shared_filesystem=False`, `max_runtime=180`, `max_run_attempts=1`, no schedules,
and at most two submissions for a recoverable setup error. Save resource IDs
immediately; download evidence before cleanup. A failed trial remains in the
report. Append later repetitions instead of replacing unfavorable measurements.
