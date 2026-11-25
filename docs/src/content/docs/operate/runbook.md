---
title: Monitor workers and the job queue
description: Check whether workers can take jobs and use job details and logs to investigate failures.
contentType: How-to
contentPlan: operate/runbook
sidebar: { label: "Monitor Workers and the Job Queue" }
---

Use the [administrative command-line interface (CLI)](../admin-cli/) to check API health and see which workers can take jobs. If a job fails, use its ID to look up the failure and find the corresponding worker logs.

## Check liveness and readiness

Use liveness to check the API process. Use readiness to check whether it can accept traffic:

| Check | Meaning | Typical use |
| --- | --- | --- |
| `GET /live` | The API process is alive | Process restart decisions |
| `GET /ready` | SQLite is readable | Admission of new traffic |

Readiness returns HTTP 200 with `degraded` when Redis is unavailable and HTTP 503 when SQLite is unreadable. Completed jobs remain accessible during a Redis outage. Check workers separately to confirm that they can execute the published metrics.

## Inspect workers and the queue

[Connect the CLI to your server](../admin-cli/#connect-to-the-server). Check Redis health, then list the workers and count the waiting jobs.

```sh
uv run lyra-admin health
uv run lyra-admin workers list
uv run lyra-admin --json queue show
```

In `workers list`, check each worker’s current job and heartbeat age (time since its last check-in). Compare the number of workers with the process count in `lyra-admin config-summary`.

In `queue show`, `pending_depth` counts waiting jobs. If jobs are waiting and `observed_workers` is empty, check the worker service’s startup logs. If the health check reports Redis is unavailable, restore the connection first.

## Inspect retained jobs

List up to 20 running jobs. To inspect one, replace `your_job_id_here` with an ID from the list.

```sh
uv run lyra-admin jobs list --status running --limit 20
uv run lyra-admin jobs get your_job_id_here
```

Use `--offset` and `--limit` to page through jobs with the same status. The default page size is 50 jobs and the maximum is 200. The order depends on the status.

- Queued jobs: first-in, first-out (FIFO)
- Running jobs: registry order
- Completed jobs: newest completion first, then descending job ID

Transitions and stale entries can shorten a page. These views have no global chronological order or metric filter.

Administrator job details include queue, worker identity, and fuller failure diagnostics. Lyra removes sensitive details from public errors. If it cannot save structured error metadata, it returns a generic public message.

## Read structured logs

Lyra writes one JSON object per line to standard output or `logging.file`. Each record identifies its logger and includes a message, severity level, and UTC timestamp. Plugin logs also identify the job and node.

Lyra logs lifecycle transitions independently of progress. Unexpected exceptions include full tracebacks in logs; public results contain concise errors. Inspect the failing node and dependency path for a graph failure.

## Understand retention and cleanup

Check these settings when investigating a timeout or missing result:

| Job state | Limit |
| --- | --- |
| Queued | No queue-age expiration |
| Running | `jobs.execution_timeout_seconds`, default 18000s (five hours) |
| RQ records and admin tracebacks | `jobs.rq_retention_seconds`, default 86400s (one day) |

SQLite retains terminal status, structured failures, and [stored results](../caching/#preserve-stored-results) indefinitely. Preserve the shared file across deployments. Arrange separate cleanup for temporary scratch files.

## Respond to a worker interruption

Redis Queue (RQ) supervises worker processes and execution children. Workers use these intervals:

- Worker time to live (TTL): 30s
- Execution checks: every 5s
- Maintenance: every 15s

A worker serving the queue should detect abandoned jobs within approximately two minutes when it can perform maintenance. Outages and worker saturation can delay detection. API reads do not repair job records.

Lyra does not replay jobs automatically after failure. Read [the lifecycle explanation](../../concepts/lifecycle/) and [troubleshooting](../troubleshooting/) before deciding to submit new work.

## Refresh cached results

Update implementation and origin revisions when code or data changes affect outputs. Follow [result-store configuration](../caching/) for the shared path and indefinite retention policy.
