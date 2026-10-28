---
title: Update a deployment safely
description: Drain work and recreate matching API and worker processes after configuration or package changes.
contentType: How-to
contentPlan: operate/updates
sidebar: { label: "Update A Deployment Safely" }
---

API and workers must agree on plugin contracts, package versions, and routing. Plan the update before restarting processes that may be executing work.

## Choose the required update

Match the changed files or settings to the processes and artifacts that need updating:

| Change | Required action |
| --- | --- |
| Editable plugin implementation | Restart affected workers |
| Metric inputs, outputs, or dependencies | Regenerate the manifest; restart API and workers |
| Package dependency or version | Update deployment locks, rebuild, and recreate containers |
| Plugin selection or queue routing | Validate configuration; restart API and workers |
| Application source | Restart affected processes; coordinate API and workers for contract changes |

These actions assume the editable development setup where source is mounted. Production image contents change only after an image rebuild.

## Drain and restart

For contract, routing, configuration, or package updates:

1. Stop new submissions at the proxy or deployment boundary.
2. Drain queued, reserved, and running work.
3. Stop the API and all worker pools with your process supervisor or Compose.
4. Update manifests, validate TOML, and update locks and images where needed.
5. Recreate API and workers with the same image and configuration.
6. Wait for readiness and inspect worker coverage for every routed queue.
7. Reopen submissions.

Allow running work to finish. The example’s worker stop grace period exceeds the default five-hour execution timeout. Check queues and active jobs rather than relying on elapsed time alone.

## Recreate the example

From `examples/lyra-deployment`, after draining and preparing the change:

```sh
docker compose up -d --build --force-recreate
```

Preserve the data volume. For editable development, use the same Compose override files used to start the deployment:

```sh
docker compose -f compose.yml -f compose.dev.yml up -d --build --force-recreate
```

For implementation-only edits in development, a worker restart is sufficient:

```sh
docker compose -f compose.yml -f compose.dev.yml restart interactive batch
```

## Verify before reopening traffic

Check `/ready`, installed plugin versions, effective routing, worker startup logs, and observed queue coverage. A healthy API can still have no worker consuming a metric’s queue.

Configuration is loaded once at startup. After correcting a catalog failure, restart the API and workers; request traffic does not trigger a catalog reload.

Use [troubleshooting](../troubleshooting/) for startup and execution failures.
