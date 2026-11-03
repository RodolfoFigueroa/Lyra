---
title: Build a city analysis plugin
description: Build and test a street maintenance plugin using bundled city data.
contentType: Landing
contentPlan: plugins/tutorial/index
sidebar: { label: "Build A City Plugin" }
---

Build a plugin that measures streets, identifies maintenance workload, and allocates that workload to city blocks. In each chapter, you add one capability and run it before continuing. You need Python 3.11 or later, uv, and a Lyra checkout.

## Prepare the example project

From the repository root, enter the standalone example and prepare its environment:

```sh
cd examples/city-plugin
uv sync
```

The project declares its dependencies, includes its data, and has its own tests and plugin manifest. Its SDK dependency points to the checkout’s SDK. To work outside this checkout, copy the project and use `uv add /your_lyra_checkout_here/packages/lyra_sdk` to select your SDK source.

## Understand the city data

The fictional city has three streets and three blocks. Package data contains projected geometry in EPSG:32614, synthetic condition observations, and explicit membership weights. The plugin loads files through Python package resources, so you can run it from another directory. You need no database, server, credentials, or data download.

| Street | Length | Condition score | Membership |
| --- | --- | --- | --- |
| `street-1` | 100 m | 20 | Entirely allocated to `block-1` |
| `street-2` | 200 m | 80 | Half allocated to each of `block-1` and `block-2` |
| `street-3` | 300 m | 40 | Entirely allocated to `block-2` |

Higher scores mean better condition on this synthetic 0–100 scale. A street needs maintenance when its score is strictly below the chosen threshold, default 50. Its full length then contributes to workload.

`block-empty` has no streets and returns zero workload. These choices define the demonstration calculation.

## Follow the chapters

Write and run your own adapters as you follow the chapters. Chapters one through three introduce separate scripts. From chapter four onward, extend `city_example/tutorial.py` into the complete plugin, then export its factory in chapter eight. Add the tests shown in each chapter and keep them as the graph grows.

The project supplies the calculation helpers, schemas, provider, and data. Its completed implementation and numbered reference checkpoints remain available for comparison. Run your scripts from the example project directory:

| Chapter | Build | Run |
| --- | --- | --- |
| [1. Register and run a metric](./first-metric/) | A fixed-input calculation and named table | `uv run python first_metric.py` |
| [2. Select streets by ID](./select-streets/) | Spatial inputs and a provider | `uv run python selected_metric.py` |
| [3. Add parameters and describe results](./parameters/) | A configurable maintenance calculation | `uv run python maintenance_metric.py` |
| [4. Reuse metric outputs](./dependencies/) | An explicit table dependency graph | `uv run python run_tutorial.py` |
| [5. Calculate workload by block](./blocks/) | Derived location inputs and aggregation | `uv run python block_report.py` |
| [6. Accept block selections](./canonical/) | Canonical selection for street calculations | `uv run python canonical_request.py` |
| [7. Inspect and test the graph](./testing/) | Provenance and isolated tests | `uv run python inspect_tutorial.py` |
| [8. Package the plugin](./package/) | A verified manifest and operator handoff | `uv run lyra-plugin check-manifest` |

Replace `chapter_number` with a number from 1 to 7 in `uv run python -m city_example chapter_number` to run a supplied checkpoint. These commands use the reference handlers; your authored scripts use your own implementations. Chapter three’s script demonstrates threshold 40, while its reference checkpoint uses the default 50.

## Choose an independent guide

For a specific change to your own plugin, use the [plugin guides](../). For running an existing plugin, use [local execution](../../use/local-execution/). Server setup belongs in [deployment](../../operate/deployment/).
