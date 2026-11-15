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

To read data after job expiry, use [read-only `ResultStore` lookup](../../use/results/#reopen-a-local-result) with a saved execution ID. You need filesystem access to the store, but no plugins or external data acquisition. Remote job methods still depend on Redis retention.

Retention applies while the store’s schema remains compatible with your Lyra version. Closed alpha doesn’t promise migrations or readers for incompatible stores; you can explicitly reset a development store if needed.

## Update revisions when results change

Update the relevant declaration whenever code or source data changes the output. An [origin](../../plugins/location-providers/#declare-a-provider) identifies an external dataset and its revision. Coordinate these revisions with your plugin authors before [updating the deployment](../updates/):

- Change a node’s `code_revision` when its implementation or declarations affect outputs
- Change an origin’s identity when its external data changes
- Change a provider’s code revision or ID when its records or collection membership change

Changing declared identities changes which stored values a computation can reuse; earlier values remain in the file. Inspect [execution observations](../../use/results/#inspect-execution-provenance) to see whether a handler ran. A node with `cache="off"` executes when invoked, even if its value is retained.
