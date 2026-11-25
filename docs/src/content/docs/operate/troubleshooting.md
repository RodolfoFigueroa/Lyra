---
title: Troubleshoot a deployment
description: Diagnose startup failures, missing metrics, queued work, and failed calculations.
contentType: Troubleshooting
contentPlan: operate/troubleshooting
sidebar: { label: "Troubleshoot A Deployment" }
---

Start with the symptom you can observe. Use the job’s identifiers and logs to trace failures across the API and workers.

## Start with a short inspection

Collect these observations before restarting a process, so you can identify where the failure occurs:

1. Check `/live` and `/ready`.
2. Inspect administrator status and its observation metadata.
3. Run `lyra-admin catalog show` and look for the requested metric.
4. Run `lyra-admin queue show` and check for an active worker.
5. Read worker startup logs and the job’s administrator diagnostics.

Use the [command-line interface (CLI) guide](../admin-cli/) to find the inspection commands. Correct invalid plugin definitions or install missing packages before restarting.

## Diagnose startup and discovery

If a process fails to start or a metric is missing, check the deployment inputs that control discovery and worker loading:

| Symptom | Check |
| --- | --- |
| Process exits during startup | TOML syntax, required credentials, and readable paths |
| Liveness works but readiness fails | Redis readiness details |
| Metric is absent from the catalog | Installed distribution, enabled entry, factory import, and registered metrics |
| Worker fails to start | Plugin imports, name collisions, and graph validation |

For plugin resource failures, check the resource settings and credential references on workers executing the affected job. Follow [Configure plugin resources](../configuration/#configure-plugin-resources) to check the setup.

The API validates plugin catalogs before creating service clients. An empty catalog is valid. Validation stops startup if it finds:

- Invalid plugin definitions
- Duplicate declarations
- Invalid graphs

Read the process logs, correct the reported plugin or configuration error, then restart the API and workers.

At startup, the API and workers import enabled plugin factories and validate their definitions. Install each plugin and its dependencies in the shared deployment image, and use matching configuration for both processes.

## Diagnose submission and execution

Once startup succeeds, use the observed job behavior or error code to narrow the investigation:

| Symptom | Check |
| --- | --- |
| Job stays queued | Run `lyra-admin workers list`. If no worker has a fresh heartbeat, check the worker startup logs. If all workers are busy, the job waits for one to finish. |
| Worker reports an unknown metric | API and worker package/configuration agreement |
| Submission returns 422 | The live request schema, required parameters, and named ID selections |
| Job location resolution fails | Read the failure’s node, input, and reason. Check worker logs, provider configuration, credentials, and source availability. |
| Job fails with `invalid_input` | Python validators, defaults, and cross-field rules |
| Job fails with `invalid_result` | Output keys, native table types, CRS, and public serialization |
| Job fails with `resource_configuration` | The named resource’s settings and credential references in `lyra.toml` |
| Job fails while querying a source | The failing node’s logs, source availability, and the plugin’s connection settings |

A schema-valid request can fail Python validation before its handler runs. Fix the underlying input or semantic rule rather than repeatedly submitting the same payload.

## Diagnose a graph failure

Find the failing node and dependency path in the job details, then inspect the worker logs for the exception. One worker runs the whole graph. Check that it has the plugins, source clients, and permissions the failing node needs.

Execution stops at the first failure and publishes no partial outputs. A new submission starts a fresh graph; it does not resume a failed node.

## Distinguish missing results from active work

Check terminal history by job ID. Restore Redis access to inspect queued and running jobs.

A local wait timeout leaves the remote job running. Reconnect with its ID. See [job handling](../../use/jobs/) and [results](../../use/results/) for the consumer-facing behavior.

After correcting a deployment, follow [the safe update procedure](../updates/) before reopening submissions.
