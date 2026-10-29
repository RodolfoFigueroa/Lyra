---
title: Test a plugin
description: Check calculation behavior, input validation, graph execution, and manifest freshness without services.
contentType: How-to
contentPlan: plugins/testing
sidebar: { label: "Test A Plugin" }
---

Test your calculation with small fixtures whose results you know, then check the adapter and dependency graph around it. Replace source acquisition with mocks or in-process fakes so tests run without database, Redis, HTTP, cloud, or Earth Engine connections.

## Test the adapter directly

The decorator leaves your function callable, so you can supply its parameters, resolved geometry, dependency tables, and context yourself. The following test targets the repository’s example table metric and can live in `test_plugin.py` within that plugin project.

Start with the test imports:

```python title="test_plugin.py"
from unittest.mock import create_autospec

import pytest
from lyra.sdk import RunContext
from lyra.sdk.models.geometry import GeoJSON

from smoke_plugin.metrics import run_table
from smoke_plugin.plugin import create_plugin
```

Add a fixture containing one polygon and its coordinate reference system (CRS). Keeping geometry setup in a fixture lets the test focus on the calculation:

```python title="test_plugin.py"
@pytest.fixture
def region():
    feature = {
        "type": "Feature",
        "id": "area",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [[-99.2, 19.3], [-99.1, 19.3], [-99.1, 19.4], [-99.2, 19.3]]
            ],
        },
        "properties": {},
    }
    return GeoJSON.model_validate(
        {
            "type": "FeatureCollection",
            "features": [feature],
            "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        }
    )
```

Append the test, using a context mock that follows the `RunContext` interface:

```python title="test_plugin.py" {7}
def test_table_metric(region):
    plugin = create_plugin()
    parameters = plugin.prepare_parameters("smoke_table_metric", {"value": 7})
    context = create_autospec(RunContext, instance=True)

    frame = run_table(location=region, parameters=parameters, context=context)
    outputs = plugin.normalize_result("smoke_table_metric", frame)

    assert outputs["smoke_table_metric"].equals(frame)
    assert frame["value"].tolist() == [7]
    assert frame.index.tolist() == ["area"]
```

`prepare_parameters` checks the model and raises `MetricInputError` for invalid input. `normalize_result` checks output names, Pandera schemas, and serialization requirements, raising `MetricResultError` for invalid results. Pass `lazy=True` to collect diagnostic failures.

Direct invocation doesn’t execute dependencies, so supply fake tables explicitly when testing a consumer. Use the graph test below to check that Lyra invokes those dependencies and forwards the expected parameters.

## Test the complete graph

The example population graph reads a census table through the `database` resource. Pass a fake `DatabaseIO` subclass to the runner’s `resource_overrides` argument to test it without a database connection.

Create `test_graph.py` with the imports needed for the runner, contexts, and fake database:

```python title="test_graph.py"
import logging
from pathlib import Path
from unittest.mock import create_autospec

import pandas as pd
import pytest
from lyra.sdk import GraphExecutionError, LocalRunner, RunContext
from lyra.sdk.db import DatabaseIO

from smoke_plugin.plugin import create_plugin
```

Add a helper that creates the runner with a fake database and a context for each node. The fake forwards queries to your mock, so tests can check their parameters and control the returned data:

```python title="test_graph.py"
def make_runner(db, temp_dir):
    class FakeDatabase(DatabaseIO):
        def query_dataframe(self, sql, *, params=None):
            return db.query_dataframe(sql, params=params)

    def context_factory(node):
        context = create_autospec(RunContext, instance=True)
        context.job_id = "example_run"
        context.node = node
        context.logger = logging.getLogger(f"example.{node}")
        context.temp_dir = Path(temp_dir)
        return context

    return LocalRunner.from_definitions(
        {"example": create_plugin()},
        context_factory=context_factory,
        resource_overrides={"database": FakeDatabase},
    )
```

The success test checks both the published table and the year sent to the source. Pytest creates `tmp_path` as a temporary directory, and no service client opens a connection:

```python title="test_graph.py"
def test_population_graph(tmp_path):
    db = create_autospec(DatabaseIO, instance=True)
    db.query_dataframe.return_value = pd.DataFrame(
        {"area_code": ["area_a"], "population": [123]}
    )
    runner = make_runner(db, tmp_path)
    request = {"parameters": {"year": 2020}}

    tables = runner.run("example_city_population", request)

    assert tables["example_city_population"]["population"].tolist() == [123]
    db.query_dataframe.assert_called_once()
    assert db.query_dataframe.call_args.kwargs["params"] == {"year": 2020}
```

Add a failure case to verify that diagnostics identify the source node and its dependency path:

```python title="test_graph.py"
def test_population_source_failure(tmp_path):
    db = create_autospec(DatabaseIO, instance=True)
    db.query_dataframe.side_effect = ValueError("Fixture source is unavailable")
    runner = make_runner(db, tmp_path)
    request = {"parameters": {"year": 2020}}

    with pytest.raises(GraphExecutionError) as error:
        runner.run("example_city_population", request)

    assert error.value.node == "example_city_source"
    expected_path = ("example_city_population", "example_city_source")
    assert error.value.path == expected_path
```

Your context factory replaces the runner’s default context construction, so your application owns its clients and temporary storage. These tests use pytest’s temporary directory and mock clients; a local application must arrange cleanup for any resources it creates.

## Check the public result contract

Check the aspects consumers depend on, including output names, identifier columns, column meanings, empty-table behavior, and spatial CRS. Exercise public serialization when callers use server results, because a valid native table can still contain values that JSON cannot encode.

Compare the adapter’s results with the original calculation on a known fixture, including scientific assumptions and units. For lower-level execution tests, create manifests with `PluginDefinition.manifest(...)`, build the graph with `lyra.sdk.graph.build_graph`, and pass the matching plugin ownership keys to `GraphExecutor`.

## Generate valid tables with Hypothesis

Use `Model.to_schema().strategy()` or `schema.strategy()` with Hypothesis for isolated valid examples. Custom checks and geometry may require explicit strategies. Include invalid examples and public serialization checks.

## Keep the manifest current

From the plugin project, run the tests before generating and checking the manifest:

```sh
uv run pytest
uv run lyra-plugin build-manifest
uv run lyra-plugin check-manifest
uv run lyra-plugin describe
```

Commit the generated manifest and include its freshness check in continuous integration (CI), so contract edits cannot leave deployment metadata out of date.

To regenerate the manifest before each commit, add this optional hook to your plugin project’s `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: local
    hooks:
      - id: lyra-plugin-manifest
        name: Build and validate Lyra plugin manifest
        entry: uv run lyra-plugin build-manifest
        language: system
        pass_filenames: false
        always_run: true
```

Install and register pre-commit:

```sh
uv add --dev pre-commit
uv run pre-commit install
```

If the hook updates the manifest, stage the updated file and commit again.

Continue to [package the plugin](../publish-and-debug/) when the calculation, declarations, and generated manifest agree.
