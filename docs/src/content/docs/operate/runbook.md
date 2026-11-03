---
title: Monitor workers, queues, and logs
description: Check service health, worker coverage, retained jobs, and operational diagnostics.
contentType: How-to
contentPlan: operate/runbook
sidebar: { label: "Monitor Workers, Queues, And Logs" }
---

Start an operational check with service health, then compare the queues receiving work with the workers available to consume them. The [administrative command-line interface (CLI)](../admin-cli/) reports a snapshot each time you run a command.

## Check liveness and readiness

Use liveness to check the API process and readiness to check whether it can accept traffic:

| Check | Meaning | Typical use |
| --- | --- | --- |
| `GET /live` | The API process is alive | Process restart decisions |
| `GET /ready` | Redis is available | Admission of new traffic |

Readiness returns 503 when Redis is unavailable. Check workers separately to confirm that they can execute the published metrics.

## Inspect workers and queues

With the CLI connection details configured, inspect observed workers and compare their queues with effective routing:

```sh
uv run lyra-admin health
uv run lyra-admin workers list
uv run lyra-admin queues list
uv run lyra-admin routing list
```

Set the host, transport, and administrator key as described in the CLI guide. Worker observations include IDs, hostnames, queues, current jobs, and heartbeat age. The CLI reports configured pools separately from observed processes.

Check that each metric’s assigned queue has an observed worker. If Redis is unavailable, restore its connection before interpreting queue or worker observations.

## Inspect retained jobs

List one queue and status at a time:

```sh
uv run lyra-admin jobs list --queue interactive --status running --limit 20
uv run lyra-admin jobs get your_job_id_here
```

The API accepts `offset` and `limit`, with a default limit of 50 and a maximum of 200. Queued jobs use first-in, first-out (FIFO) order, running jobs use registry order, and terminal jobs use descending registry order. Transitions and stale entries can shorten a page. These are queue/status views, without a global chronological or metric filter.

Administrator job details include queue, worker identity, and fuller failure diagnostics. Lyra removes sensitive details from public errors. If it cannot save structured error metadata, it returns a generic public message.

## Read structured logs

Lyra writes one JSON object per line to standard output or `logging.file`. Records contain a Coordinated Universal Time (UTC) timestamp, level, logger, and message. Plugin logging also includes the job ID and node.

Lyra logs lifecycle transitions independently of progress. Unexpected exceptions include full tracebacks in logs; public results contain concise errors. Inspect the failing node and dependency path for a graph failure.

## Understand retention and cleanup

Queued jobs have no queue-age expiration. Running jobs use `jobs.execution_timeout_seconds`, default 18000 seconds. Completed successes and failures remain for `jobs.result_retention_seconds`, default 86400 seconds.

Retention controls Redis records and API access. Scratch files can remain on disk. Arrange operational cleanup for them; the application does not run a filesystem sweeper.

## Respond to a worker interruption

Redis Queue (RQ) supervises worker processes and execution children. Workers use a time to live (TTL) of 30s, check execution every 5s, and perform maintenance every 15s.

A worker serving the queue should detect abandoned jobs within approximately two minutes when it can perform maintenance. Outages and worker saturation can delay detection. API reads do not repair job records.

Lyra does not replay jobs automatically after failure. Read [the lifecycle explanation](../../concepts/lifecycle/) and [troubleshooting](../troubleshooting/) before deciding to submit new work.

## Refresh cached results

Maintain declared dataset revisions and choose a run-wide `cache_revision` for uncovered changes. Follow [computation cache administration](../caching/) for configuration, invalidation, and cleanup.
