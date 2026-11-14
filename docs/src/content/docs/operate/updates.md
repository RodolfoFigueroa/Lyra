---
title: Update a deployment safely
description: Drain work and recreate matching API and worker processes after configuration or package changes.
contentType: How-to
contentPlan: operate/updates
sidebar: { label: "Update A Deployment Safely" }
---

Drain active work before updating packages or configuration, then restart the API and workers together. Use one immutable build and the same `build_revision` for both. Check that workers serve every configured queue before reopening submissions.

## Choose the required update

Match the changed files or settings to the processes and deployment files that need updating:

| Change | Required action |
| --- | --- |
| Editable plugin implementation | Update affected code revisions; restart API and workers |
| Metric inputs, outputs, or dependencies | Update affected revisions when outputs change; restart API and workers |
| Package dependency or version | Update deployment locks, rebuild, and recreate containers |
| Plugin selection or queue routing | Validate configuration; restart API and workers |
| Application source | Update code revisions if execution behavior changes; restart affected processes and coordinate API and workers for contract changes |

For editable development, the implementation row assumes mounted source files. For production, rebuild images whenever their packaged contents change.

## Decide whether cached computations remain valid

Restarting processes preserves stored results. Lyra uses [computation identities](../../concepts/caching/#keep-cached-results-current) to decide which values it can reuse. These identities include declared revisions and effective inputs.

Update each affected node’s `code_revision` when its implementation or declarations change the output. For external data changes, update the [origin or provider identity](../../plugins/location-providers/#define-revision-coverage) with your plugin authors. Lyra does not detect code or data changes automatically.

Preserve the SQLite file configured by `result_store_path` and the rest of the data volume. Changing declared identities leaves existing stored values and result descriptions intact.

## Drain and restart

Drain work before changing contracts, routing, configuration, or packages. Follow this sequence:

1. Stop new submissions at the proxy or deployment boundary.
2. Drain queued, reserved, and running work.
3. Stop the API and all worker pools with your process supervisor or Compose.
4. Apply the changes from [Choose the required update](#choose-the-required-update).
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

For implementation-only development edits, drain affected work and update code revisions as needed. Then restart the API and workers:

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
