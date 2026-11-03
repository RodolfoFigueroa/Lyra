---
title: Develop with editable sources
description: Mount plugin checkouts, regenerate contracts, and restart matching development processes.
contentType: How-to
contentPlan: plugins/development
sidebar: { label: "Develop With Editable Sources" }
---

Use the example deployment to develop plugin code in editable checkouts. Complete [deployment setup](../../operate/deployment/) first; this workflow uses its development image and source mounts.

## Develop with editable sources

From `examples/lyra-deployment`, use the checked-in override:

```sh
docker compose -f compose.yml -f compose.dev.yml up -d --build
```

The city plugin includes its geometry and observations. Configure named resources when adding plugins that require external data.

The override mounts application and plugin checkouts at their editable installation paths. Its absolute `manifest_path` points to the generated manifest in the mounted plugin checkout. Install the distribution even when you mount its source.

Keep container dependencies in `/app/.venv`; mount source checkouts without replacing that environment with a host virtual environment. Source edits require manual process restarts; use the [change table](../../operate/updates/#choose-the-required-update).

After changing a metric contract, regenerate and check its manifest from the Lyra repository root:

```sh
uv run lyra-plugin build-manifest --project examples/city-plugin
uv run lyra-plugin check-manifest --project examples/city-plugin
```

Restart the API and workers after updating the manifest. Workers reject manifests that differ from imported definitions.

## Add another checkout

For an independently maintained plugin:

1. Add the distribution and source to both dependency sets; make the development source editable.
2. Add a named Compose build context for its checkout.
3. Copy that context into the Dockerfile’s source stage, preserving relative dependency paths.
4. Add a matching development mount to API and worker services.
5. Enable the distribution in both TOML files; set a development `manifest_path`.
6. Generate the manifest, update locks, and rebuild.

`LYRA_SOURCE` and `CITY_PLUGIN_SOURCE` identify the example checkouts. For another plugin, choose source and container paths that resolve consistently during locking and image builds.

For a plugin checkout beside the Lyra repository, use these paths in the example deployment:

| Setting | Path |
| --- | --- |
| Production uv source | `../../../other-plugin` |
| Development uv source | `../../../../other-plugin` |
| Container source and development mount destination | `/workspace/other-plugin` |

Copy each build context into the Dockerfile’s `sources` stage and mount its checkout read-only during development. Add native libraries required by the plugin to the runtime stage.

Commit both lockfiles and source changes: local path locks pin dependencies but do not pin checkout contents. For production Git dependencies, pin full commit IDs and declare the SDK and utilities’ repository subdirectories at the same commit. Supply private dependency credentials during image builds.

Apply changes with the [safe update procedure](../../operate/updates/).
