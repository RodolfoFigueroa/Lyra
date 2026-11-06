---
title: Configure computation caching
description: Configure shared artifact storage, refresh cached calculations, and prune unused results.
contentType: How-to
contentPlan: operate/caching
sidebar: { label: "Configure Computation Caching" }
---

Configure shared storage for the application programming interface (API) and workers, then choose how to refresh and remove cached results. [Artifact bundles](../../concepts/caching/#store-and-validate-cached-results) contain native output files that both computation lookups and retained jobs can reference.

## Cache metric tables

The server always uses filesystem artifacts for computation caching and successful job outputs. `artifact_dir` defaults to `LYRA_DATA_DIR/artifacts`. To use another path, add `artifact_dir` before any section in your [configuration file](../configuration/):

```toml
artifact_dir = "/srv/lyra/artifacts"
```

Use an absolute path shared by the API and all workers on one host. Keep it separate from job scratch directories. The example deployment shares this path through its data volume. A local runner uses persistent caching only when you pass `artifact_dir`.

Workers sharing a directory must use equivalent code, plugin contracts, and resource settings. Keep source data unchanged while reusing cached results, or update its declared revision. Use [offline pruning](#reclaim-artifact-storage) to reclaim storage while preserving retained results.

If a cache read, lock, or lookup write fails, Lyra logs a warning and may recalculate. A job succeeds only after Lyra saves a readable output artifact. See [caching across executions](../../concepts/caching/) for dependency behavior and concurrency limits.

## Refresh cached results

Maintain declared location revisions for changes within their documented dataset coverage. Set or change the run-wide `cache_revision` for these cases:

- External changes outside declared coverage
- Changes to unversioned datasets or implementations
- Deliberate recomputation

Lyra includes the run-wide revision in each node’s computation identity alongside its declared location revisions. Changing this value does not delete existing cache entries. Choose a string of at most 256 characters with at least one non-whitespace character. Lyra preserves the exact value, including whitespace.

Pass the revision separately from metric inputs when running locally:

```python
runner.run("metric_name", inputs, cache_revision="dataset-2026-10")
```

With the Python client, supply the revision when submitting a job:

```python
client.raw.submit(
    "metric_name", inputs, cache_revision="dataset-2026-10"
)
```

Hypertext Transfer Protocol (HTTP) and Model Context Protocol (MCP) job requests accept `cache_revision` alongside `metric` and `input`. Lyra records the revision in job provenance; it does not change handler parameters or location content fingerprints.

Omitting the revision or repeating a value permits reuse when the other cache inputs match. Changing the revision selects a separate cache namespace and keeps older artifacts available. Return to a previous revision to reuse its valid cached results.

When Lyra accepts a request, it records the declared location revisions. Providers must serve those revisions or fail. This record neither detects unreported changes nor creates data snapshots. See [revision coverage](../../plugins/location-providers/#define-revision-coverage) before changing source declarations.

## Reclaim artifact storage

Prune unused artifacts with the API and all workers stopped, while keeping Redis available. The command checks retained jobs across all queues, including queues you no longer configure. Use the server’s configuration and mounted data volume.

Preview cleanup counts and storage savings with `--dry-run`:

```sh
uv run python -m lyra_app.artifacts prune \
  --config /lyra_config/lyra.toml --dry-run
```

The dry run reports counts and bytes without deleting anything. After reviewing the selection, remove `--dry-run` to delete the selected files:

```sh
uv run python -m lyra_app.artifacts prune --config /lyra_config/lyra.toml
```

Pruning preserves bundles that computation lookups or retained jobs reference. It removes unreferenced bundles and abandoned temporary writes. If the command cannot read a retained reference, it stops before deleting files.

Add `--clear-cache` to remove computation lookups and reclaim their unreferenced bundles. The command preserves outputs that retained jobs still reference. Redis retention expiry does not delete files, and computation lookups can retain artifacts after jobs expire. Schedule offline pruning to reclaim this space; Lyra does not remove unused artifacts automatically.
