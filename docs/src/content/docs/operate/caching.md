---
title: Configure computation caching
description: Enable persistent caches and refresh results through declared and run-wide revisions.
contentType: How-to
contentPlan: operate/caching
sidebar: { label: "Configure Computation Caching" }
---

Configure shared cache storage and a revision policy for the workers using it. Keep computation artifacts separate from retained job records.

## Cache metric tables

Set `cache_dir` to reuse validated metric tables and operation locations across jobs. Add this top-level setting before any TOML section:

```toml
cache_dir = "/lyra_data/cache/metrics"
```

Use an absolute path that participating workers share on one host. Keep it separate from job scratch directories. Omitting `cache_dir` disables persistent caching.

Workers sharing a directory must use equivalent code, plugin contracts, and resource settings. Keep source data unchanged while reusing cached results, and manage invalidation and cleanup through your operational workflow. Stop all processes using a directory before removing it.

Cache storage errors produce warnings and may cause recalculation. See [reuse across executions](../../concepts/graphs/#reuse-tables-across-executions) for dependency behavior and concurrency limits.

## Refresh cached results

Maintain declared location revisions for changes within their documented dataset coverage. Set or change the run-wide `cache_revision` for these cases:

- External changes outside declared coverage
- Changes to unversioned datasets or implementations
- Deliberate recomputation

The run-wide revision adds to location revisions and computation identity without purging the server cache. Choose a string of at most 256 characters with at least one non-whitespace character. Lyra preserves the exact value, including whitespace.

Pass the revision separately from metric inputs when running locally:

```python
runner.run("metric_name", inputs, cache_revision="dataset-2026-10")
```

With the Python client, supply the revision when submitting a job:

```python
client.raw.submit("metric_name", inputs, cache_revision="dataset-2026-10")
```

HTTP and Model Context Protocol (MCP) job requests accept `cache_revision` alongside `metric` and `input`. Lyra records the revision in job provenance; it does not change handler parameters or location content fingerprints.

Omitting the revision or repeating a value permits reuse when the other cache inputs match. Changing the revision selects a separate cache namespace and keeps older artifacts available. Return to a previous revision to reuse its valid cached results.

Acceptance pins declared location revisions. Providers must serve those revisions or fail; pinning neither detects unreported changes nor creates snapshots. See [revision coverage](../../plugins/location-providers/#define-revision-coverage) before changing source declarations.
