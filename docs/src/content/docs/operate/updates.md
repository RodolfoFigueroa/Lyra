---
title: Update a deployment safely
description: Drain work and recreate matching API and worker processes after configuration or package changes.
contentType: How-to
contentPlan: operate/updates
sidebar: { label: "Update A Deployment Safely" }
---

Drain active work before updating packages or configuration, then restart the API and workers together. Use one immutable build and the same `build_revision` for both. Check that workers serve every configured queue before reopening submissions.

## Choose the required update

Match the changed files or settings to the processes and artifacts that need updating:

| Change | Required action |
| --- | --- |
| Editable plugin implementation | Invalidate affected cached computations; restart API and workers |
| Metric inputs, outputs, or dependencies | Restart API and workers to load the updated definitions |
| Package dependency or version | Update deployment locks, rebuild, and recreate containers |
| Plugin selection or queue routing | Validate configuration; restart API and workers |
| Application source | Invalidate cached computations if execution behavior changes; restart affected processes and coordinate API and workers for contract changes |

For editable development, the implementation row assumes mounted source files. For production, rebuild images whenever their packaged contents change.

## Decide whether cached computations remain valid

Restarting processes preserves the computation cache. Lyra checks [computation identities](../../concepts/caching/#keep-cached-results-current) to determine which results it can reuse. Each identity covers the node’s semantic contracts, declared dependencies, and author-maintained `(name, code_revision)`. Current provider identity also includes its distribution version and source contracts.

Editorial metadata and unrelated plugins do not affect computation identity. Lyra does not hash Python source or resource settings. Changing an implementation without updating its `code_revision` can therefore reuse outdated results.

Advance affected nodes’ `code_revision` values when calculation behavior changes. For uncovered data changes or deliberate recomputation, choose an invalidation method:

- Set a new `cache_revision` on every subsequent job request. For local execution, pass it to `runner.run()`. Keep that value on later runs to reuse results from the updated implementation.
- Stop all processes sharing `artifact_dir`, keep Redis available, and run [artifact pruning](../caching/#reclaim-artifact-storage) with `--clear-cache`.

The pruning command preserves artifacts referenced by retained jobs. Preserve the shared artifact directory and the rest of the data volume.

## Drain and restart

Drain work before changing contracts, routing, configuration, or packages. Follow this sequence:

1. Stop new submissions at the proxy or deployment boundary.
2. Drain queued, reserved, and running work.
3. Stop the API and all worker pools with your process supervisor or Compose.
4. Apply the changes from [Choose the required update](#choose-the-required-update). If you chose cache pruning, run it with `--clear-cache` before restarting processes.
5. Assign a new immutable `build_revision` when executable code, declarations, or routing change. Recreate API and workers with the same image and configuration.
6. Wait for readiness and inspect worker coverage for every routed queue.
7. Reopen submissions.

Pending jobs from another build fail with `deployment_changed` before acquiring resources. Resubmit those requests against the current deployment.

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

For implementation-only development edits, drain affected work and invalidate cached results as needed. Then restart the API and workers:

```sh
docker compose -f compose.yml -f compose.dev.yml restart api interactive batch
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
