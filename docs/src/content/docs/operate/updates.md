---
title: Update a deployment safely
description: Drain work and recreate matching API and worker processes after configuration or package changes.
contentType: How-to
contentPlan: operate/updates
sidebar: { label: "Update A Deployment Safely" }
---

Pause submissions before updating packages or configuration. Restart the API and workers with the same build and configuration. Before accepting new jobs, check API readiness and look for a worker heartbeat on the queue.

## Choose the required update

Match the changed files or settings to the processes and deployment files that need updating:

| Change | Required action |
| --- | --- |
| Editable plugin implementation | Update affected code revisions; restart API and workers |
| Metric inputs, outputs, or dependencies | Update affected revisions when outputs change; restart API and workers |
| Package dependency or version | Update deployment locks, rebuild, and recreate containers |
| Plugin selection or queue name | Validate `lyra.toml`, then restart the API and worker service |
| Application source | Update code revisions if execution behavior changes; restart affected processes and coordinate API and workers for contract changes |

For editable development, the implementation row assumes mounted source files. For production, rebuild images whenever their packaged contents change.

## Decide whether cached computations remain valid

Restarting processes preserves stored results. Lyra uses [computation identities](../../concepts/caching/#keep-cached-results-current) to decide which values it can reuse. These identities include declared revisions and effective inputs.

Update each affected node’s `code_revision` when its implementation or declarations change the output. For external data changes, update the [origin or provider identity](../../plugins/location-providers/#define-revision-coverage) with your plugin authors. Lyra does not detect code or data changes automatically.

Preserve the SQLite file configured by `result_store_path` and the rest of the data volume. Changing declared identities leaves existing stored values and result descriptions intact.

## Drain and restart

Keep submissions paused until the updated API and workers can accept jobs.

1. Stop new submissions at the proxy or deployment boundary.
2. Drain the queue and let running jobs finish. If you’re switching queues, you can discard waiting jobs before the switch.
3. Stop the API and the worker service with your process supervisor or Compose.
4. Apply the changes from [Choose the required update](#choose-the-required-update).
5. Assign a new [build revision](../configuration/#assign-a-deployment-build-revision) if you changed executable code, declarations, or provider routing rules. Recreate the API and worker service with the same image and configuration.
6. [Verify the deployment](#verify-before-reopening-traffic), then reopen submissions.

If a worker receives a job from another build, it returns `deployment_changed` before acquiring resources. Resubmit that request against the updated deployment.

In the example, Compose gives workers more than the default five-hour job timeout to shut down. Use `lyra-admin queue show` and `lyra-admin workers list` to check for waiting and running jobs before stopping the services.

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

If you edited only mounted development code, update the affected revisions and drain the queue. Restart both services to load your changes.

```sh
docker compose -f compose.yml -f compose.dev.yml restart api worker
```

## Verify before reopening traffic

Reopen submissions once these checks pass.

- `GET /ready` returns HTTP 200
- API and worker processes use the intended plugin versions
- Worker startup logs report no errors
- `lyra-admin queue show` lists an active worker on the configured queue

Lyra loads configuration once at startup. After correcting a catalog failure, restart the API and workers; request traffic does not trigger a catalog reload.

Use [troubleshooting](../troubleshooting/) for startup and execution failures.
