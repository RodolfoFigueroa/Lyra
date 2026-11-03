---
title: Use runtime services
description: Access logging, scratch storage, progress reporting, and data acquisition services.
contentType: How-to
contentPlan: plugins/runtime
sidebar: { label: "Use Runtime Services" }
---

Use `RunContext` for logging, scratch storage, and progress reporting. A resource groups file or service operations with their configuration and credentials. Request one when your metric needs database queries, file reads, or downloads. Lyra initializes each requested resource before invoking the node and closes it when the job ends.

Declare `context: RunContext` to access these services:

| Service | Use it to |
| --- | --- |
| `job_id` and `node` | Identify the current job and metric |
| `logger` | Log diagnostics |
| `temp_dir` | Store scratch files |
| `report_progress(...)` | Report progress |

## Log diagnostics and use scratch storage

Use `context.logger` for diagnostics. It supports ordinary Python logging arguments and attaches the job ID and node name to worker log records.

Use `context.temp_dir` for internal scratch files. Return native tables, with all needed data available in memory. Server scratch files can outlive result retention; operators manage their cleanup. The default local runner removes scratch storage before returning.

## Report progress

Call `context.report_progress(...)` to report the current stage and completed work:

```python
context.report_progress(
    stage="areas",
    current=10,
    total=100,
    unit="areas",
    message="Processed the first ten areas.",
)
```

`current` must be finite and nonnegative. A supplied `total` must be finite and positive, and `current` cannot exceed it.

Progress is an estimate: stages, totals, and units may change, and estimates may decrease. Lyra combines frequent updates using `jobs.progress_min_interval_ms` (default 1000 ms). Persistence failures do not fail the calculation.

Keep progress optional. Clients use lifecycle status to detect completion. Progress units are human-readable labels, separate from output column units.

## Acquire supporting data

Use [data resources](../resources/) for file or service clients, configuration, credentials, and setup/cleanup. [Earth Engine](../earth-engine/) has a dedicated integration recipe.

## Register locations

A [location provider](../location-providers/) resolves exact IDs; [location operations](../location-operations/) derive inputs inside a graph. Follow those guides for their contracts and registration.

## Define an I/O resource

Follow [Define data resources](../resources/) for the complete file reader and lifecycle contract.

## Manage resource clients and credentials

Resources own their clients and credentials. Keep acquisition inside their managed lifecycle, as described in [resource lifecycle](../resources/#manage-resource-clients-and-credentials).
