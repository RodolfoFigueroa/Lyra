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

Keep the SQLite file and its volume when you restart or update your deployment. Workers save required outputs and a result description, which identifies those outputs, before reporting success.

Lyra retains stored values and result descriptions indefinitely. Job expiration does not delete them, and neither do worker restarts or changes to cache settings.

## Update revisions when results change

Update the relevant declaration whenever code or source data changes the output. An [origin](../../plugins/location-providers/#declare-a-provider) identifies an external dataset and its revision. Coordinate these revisions with your plugin authors before [updating the deployment](../updates/):

- Change a node’s `code_revision` when its implementation or declarations affect outputs
- Change an origin’s identity when its external data changes
- Change a provider’s code revision or ID when its records or collection membership change

Changing declared identities changes which stored values a computation can reuse; earlier values remain in the file. Inspect [execution observations](../../use/results/#inspect-execution-provenance) to see whether a handler ran. A node with `cache="off"` executes when invoked, even if its value is retained.
