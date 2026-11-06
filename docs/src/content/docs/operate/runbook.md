---
title: Monitor workers, queues, and logs
description: Check service health, worker coverage, retained jobs, and operational diagnostics.
contentType: How-to
contentPlan: operate/runbook
sidebar: { label: "Monitor Workers, Queues, And Logs" }
---

Check service health first, then confirm that workers serve the queues receiving work. Use retained job details and logs to investigate failures. The [administrative command-line interface (CLI)](../admin-cli/) reports a snapshot each time you run a command.

## Check liveness and readiness

Use liveness to check the application programming interface (API) process. Use readiness to check whether it can accept traffic:

| Check | Meaning | Typical use |
| --- | --- | --- |
| `GET /live` | The API process is alive | Process restart decisions |
| `GET /ready` | Redis is available | Admission of new traffic |

Readiness returns 503 when Redis is unavailable. Check workers separately to confirm that they can execute the published metrics.

## Inspect workers and queues

Configure your connection using the [CLI guide](../admin-cli/), then compare observed workers with the queues assigned to metrics:

```sh
uv run lyra-admin health
uv run lyra-admin workers list
uv run lyra-admin queues list
uv run lyra-admin routing list
```

Worker observations identify each process, its queues, and its current job. They also report heartbeat age: the time since its last check-in. The CLI lists configured worker pools separately from observed processes.

Check that each metric’s assigned queue has an observed worker. If Redis is unavailable, restore its connection before interpreting queue or worker observations.

## Inspect retained jobs

List one queue and status at a time:

```sh
uv run lyra-admin jobs list --queue interactive --status running --limit 20
uv run lyra-admin jobs get your_job_id_here
```

Use `offset` and `limit` to page through jobs. The default limit is 50; the maximum is 200. Each view covers one queue and status, with this ordering:

- Queued jobs: first-in, first-out (FIFO)
- Running jobs: registry order
- Completed jobs: descending registry order

Transitions and stale entries can shorten a page. These views have no global chronological order or metric filter.

Administrator job details include queue, worker identity, and fuller failure diagnostics. Lyra removes sensitive details from public errors. If it cannot save structured error metadata, it returns a generic public message.

## Read structured logs

Lyra writes one JavaScript Object Notation (JSON) object per line to standard output or `logging.file`. Each record identifies its logger and includes a message, severity level, and Coordinated Universal Time (UTC) timestamp. Plugin logs also identify the job and node.

Lyra logs lifecycle transitions independently of progress. Unexpected exceptions include full tracebacks in logs; public results contain concise errors. Inspect the failing node and dependency path for a graph failure.

## Understand retention and cleanup

Check these settings when investigating a timeout or missing result:

| Job state | Limit |
| --- | --- |
| Queued | No queue-age expiration |
| Running | `jobs.execution_timeout_seconds`, default 18000s (five hours) |
| Succeeded or failed | `jobs.result_retention_seconds`, default 86400s (one day) |

Retention controls Redis records and API access. Use [offline pruning](../caching/#reclaim-artifact-storage) to remove output artifacts that no jobs or computation lookups reference. Arrange separate cleanup for temporary scratch files. Lyra does not delete unused files automatically.

## Respond to a worker interruption

Redis Queue (RQ) supervises worker processes and execution children. Workers use these intervals:

- Worker time to live (TTL): 30s
- Execution checks: every 5s
- Maintenance: every 15s

A worker serving the queue should detect abandoned jobs within approximately two minutes when it can perform maintenance. Outages and worker saturation can delay detection. API reads do not repair job records.

Lyra does not replay jobs automatically after failure. Read [the lifecycle explanation](../../concepts/lifecycle/) and [troubleshooting](../troubleshooting/) before deciding to submit new work.

## Refresh cached results

Maintain declared dataset revisions and choose a run-wide `cache_revision` for uncovered changes. Follow [computation cache administration](../caching/) for configuration, invalidation, and cleanup.
