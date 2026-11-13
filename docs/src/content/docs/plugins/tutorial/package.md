---
title: Package the plugin for sharing
description: Prepare your completed plugin and a known request for another developer or operator.
contentType: Tutorial
contentPlan: plugins/tutorial/package
sidebar: { label: "8. Package The Plugin" }
next: false
---

Make your completed plugin discoverable so another developer can run it and verify the results. Start with [your inspected and tested plugin](../testing/) in `city_example/tutorial.py`. Export its factory, inspect its registered definitions, and prepare a request with known results.

## Export your factory

Point the package’s public `create_plugin` name at the factory you wrote. Replace `city_example/__init__.py` with this export so importing the package selects your implementation:

```python title="city_example/__init__.py"
"""Public factory for the city plugin built in this tutorial."""

from city_example.tutorial import create_plugin

__all__ = ["create_plugin"]
```

The existing project configuration selects that public factory:

```toml
[project.entry-points."lyra.plugins"]
plugin = "city_example:create_plugin"
```

Exporting the factory makes both project inspection and `from city_example import create_plugin` use your implementation. Your tutorial scripts continue to import `city_example.tutorial` directly.

## Verify the exported entry point

Create `tests/test_tutorial_package.py` to confirm the export and exercise a known public request:

```python title="tests/test_tutorial_package.py" {7,18-23}
from lyra.runtime.local import LocalRunner
from city_example import create_plugin
from city_example.tutorial import create_plugin as tutorial_factory


def test_exported_factory_runs_the_authored_graph():
    assert create_plugin is tutorial_factory
    runner = LocalRunner.from_definitions({"city": create_plugin()})
    table = runner.run(
        "city_street_maintenance",
        inputs={
            "streets": {
                "provider": "city_blocks",
                "ids": ["block-1", "block-2"],
            }
        },
    ).tables["maintenance"]
    assert table.street_id.tolist() == [
        "street-1",
        "street-2",
        "street-3",
    ]
    assert table.maintenance_length_m.tolist() == [100.0, 0.0, 300.0]
```

Run the tests, inspect the maintenance metric, and save its dependency graph to `graph.mmd`:

```sh
uv run pytest
uv run lyra-plugin describe city_street_maintenance
uv run lyra-plugin diagram --detailed --output graph.mmd
```

Check that `describe` identifies `city_example.tutorial` as the handler module. Both inspection commands load your factory without reading provider data.

## Keep source and data in the package

You install the project under the distribution name `lyra-city-example` and import it in Python as `city_example`. The project uses Hatch to build the package. Its existing configuration includes your module, the shared components, and `city_example/data/city.json`:

```toml
[tool.hatch.build.targets.wheel]
packages = ["city_example"]
```

The data loader reads package resources, so you can run the plugin from another working directory. If you copy the project elsewhere, update its path to Lyra and regenerate its lockfile. Commit the project files together, including the dataset and lockfile. Follow [Package a plugin](../../publish-and-debug/) for the packaging requirements.

## Hand off a known request

Give the operator a known request to check after enabling your plugin. Include the code revision you tested, your verification results, and this request:

```json {3-10}
{
  "metric": "city_street_maintenance",
  "inputs": {
    "streets": {
      "provider": "city_blocks",
      "ids": ["block-1", "block-2"]
    }
  },
  "parameters": {"condition_threshold": 50}
}
```

The result contains three street rows with maintenance lengths 100 m, 0 m, and 300 m. Include those expected results and explain the synthetic score scale and bundled dataset, so the operator can recognize a successful run. The plugin requires no data service; server execution adds Lyra’s API, Redis, and workers.

To serve your implementation, [deploy the city plugin](../../../operate/deployment/) using your completed project as the image’s build source. For an existing deployment, follow [enable plugins and route work](../../../operate/plugins/).

Run the plugin through [local execution](../../../use/local-execution/), [Python](../../../use/python-client/), [HTTP](../../../use/rest-api/), or the [Model Context Protocol (MCP)](../../../use/mcp/). Optional extensions have independent guides for [concrete metrics](../../metrics/), [resources](../../resources/), and [target resolution](../../location-operations/#resolve-existing-target-records).
