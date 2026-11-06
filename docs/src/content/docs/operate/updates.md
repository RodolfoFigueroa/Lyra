---
title: Update a deployment safely
description: Drain work and recreate matching API and worker processes after configuration or package changes.
contentType: How-to
contentPlan: operate/updates
sidebar: { label: "Update A Deployment Safely" }
---

Drain active work before updating packages or configuration, then restart the application programming interface (API) and workers together. Keep their plugin contracts and package versions compatible, and ensure workers serve the configured queues.

## Choose the required update

Match the changed files or settings to the processes and artifacts that need updating:

| Change | Required action |
| --- | --- |
| Editable plugin implementation | Invalidate affected cached computations; restart affected workers |
| Metric inputs, outputs, or dependencies | Regenerate the manifest; restart API and workers |
| Package dependency or version | Update deployment locks, rebuild, and recreate containers |
| Plugin selection or queue routing | Validate configuration; restart API and workers |
| Application source | Invalidate cached computations if execution behavior changes; restart affected processes and coordinate API and workers for contract changes |

For editable development, the implementation row assumes mounted source files. For production, rebuild images whenever their packaged contents change.

## Decide whether cached computations remain valid

Restarting processes preserves the computation cache. Lyra checks [computation identities](../../concepts/caching/#keep-cached-results-current) to determine which results it can reuse. Each identity covers the node’s contracts, all declared dependencies, and their installed package versions.

Editorial metadata and unrelated plugins do not affect computation identity. Lyra does not hash Python source or resource settings. Changing an implementation without updating its declarations or version can therefore reuse outdated results.

If an update changes calculated values without changing computation identity, choose an invalidation method:

- Set a new `cache_revision` on every subsequent job request. For local execution, pass it to `runner.run()`. Keep that value on later runs to reuse results from the updated implementation.
- Stop all processes sharing `artifact_dir`, keep Redis available, and run [artifact pruning](../caching/#reclaim-artifact-storage) with `--clear-cache`.

The pruning command preserves artifacts referenced by retained jobs. Preserve the shared artifact directory and the rest of the data volume.

## Drain and restart

Drain work before changing contracts, routing, configuration, or packages. Follow this sequence:

1. Stop new submissions at the proxy or deployment boundary.
2. Drain queued, reserved, and running work.
3. Stop the API and all worker pools with your process supervisor or Compose.
4. Update manifests, configuration, dependency locks, and images as required. Run pruning with `--clear-cache` if you chose that invalidation method.
5. Recreate API and workers with the same image and configuration.
6. Wait for readiness and inspect worker coverage for every routed queue.
7. Reopen submissions.

Allow running work to finish. The example’s worker stop grace period exceeds the default five-hour execution timeout. Check queues and active jobs rather than relying on elapsed time alone.

## Recreate the example

After draining work and preparing the update, rebuild and recreate the containers from `examples/lyra-deployment`:

```sh
docker compose up -d --build --force-recreate
```

Preserve the data volume. For editable development, use the same Compose override files used to start the deployment:

```sh
docker compose -f compose.yml -f compose.dev.yml \
  up -d --build --force-recreate
```

For implementation-only development edits, drain affected work and invalidate cached results as needed. Then restart the workers:

```sh
docker compose -f compose.yml -f compose.dev.yml restart interactive batch
```

## Verify before reopening traffic

Before reopening traffic, check these signals:

- `/ready` returns `200`
- API and worker processes use the intended plugin versions
- Routing assigns each metric to the intended queue
- Worker startup logs report no errors
- An observed worker serves every routed queue

A healthy API can still have no worker consuming a metric’s queue.

Lyra loads configuration once at startup. After correcting a catalog failure, restart the API and workers; request traffic does not trigger a catalog reload.

Use [troubleshooting](../troubleshooting/) for startup and execution failures.
