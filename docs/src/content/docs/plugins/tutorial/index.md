---
title: Build a city analysis plugin
description: Build and test a street maintenance plugin using bundled city data.
contentType: Landing
contentPlan: plugins/tutorial/index
sidebar: { label: "Build A City Plugin" }
---

Suppose you’re building a report to help a city plan street maintenance. You need to identify streets in poor condition, measure how much work they need, and assign that work to blocks. In this tutorial, you’ll turn those calculations into a Lyra plugin that other developers can run with their own selections and thresholds.

Each chapter adds one capability and gives you a result to check before continuing. You’ll use Python, pandas, and geospatial concepts you already know; the chapters introduce Lyra’s concepts as you need them. You need Python 3.11 or later, uv, and a Lyra checkout.

## Prepare the example project

From the repository root, enter the standalone example and prepare its environment:

```sh
cd examples/city-plugin
uv sync
```

The project includes the data and helper functions so you can concentrate on connecting your calculation to Lyra. It also includes tests and a manifest, a saved description of what the plugin accepts and returns. The example uses the software development kit (SDK) from your Lyra checkout. If you copy the project elsewhere, run `uv add /your_lyra_checkout_here/packages/lyra_sdk` to keep using that SDK.

## Understand the city data

The fictional city has three streets and three blocks. The bundled data includes geometry in EPSG:32614, condition scores, and the share of each street assigned to each block. You need no database, server, credentials, or data download.

| Street | Length | Condition score | Membership |
| --- | --- | --- | --- |
| `street-1` | 100 m | 20 | Entirely allocated to `block-1` |
| `street-2` | 200 m | 80 | Half allocated to each of `block-1` and `block-2` |
| `street-3` | 300 m | 40 | Entirely allocated to `block-2` |

Higher scores mean better condition on this synthetic 0–100 scale. A street needs maintenance when its score is strictly below the chosen threshold, default 50. Its full length then contributes to workload.

`block-empty` has no streets and returns zero workload. The shared street lets you check that the report divides work between blocks; the empty block checks that blocks with no work still appear. These are demonstration rules, not a real city’s maintenance policy.

## Follow the chapters

Write the functions that connect the supplied calculations to Lyra as you follow the chapters. These wrapper functions are called adapters. Chapters one through three introduce separate scripts.

From chapter four onward, extend `city_example/tutorial.py` into the complete plugin, then make that plugin available through the package in chapter eight. Add the tests shown in each chapter and keep them as you connect more calculations.

The project supplies the calculations, table schemas, and code for loading the data. It also includes a completed plugin and a runnable version of each chapter for comparison. Run your scripts from the example project directory:

| Chapter | Build | Run |
| --- | --- | --- |
| [1. Register and run a metric](./first-metric/) | Street lengths returned in a result table | `uv run python first_metric.py` |
| [2. Select streets by ID](./select-streets/) | A calculation for the streets you choose | `uv run python selected_metric.py` |
| [3. Add parameters and describe results](./parameters/) | A configurable maintenance calculation | `uv run python maintenance_metric.py` |
| [4. Reuse metric outputs](./dependencies/) | Calculations that share length and condition tables | `uv run python run_tutorial.py` |
| [5. Calculate workload by block](./blocks/) | Street selection and weighted block totals | `uv run python block_report.py` |
| [6. Accept block selections](./canonical/) | Street results requested using block IDs | `uv run python canonical_request.py` |
| [7. Inspect and test the graph](./testing/) | A record of the run and tests without services | `uv run python inspect_tutorial.py` |
| [8. Package the plugin](./package/) | A package another developer can run | `uv run lyra-plugin check-manifest` |

Replace `chapter_number` with a number from 1 to 7 in `uv run python -m city_example chapter_number` to run a supplied checkpoint. These commands run the supplied chapter implementations; the scripts you write run your own code. Chapter three’s script demonstrates threshold 40, while its reference checkpoint uses the default 50.

## Choose an independent guide

For a specific change to your own plugin, use the [plugin guides](../). For running an existing plugin, use [local execution](../../use/local-execution/). Server setup belongs in [deployment](../../operate/deployment/).
