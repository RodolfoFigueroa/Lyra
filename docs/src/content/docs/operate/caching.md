---
title: Configure shared result storage
description: Configure shared SQLite storage and retain computation results across runs.
contentType: How-to
contentPlan: operate/caching
sidebar: { label: "Configure Shared Result Storage" }
---

Configure one SQLite file so your API and workers share stored results on one host. Keep that file across deployments, and update declared revisions when code or source data changes.

## Store computation results

Choose an absolute path that the API and every worker can read and write. Keep the file outside job scratch directories.

`result_store_path` defaults to `LYRA_DATA_DIR/results.sqlite3`. To use another file, add this setting before any section in your [configuration file](../configuration/):

```toml {1}
result_store_path = "/srv/lyra/results.sqlite3"
```

Lyra creates the parent directory when it initializes storage. Give the account running Lyra write access to both the file and directory. SQLite needs directory access to create journal files.

The [example deployment](../deployment/) selects `/lyra_data/results.sqlite3` in both configurations. Its API and workers share the data volume on one host.

## Preserve stored results

Keep the SQLite file and its volume when you restart or update your deployment. Before reporting success, workers save required final values and publish the result description and complete report in one transaction. Local and development runs follow the same rules.

If storage or publication fails, the attempt fails even when calculation finished. Complete cache values saved earlier can remain reusable.

Lyra retains stored values, result descriptions, and reports indefinitely within a supported store. Job expiry, worker restarts, and changes to cache keys or policies don’t delete them. Plan for storage growth: Lyra doesn’t automatically remove old data.

After queue expiry, use the job ID to [read retained results through the API](../../use/results/#keep-access-after-job-expiry). With filesystem access, you can also [open the store locally](../../use/results/#reopen-a-local-result). Local reads need no plugins or provider connections.

Retention applies while the store’s schema remains compatible with your Lyra version. Closed alpha doesn’t promise migrations or readers for incompatible stores; you can explicitly reset a development store if needed.

Metric publication commits all newly computed partitions from one successful
invocation together, including every named output, completion record, and retained
input binding. If publication fails, none of that invocation's new metric values
remain. Previously committed partitions and successful dependencies remain reusable.
Each partition keeps its own computation identity, so overlapping requests can
reuse individual partitions. Captured inputs can remain stored after a failure;
they do not mark the failed metric as complete.

## Update revisions when results change

Update the relevant declaration whenever code or source data changes the output. An [origin](../../plugins/location-providers/#declare-a-provider) identifies an external dataset and its revision. Coordinate these revisions with your plugin authors before [updating the deployment](../updates/):

- Change a node’s `code_revision` when its implementation or declarations affect outputs
- Change an origin’s identity when its external data changes
- Change a provider’s code revision or ID when its records or collection membership change

Changing declared identities changes which stored values a computation can reuse; earlier values remain in the file. Inspect [execution observations](../../use/results/#inspect-execution-provenance) to see whether a handler ran. A node with `cache="off"` executes when invoked, even if its value is retained.

## Budget for retained geographic inputs

Successful results retain the provider records, operation pairs and shared selection
metadata needed for available named location inputs. Small metric outputs can refer
to large inputs. Provider values are stored once per qualified identity; shared
memberships are referenced across partitions, and correspondence tables store pairs
without copied target geometries. Reads join those pairs to retained target records.
Generated Arrow streams are not stored artifacts.

These savings do not bound growth. Dense pair sets can exceed either provider's row
count, and changed revisions or selections add distinct retained data. Initial
serialization, indexes and write contention add costs. Native input reads require
memory proportional to the requested selection; descriptions avoid geometry reads,
and HTTP/MCP rows use bounded SQL-selected pages. See the
[benchmark guide](https://github.com/RodolfoFigueroa/lyra/blob/main/benchmarks/USAGE.md)
for measurement scope.

A provider with caching disabled still acquires records when a calculation needs them. A downstream metric can skip acquisition if it reuses its cached result. To [read saved inputs after queue expiry](../../use/results/#read-retained-location-inputs), keep the job ID. These reads use the saved records directly.
