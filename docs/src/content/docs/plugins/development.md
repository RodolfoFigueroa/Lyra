---
title: Develop with editable sources
description: Mount plugin checkouts and restart the API and workers after source changes.
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

The override mounts application and plugin checkouts at their editable installation paths. Install the distribution even when you mount its source.

Keep container dependencies in `/app/.venv`. Mount source checkouts without replacing that environment with a host virtual environment.

After changing plugin definitions, restart the API and workers to load the updated definitions. Follow [Choose the required update](../../operate/updates/#choose-the-required-update) to update revisions when code or data changes affect outputs. Development runs retain results in the configured SQLite store.

## Add another checkout

For an independently maintained plugin:

1. Add the distribution and source to both dependency sets; make the development source editable.
2. Add a named Compose build context for its checkout.
3. Copy that context into the Dockerfile’s source stage, preserving relative dependency paths.
4. Add a matching development mount to API and worker services.
5. Enable the distribution in `lyra.toml` and `lyra.dev.toml`.
6. Update both dependency lockfiles and rebuild the development image.

`LYRA_SOURCE` and `CITY_PLUGIN_SOURCE` identify the example checkouts. For another plugin, choose source and container paths that resolve consistently during locking and image builds.

For a plugin checkout beside the Lyra repository, use these paths in the example deployment:

| Setting | Path |
| --- | --- |
| Production uv source | `../../../other-plugin` |
| Development uv source | `../../../../other-plugin` |
| Container source and development mount destination | `/workspace/other-plugin` |

Copy each build context into the Dockerfile’s `sources` stage and mount its checkout read-only during development. Add native libraries required by the plugin to the runtime stage.

Commit both lockfiles alongside your source changes. A local path in a lockfile points to the checkout, whose contents can change.

Declare `lyra[server]` in the deployment project. If production builds install Lyra or plugins from Git, pin each repository to a full commit ID. Supply credentials during the image build for any private repositories.

Apply changes with the [safe update procedure](../../operate/updates/).
