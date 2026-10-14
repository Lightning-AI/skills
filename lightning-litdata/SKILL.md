---
name: lightning-litdata
description: Preprocess and stream training datasets with LitData, choose raw or optimized storage, configure media serialization, resume data loaders, and diagnose cloud I/O, caching and multi-worker failures. Use when the user works with litdata, StreamingDataset, StreamingRawDataset, StreamingDataLoader, optimize, map, dataset mixing, keyed lookup, or dataset throughput on Lightning Studios.
license: Apache-2.0
compatibility: Requires Python with uv or pip and litdata with PyTorch. Cloud data requires credentials for the selected storage; Lightning multi-node processing additionally requires lightning-sdk and an authorized teamspace.
---

# Lightning AI LitData

Use LitData to turn source data into validated training batches, stream existing
datasets, or diagnose the I/O that limits training. Local processing needs no
Lightning account. Private cloud storage needs credentials for that storage;
Lightning multi-node processing also needs an authorized teamspace.

Start with the task table below. Read the relevant section of
[the usage cookbook](references/using-litdata.md) when choosing an API or writing
an example; load specialized references only when needed. The 15 bundled guides
cover advanced workflows without requiring a LitData checkout.

## Setup & version checks

Use the project's existing environment and dependency manager. For a disposable
local verification environment:

```bash
uv venv /tmp/litdata-check
uv pip install --python /tmp/litdata-check/bin/python litdata torch
/tmp/litdata-check/bin/python -c "import litdata, torch; print(litdata.__version__, torch.__version__)"
```

Do not replace a project's PyTorch/CUDA pins just to run an example. Install
optional storage, image, audio or video dependencies only when that task requires
them. Verify extras against the installed package metadata; older documentation
uses both `extra` and `extras`; use only the extra declared by that version.

These references are a snapshot of upstream development, not proof that every
API exists in the latest PyPI wheel. Before using newer features, inspect the
installed API (for example `hasattr(litdata, "StreamingRawDataset")` and
`inspect.signature(litdata.optimize)`). Prefer the stable installed equivalent;
if the task requires an unreleased feature, explain the version requirement
before changing the dependency and pin the selected commit. Do not silently
upgrade a training environment to upstream main.

## Choose the data path

