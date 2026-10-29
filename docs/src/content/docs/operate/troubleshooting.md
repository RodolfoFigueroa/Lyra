---
title: Troubleshoot a deployment
description: Diagnose startup failures, missing metrics, queued work, and failed calculations.
contentType: Troubleshooting
contentPlan: operate/troubleshooting
sidebar: { label: "Troubleshoot A Deployment" }
---

Start with the symptom you can observe. Keep the job ID, metric name, queue, and relevant logs together so you can follow a failure across the API and worker.

## Start with a short inspection

Collect these observations before restarting a process, so you can identify where the failure occurs:

1. Check `/live` and `/ready`.
2. Inspect administrator status and its observation metadata.
3. Confirm the metric is in the catalog and has the expected route.
4. Confirm an observed worker consumes that queue.
5. Read worker startup logs and the job’s administrator diagnostics.

The [CLI guide](../admin-cli/) lists the inspection commands. Repeated restarts will not resolve an invalid manifest or missing package.

## Diagnose startup and discovery

If a process fails to start or a metric is missing, check the deployment inputs that control discovery and worker loading:

| Symptom | Check |
| --- | --- |
| Process exits during startup | TOML syntax, required credentials, and readable paths |
| Liveness works but readiness fails | Redis and PostGIS readiness details |
| Metric is absent from the catalog | Installed distribution, enabled entry, manifest path, and manifest validity |
| Worker fails to start | PostGIS probe, plugin imports, name collisions, and manifest freshness |

If you configure `[earth_engine]`, also check the project ID and credential mount on every worker. Workers stop during startup if they cannot load the credentials or initialize Earth Engine. Follow [Enable Earth Engine for workers](../configuration/#enable-earth-engine-for-workers) to check the setup.

The API validates plugin catalogs before creating service clients. An empty catalog is valid. Validation stops startup if it finds:

- Invalid manifests
- Duplicate declarations
- Invalid graphs

Read the process logs, correct the reported plugin or configuration error, then restart the API and workers.

Workers compare live definitions with saved manifests. Generate and check a manifest after contract edits. Use matching images and configuration across the deployment.

## Diagnose submission and execution

Once startup succeeds, use the observed job behavior or error code to narrow the investigation:

| Symptom | Check |
| --- | --- |
| Job stays queued | A live worker consumes its assigned queue |
| Worker reports an unknown metric | API and worker package/configuration agreement |
| Submission returns 422 | The live request schema, required parameters, and named ID selections |
| Spatial resolution returns 503 | `location_provider_unavailable`: inspect provider configuration, credentials, source availability, and server logs |
| Job fails with `invalid_input` | Python validators, defaults, and cross-field rules |
| Job fails with `invalid_result` | Output keys, native table types, CRS, and public serialization |
| Operation fails with `database_not_configured` | [Configure PostgreSQL access](../configuration/#configure-postgresql-access) for providers or metrics that request the `database` resource |
| Job fails with `database_unavailable` | Worker database access and the service’s retry guidance |

A schema-valid request can fail Python validation before its handler runs. Fix the underlying input or semantic rule rather than repeatedly submitting the same payload.

## Diagnose a graph failure

Read the failing node and dependency path, then inspect worker logs for the original exception. The requested metric runs its whole graph on one worker. That worker must have every required plugin, source client, and source permission.

Each metric’s queue applies when you request that metric directly. Dependencies execute on the requested metric’s worker.

Execution stops at the first failure and publishes no partial outputs. A new submission starts a fresh graph; it does not resume a failed node.

## Distinguish missing results from active work

An active job has no completed result yet. Poll its status. An unknown or expired result reference cannot distinguish a never-existing job from expired data. Save completed data before retention ends.

A local wait timeout leaves the remote job running. Reconnect with its ID. See [job handling](../../use/jobs/) and [results](../../use/results/) for the consumer-facing behavior.

After correcting a deployment, follow [the safe update procedure](../updates/) before reopening submissions.
