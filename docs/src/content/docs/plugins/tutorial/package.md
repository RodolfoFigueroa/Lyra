---
title: Package the plugin for sharing
description: Verify the standalone city project and hand its manifest and data to an operator.
contentType: Tutorial
contentPlan: plugins/tutorial/package
sidebar: { label: "8. Package The Plugin" }
next: false
---

Prepare the plugin you built for another developer or operator. Start with [your inspected and tested graph](../testing/). Your implementation is `city_example/tutorial.py`; the provider, calculation helpers, schemas, and bundled data remain part of the same package.

## Export your factory

Replace `city_example/__init__.py` with this export:

```python title="city_example/__init__.py"
"""Public factory for the city plugin built in this tutorial."""

from city_example.tutorial import create_plugin

__all__ = ["create_plugin"]
```

The existing project configuration selects that public factory:

```toml
[tool.lyra]
factory = "city_example:create_plugin"
```

After this edit, both the manifest command and `from city_example import create_plugin` use your implementation. Your `run_tutorial.py`, `block_report.py`, and `canonical_request.py` scripts continue to import the module directly.

## Verify the exported entry point

Create `tests/test_tutorial_package.py` to confirm the export and exercise a known public request:

```python title="tests/test_tutorial_package.py"
from lyra.sdk import LocalRunner
from city_example import create_plugin
from city_example.tutorial import create_plugin as tutorial_factory


def test_exported_factory_runs_the_authored_graph():
    assert create_plugin is tutorial_factory
    runner = LocalRunner.from_definitions({"city": create_plugin()})
    table = runner.run(
        "city_street_maintenance",
        {
            "streets": {
                "collection": "example:city",
                "unit_type": "block",
                "ids": ["block-1", "block-2"],
            }
        },
    )["maintenance"]
    assert table.street_id.tolist() == [
        "street-1",
        "street-2",
        "street-3",
    ]
    assert table.maintenance_length_m.tolist() == [100.0, 0.0, 300.0]
```

Run your complete project test suite before generating the contract:

```sh
uv run pytest
uv run lyra-plugin build-manifest
uv run lyra-plugin check-manifest
uv run lyra-plugin describe city_street_maintenance
uv run lyra-plugin diagram --detailed --output graph.mmd
```

`describe` identifies `city_example.tutorial` as the handler module. The saved manifest and diagram now describe your registered graph. Generating a manifest imports definitions but does not acquire the provider.

## Keep source and data in the package

The distribution is `lyra-city-example`; the import package is `city_example`. Its existing Hatch configuration includes your new module, the shared components, and `city_example/data/city.json`:

```toml
[tool.hatch.build.targets.wheel]
packages = ["city_example"]

[tool.hatch.build.targets.wheel.shared-data]
"lyra.plugin.json" = "share/lyra/plugins/lyra-city-example/lyra.plugin.json"
```

The loader uses `importlib.resources`, so callers do not need the original working directory. When copying the project elsewhere, update its SDK source path and regenerate its lockfile. Commit source, data, tests, lockfile, and manifest together. [Package a plugin](../../publish-and-debug/) explains the general packaging rules.

## Hand off a known request

Give the operator the chosen revision, verification results, and this request:

```json
{
  "metric": "city_street_maintenance",
  "input": {
    "streets": {
      "collection": "example:city",
      "unit_type": "block",
      "ids": ["block-1", "block-2"]
    },
    "parameters": {"condition_threshold": 50}
  }
}
```

The result contains three street rows with maintenance lengths 100 m, 0 m, and 300 m. Describe the synthetic score scale and bundled dataset. The plugin requires no data service; server execution adds Lyra's API, Redis, and workers.

To serve your implementation, [deploy the city plugin](../../../operate/deployment/) using your completed project as the image's build source. For an existing deployment, follow [enable plugins and route work](../../../operate/plugins/).

Run the plugin through [local execution](../../../use/local-execution/), [Python](../../../use/python-client/), [HTTP](../../../use/rest-api/), or [MCP](../../../use/mcp/). Optional extensions have independent guides for [editions](../../editions/), [resources](../../resources/), and [constructed locations](../../location-operations/#convert-tables-and-construct-entities).
