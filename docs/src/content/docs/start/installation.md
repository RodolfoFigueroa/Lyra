---
title: Install Lyra for Python
description: Set up the city example or install Lyra in an existing Python project.
contentType: How-to
contentPlan: start/installation
sidebar: { label: "Install Lyra For Python" }
---

To try Lyra, start with the city example and its bundled street data. For your own project, choose the dependencies you need. Use Python 3.11 or later and a Lyra checkout for both. To manage the dependencies, install [uv](https://docs.astral.sh/uv/getting-started/installation/).

## Prepare the city example environment

From the root of your Lyra checkout, install the city example and its locked dependencies.

```sh
cd examples/city-plugin
uv sync
```

The example uses the software development kit (SDK) to define the city plugin and the runtime to execute it. To try it with the bundled data, [run your first metric locally](../local/).

If you’re developing Lyra itself, follow the [contributor setup instructions](https://github.com/RodolfoFigueroa/Lyra/blob/main/CONTRIBUTING.md).

## Add Lyra to an existing project

Check out the Lyra release or commit you want to use. Choose the Lyra requirement for your task. The names in brackets select optional dependencies, called extras. Pass each extra to uv with `--extra`.

| What you want to do | What to install |
| --- | --- |
| Validate contracts or use date helpers | `lyra` |
| Submit server jobs, download results, or run `lyra-admin` | `lyra[client]` |
| Convert client results to pandas or GeoPandas tables | `lyra[client,tables]` |
| Read location artifacts, export geospatial files, or work with geometry | `lyra[data]` |
| Define plugins and build their manifests with `lyra-plugin` | `lyra[sdk]` |
| Run plugins locally and cache results | `lyra[runtime]` |
| Serve metrics over the web, expose Model Context Protocol tools, and run background workers | `lyra[server]` |
| Summarize Earth Engine imagery within polygons | `lyra[earth-engine]` |

Run the commands for your task from your project directory. Replace `/your_lyra_checkout_here` with the absolute path to your checkout. Keep the checkout at that path and commit the updated `pyproject.toml` and `uv.lock`.

### Write and test a plugin

Add the SDK as a plugin dependency. Declare any other libraries your plugin imports directly.

```sh
uv add --extra sdk /your_lyra_checkout_here
```

If your tests run the plugin locally, add the runtime as a development dependency.

```sh
uv add --dev --extra runtime /your_lyra_checkout_here
```

### Connect to a server

For server requests and downloads, install the client extra.

```sh
uv add --extra client /your_lyra_checkout_here
```

If you also want pandas or GeoPandas tables, use this command to install both extras together.

```sh
uv add --extra client --extra tables /your_lyra_checkout_here
```

## Choose your next task

[Run a calculation locally](../local/) or [connect the Python client to a server](../../use/python-client/). To deploy your own server, check the [service and data requirements](../../operate/requirements/).
