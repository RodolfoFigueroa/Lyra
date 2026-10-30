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
| `GET /ready` | Redis and PostGIS are available | Admission of new traffic |

Readiness checks Redis and PostGIS concurrently. It returns 503 if either dependency is unavailable. It does not prove that every worker can execute every published metric.

## Inspect workers and queues

With the CLI connection details configured, inspect observed workers and compare their queues with effective routing:

```sh
uv run lyra-admin health
uv run lyra-admin workers list
uv run lyra-admin queues list
uv run lyra-admin routing list
```

Set the host, transport, and administrator key as described in the CLI guide. Worker observations include IDs, hostnames, queues, current jobs, and heartbeat age. Configured pools are reported separately from observed processes.

Check that each metric’s assigned queue has an observed worker. Redis errors are reported as unavailable; they are not an empty queue or proof that a worker is offline.

## Inspect retained jobs

List one queue and status at a time:

```sh
uv run lyra-admin jobs list --queue interactive --status running --limit 20
uv run lyra-admin jobs get your_job_id_here
```

The API accepts `offset` and `limit`, with a default limit of 50 and a maximum of 200. Queued jobs use first-in, first-out (FIFO) order, running jobs use registry order, and terminal jobs use descending registry order. Transitions and stale entries can shorten a page. These are queue/status views, without a global chronological or metric filter.

Administrator job details include queue, worker identity, and fuller failure diagnostics. Public errors are sanitized. If structured error metadata could not be saved, the public message uses a generic fallback.

## Read structured logs

Lyra writes one JSON object per line to standard output or `logging.file`. Records contain a Coordinated Universal Time (UTC) timestamp, level, logger, and message. Plugin logging also includes the job ID and node.

Lifecycle transitions are logged independently of progress. Unexpected exceptions include full tracebacks in logs; public results contain concise errors. Inspect the failing node and dependency path for a graph failure.

## Understand retention and cleanup

Queued jobs have no queue-age expiration. Running jobs use `jobs.execution_timeout_seconds`, default 18000 seconds. Completed successes and failures remain for `jobs.result_retention_seconds`, default 86400 seconds.

Retention controls Redis records and API access. Scratch files can remain on disk. Arrange operational cleanup for them; the application does not run a filesystem sweeper.

## Respond to a worker interruption

Redis Queue (RQ) supervises worker processes and execution children. Workers use a time to live (TTL) of 30s, check execution every 5s, and perform maintenance every 15s.

Abandoned jobs should be detected within approximately two minutes when a worker serving the queue can perform maintenance. Outages and worker saturation can delay detection. API reads do not repair job records.

Jobs are not replayed automatically after failure. Read [the lifecycle explanation](../../concepts/lifecycle/) and [troubleshooting](../troubleshooting/) before deciding to submit new work.

## Refresh cached results

Set or change `cache_revision` when external data or an unversioned implementation changes. Choose a string of at most 256 characters containing at least one non-whitespace character. Lyra preserves the exact value, including whitespace.

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