| Goal | Start with | Detail |
|---|---|---|
| Train from existing native files without preprocessing | `StreamingRawDataset` plus PyTorch `DataLoader`, when available | [Usage §10](references/using-litdata.md#10-format-specific-streaming) |
| Repeated epochs with optimized I/O | `optimize` → `StreamingDataset` → `StreamingDataLoader` | [Usage](references/using-litdata.md), [processing](references/processing.md) |
| Transform files with side effects | `map` | [Processing](references/processing.md) |
| Tokens, images, audio, video, graphs | Correct typed wrapper/serializer and collator | [Usage](references/using-litdata.md) |
| Parquet, Hugging Face, or MDS | Existing format reader/indexer rather than unconditional re-encoding | [Usage §10](references/using-litdata.md#10-format-specific-streaming) |
| Mix datasets, exact resume, distributed shuffling | Streaming loader and dataset composition | [Streaming](references/streaming.md) |
| Stable sample keys, incremental updates | Key index and keyed access | [Keyed lookup](references/keyed-lookup.md) |

Inspect the existing data loader and configuration first. Establish the source
format/location, expected batch structure, output destination and training setup.
Ask only for missing choices that affect correctness or placement. Preserve labels,
ordering requirements and train/validation splits; use a separate output directory
for a trial conversion.

Finish the requested workflow, not just the example: implement the data path,
validate representative samples and batches, then report the artifact location
and measured result. Scale preprocessing only after that small check passes.

## Python API: optimize, stream, verify

Save the following as `litdata_smoke.py` and run it with the environment’s Python.
The `__main__` guard is required for multiprocessing. The test writes to its own
temporary directory, checks sample contents and batching, and cleans up afterward:

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from litdata import optimize, StreamingDataset, StreamingDataLoader


def prepare(i):
    return {"index": i, "value": i * 2}


def main():
    with TemporaryDirectory(prefix="litdata-smoke-") as tmp:
        output = str(Path(tmp) / "optimized")
        optimize(fn=prepare, inputs=list(range(12)), output_dir=output,
                 num_workers=1, chunk_size=4)
        dataset = StreamingDataset(input_dir=output, shuffle=False)
        assert len(dataset) == 12
        assert [int(dataset[i]["value"]) for i in range(12)] == list(range(0, 24, 2))
        loader = StreamingDataLoader(dataset, batch_size=3, num_workers=0)
        assert sum(len(batch["index"]) for batch in loader) == 12
        print("PASS: 12 samples, values and batches verified")


if __name__ == "__main__":
    main()
```

For real preprocessing, choose exactly one of `chunk_size` or `chunk_bytes`.
Start with a representative small subset, inspect decoded samples and labels,
then scale workers and data volume. Keep temporary/cache paths separate from
persistent outputs. Verify `index.json`, sample count and a full small loader
iteration; success of the preprocessing process alone does not validate training I/O.

## Example workflows

### "Prepare my images for training"

Read [media types and serializers](references/using-litdata.md#3-images-media-types--serializers). Prefer
compressed image serialization (`Image`/`Jpeg` when available, or the installed
version's supported JPEG serializer) over returning decoded PIL pixels, which
can inflate storage. Keep captions as text and labels alongside images. A
filepath string is not universally a media payload: select the appropriate
wrapper for the modality. Audio/video decoder objects may require a custom
collator; default tensor stacking is not a safe assumption.

Before the full conversion, decode a handful of examples and check dimensions,
labels, dtype and batch structure. If the source is class-sorted, shuffle the
input list with a fixed seed before optimize when full source mixing is required.
Chunk-level and intra-chunk shuffling alone do not guarantee file-level mixing.

### "Stream my bucket on a Lightning Studio"

Use [path resolution](references/resolver.md) and [Studio integration](references/lightning-studio.md).
Confirm the requested owner/teamspace and connection; do not infer it from the
first credential or membership. Pass the original supported cloud URL or Studio
connection path into LitData so its resolver can reach object storage directly.
Do not benchmark a manual read through the FUSE mount and call it LitData speed.

Use a writable local cache with an explicit budget appropriate to free space,
workers and chunk sizes. Validate a few samples with one worker, then the intended
worker count. If auth fails, inspect the selected connection and credential
scope; do not print tokens or create new permissions as a workaround.

### "Resume training / mix several datasets"

Read [shuffle and resume](references/using-litdata.md#5-shuffle-seed-drop_last-resume)
and [dataset composition](references/using-litdata.md#11-combine--parallel--cycle);
use [streaming internals](references/streaming.md) when diagnosing behavior.
Save and restore the `StreamingDataLoader.state_dict()`
alongside model/optimizer state; rebuilding the loader with a seed alone is not
mid-epoch resume. Validate sample IDs around a checkpoint and check worker/world
size support in the installed version. Choose weighted `CombinedStreamingDataset`
versus aligned `ParallelStreamingDataset` from the requested sampling semantics,
not merely because both accept several datasets.

### "My data loader hangs or is slow"

Start with [debugging](references/debugging.md). Use [cache lifecycle](references/cache-and-chunk-lifecycle.md)
for eviction or prefetch problems and [benchmarking](references/benchmarking.md)
for throughput measurements. Capture version, source/cache
paths, worker count, cache limit, first traceback and the smallest failing batch.
Compare `num_workers=0` with the intended setting before increasing timeouts.
A late `FileNotFoundError` may be a failed prefetch worker, not a missing object.
Separate cold download, warm cache, decode, collation and model compute. Report
sample size, worker count, cache state and repetitions with throughput results.

## Advanced references

| Need | Read |
|---|---|
| Remote optimize/map downloads and uploads, queues, fork behavior | [Data movement](references/data-movement.md) |
| Lightning multi-node optimize/map orchestration and merged indexes | [Multi-node](references/multi-node.md) |
| Exact env knob names and scope | [Environment variables](references/env-vars.md) |
| Binary chunk layout, Cache, readers/writers and serializers | [Storage format](references/storage-format.md) |
| Changing LitData implementation or adding a regression test | [Contributing](references/contributing.md), [testing](references/testing.md) |

Multi-node `optimize(num_nodes=...)` submits Lightning jobs; it is not equivalent
to launching the same script with torchrun. Choose the authorized teamspace,
cloud and machines, use durable shared outputs, and inspect every worker's
outcome plus the merged index. Do not launch a cluster merely to test a local
serialization change.

## Verify and report

| Task | Verify before calling it complete |
|---|---|
| Preprocess a dataset | Output index and sample count; decoded values, labels and batch structure |
| Read remote data | Representative batches with the intended worker count; source and cache paths are correct |
| Resume training | Sample IDs around the checkpoint show the intended continuation |
| Improve performance | Same workload and cache conditions before/after; report repeated measurements |
| Run multi-node processing | Every worker completes and the final merged index can be read |

Return the code or configuration changed, dataset/output location, package
versions, validation result and any remaining limitation. For benchmarks, give
samples/sec or bytes/sec with sample size, workers and cache state. Distinguish
local smoke-test success from verified cloud or distributed behavior.

## Gotchas

- Keep the multiprocessing guard; define worker functions at module scope.
- Do not confuse a local cache with the durable dataset. Cleaning a cache must
  not delete source chunks or an output directory shared by another run.
- Preserve sample ordering deliberately: a fixed seed does not repair a
  class-sorted source that was chunked without the intended mixing.
- Parent-process initialization of a non-fork-safe storage client can break
  only multi-worker runs. Inspect worker stderr and the installed version's
  downloader path before treating the eventual missing-chunk error as root cause.
- Performance figures in upstream references are measurements of particular
  workloads, not guaranteed speedups. Reproduce with representative data.

## Reference provenance and maintenance

The 15 files in `references/` are copied without content changes from
[Lightning-AI/litData's curated skill](https://github.com/Lightning-AI/litData/tree/c6f075ca7448ede4c383122678ee790e359d3711/.claude/skills/litdata/reference)
at commit `c6f075ca7448ede4c383122678ee790e359d3711` (Apache-2.0). Their `src/`, `tests/`,
`examples/` and repository README pointers refer to **the LitData repository**,
not this skills checkout. Clone it only when the task requires source work;
otherwise use the pinned GitHub tree. Internal line numbers and tuning defaults
are version-specific. When refreshing these references, compare against the
new upstream commit and re-run the local smoke test and affected workflows.
