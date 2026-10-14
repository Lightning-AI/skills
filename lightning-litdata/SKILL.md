---
name: lightning-litdata
description: Preprocess and stream training datasets with LitData, choose raw or optimized storage, configure media serialization, resume data loaders, and diagnose cloud I/O, caching and multi-worker failures. Use when the user works with litdata, StreamingDataset, StreamingRawDataset, StreamingDataLoader, optimize, map, dataset mixing, keyed lookup, or dataset throughput on Lightning Studios.
license: Apache-2.0
compatibility: Requires Python with uv or pip and litdata with PyTorch. Cloud data requires credentials for the selected storage; Lightning multi-node processing additionally requires lightning-sdk and an authorized teamspace.
---

# Lightning AI LitData

Turn source data into verified training batches or diagnose data-loading I/O.
Local processing needs no Lightning account. Private storage needs its own
credentials; Lightning multi-node processing also needs an authorized teamspace.

## Setup & version checks

Use the project's environment and retain its PyTorch/CUDA pins. For an isolated check:

```bash
uv venv /tmp/litdata-check
uv pip install --python /tmp/litdata-check/bin/python litdata torch
/tmp/litdata-check/bin/python -c "import litdata, torch; print(litdata.__version__, torch.__version__)"
```

The references track upstream development. Check installed APIs with `hasattr`
and `inspect.signature` before using newer features. Prefer supported installed
APIs; explain and pin any required unreleased dependency. Install only needed
extras, using the names declared by that package version.

## Choose the data path

Keep an existing supported format. For native files, start with
`StreamingRawDataset` when available. Use `optimize` for repeated training reads
or measured I/O bottlenecks. Read optimized data with `StreamingDataset` and
`StreamingDataLoader`.

| Task | Read only the relevant reference/section |
|---|---|
| Raw files, Parquet, HF or MDS | [Formats](references/using-litdata.md#10-format-specific-streaming) |
| Optimize or map | [Processing](references/processing.md) |
| Images, audio, video, tokens or graphs | [Media and serializers](references/using-litdata.md#3-images-media-types--serializers) |
| Resume, shuffle or mix datasets | [Streaming](references/streaming.md) |
| Stable keys and incremental updates | [Keyed lookup](references/keyed-lookup.md) |

The [cookbook](references/using-litdata.md) is long: locate the needed heading
before reading that section. Do not load all 15 reference files for one task.
Inspect the existing loader/config first. Resolve source, expected batch shape,
output destination and training setup; ask only for missing consequential choices.
Preserve labels, source ordering requirements and train/validation splits.

## Python API: optimize, stream, verify

Save as `litdata_smoke.py` and run with the environment's Python. This test writes
only temporary output and verifies values and batching:

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

Choose exactly one of `chunk_size` or `chunk_bytes`. Before scaling a conversion,
verify `index.json`, sample count, decoded labels/values and a small full loader
iteration. Use separate trial output, local cache and persistent dataset paths.
If validation fails, fix and rerun the small case before processing the full dataset.

## Example workflows

### Prepare images for training

Read [media serialization](references/using-litdata.md#3-images-media-types--serializers).
Prefer supported compressed image wrappers/serializers over decoded PIL pixels.
Keep captions as text and labels with each sample. Select wrappers by modality;
a path string does not always serialize file contents. Audio/video decoders may
need a custom collator. Verify dimensions, labels, dtype and batch structure.

### Stream a bucket on a Studio

Read [path resolution](references/resolver.md); load [Studio integration](references/lightning-studio.md)
for teamspace mounts. Verify teamspace and connection. Pass supported URLs or
connection paths to LitData so its resolver reads object storage directly.
Size a writable local cache for free space, workers and chunks. Check batches
with one worker, then the intended count. On auth failure, inspect credential
scope and connection selection without printing tokens.

### Resume training or mix datasets

Read [resume](references/using-litdata.md#5-shuffle-seed-drop_last-resume) or
[composition](references/using-litdata.md#11-combine--parallel--cycle) as needed.
Save/restore `StreamingDataLoader.state_dict()` with model/optimizer state;
a seed alone does not restore a mid-epoch position. Verify sample IDs around the
checkpoint and installed support for worker/world-size changes. Use weighted
`CombinedStreamingDataset` for sampling mixtures; `ParallelStreamingDataset`
for aligned sampling.

### Diagnose a hang or throughput regression

Start with [debugging](references/debugging.md). Read [cache lifecycle](references/cache-and-chunk-lifecycle.md)
for prefetch/eviction problems or [benchmarking](references/benchmarking.md) for
measurements. Record versions, source/cache paths, workers, cache limit and first
traceback. Compare `num_workers=0` with the intended count before increasing timeouts.
Separate cold downloads, warm cache, decoding, collation and model compute.

## Advanced references

| Trigger | Read |
|---|---|
| Remote optimize/map I/O, queues or fork failures | [Data movement](references/data-movement.md) |
| Lightning multi-node processing | [Multi-node](references/multi-node.md) |
| Need an exact tuning knob | [Environment variables](references/env-vars.md) |
| Chunk layout, Cache, reader/writer or serializer internals | [Storage format](references/storage-format.md) |
| Editing LitData source or tests | [Contributing](references/contributing.md), [testing](references/testing.md) |

`optimize(num_nodes=...)` submits Lightning jobs; it is not torchrun. Resolve the
authorized teamspace/cloud/machines, use durable shared outputs, and verify every
worker plus the merged index. Local serialization tests need no cluster.

## Gotchas

- Define worker functions at module scope and use the multiprocessing `__main__` guard.
- Cache cleanup must not delete durable source chunks or another run's output.
- For class-sorted sources needing full mixing, shuffle inputs with a fixed seed
  before optimizing. Chunk/intra-chunk shuffle does not ensure file-level mixing.
- A late missing-chunk error can follow a crashed prefetch worker. Inspect worker
  stderr; parent initialization of a non-fork-safe storage client can break only multi-worker runs.
- Manual reads through Studio FUSE mounts do not measure LitData's direct-storage path.
- Upstream throughput figures describe specific workloads. Measure representative data.

## Verify and report

Report changed code/configuration, dataset/output location, versions and actual
validation. A preprocessing test must verify decoded batches; remote reads must
use the intended workers; resume must verify sample continuity. Distinguish local
validation from cloud or distributed execution.

For performance claims, compare the same workload/cache conditions across repeated
runs. Report throughput, sample size, workers and cache state. For a long task or
handoff, retain source/output/cache paths, versions, checkpoint/job IDs and the
last verified result; inspect those artifacts before resuming or overwriting output.

## Reference provenance and maintenance

The 15 files in `references/` are copied without content changes from
[Lightning-AI/litData's curated skill](https://github.com/Lightning-AI/litData/tree/c6f075ca7448ede4c383122678ee790e359d3711/.claude/skills/litdata/reference)
at commit `c6f075ca7448ede4c383122678ee790e359d3711` (Apache-2.0). Their `src/`, `tests/`,
`examples/` and repository README pointers refer to **the LitData repository**,
not this skills checkout. Clone it only when the task requires source work;
otherwise use the pinned GitHub tree. Internal line numbers and tuning defaults
are version-specific. When refreshing these references, compare against the
new upstream commit and re-run the local smoke test and affected workflows.
