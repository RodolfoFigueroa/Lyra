---
title: Test a plugin
description: Verify calculation values, contracts, and graph orchestration using local fixtures and fake acquisition.
contentType: How-to
contentPlan: plugins/testing
sidebar: { label: "Test A Plugin" }
---

Test the original calculation and its adapter against known values, then exercise the graph with isolated acquisition. The following tests run in the [city project](../tutorial/). Database, Redis, HTTP, cloud, and connection acquisition must use mocks or in-process fakes.

## Test the adapter directly

Create `test_plugin.py`. Build resolved locations from a known frame without acquiring a provider:

```python title="test_plugin.py"
import pytest
from lyra.runtime.invocation import BoundPlugin
from city_example import create_plugin
from city_example.checkpoints import direct_maintenance
from city_example.data import load_city, street_frame
from city_example.schemas import STREET_SCHEMA


@pytest.fixture
def streets():
    return STREET_SCHEMA.validate(street_frame(load_city()))
```

Create a `BoundPlugin` from the plugin definition in your test. Use it to prepare the parameters for `direct_maintenance` and validate the frame the function returns.

```python title="test_plugin.py" {2-3,9,16-17}
def test_table_metric(streets):
    binding = BoundPlugin(create_plugin())
    parameters = binding.prepare_parameters(
        "city_street_maintenance",
        {"condition_threshold": 50},
    )
    frame = direct_maintenance(streets=streets, parameters=parameters)
    frame.index = ["first", "second", "third"]
    outputs = binding.normalize_result("city_street_maintenance", frame)
    assert outputs["maintenance"].maintenance_length_m.tolist() == [100, 0, 300]
    assert outputs["maintenance"].street_id.tolist() == [
        "street-1",
        "street-2",
        "street-3",
    ]
    assert outputs["maintenance"].index.tolist() == [0, 1, 2]
    assert frame.index.tolist() == ["first", "second", "third"]
```

The last two assertions check that the validated table has a zero-based index and the original frame keeps its labels. When adapting an existing calculation, also compare the adapter’s results with the original function’s results.

## Test the complete graph

Create `test_graph.py` with a fake provider class that forwards acquisition to a mock:

```python title="test_graph.py" {15-16,20}
from unittest.mock import Mock
import pytest
from lyra.runtime.execution import GraphExecutionError
from lyra.runtime.local import LocalRunner
from city_example import create_plugin
from city_example.data import load_city, street_frame
from city_example.provider import CityStreetProvider


def make_runner(source):
    class FakeCityProvider(CityStreetProvider):
        def setup(self):
            pass

        def resolve(self, ids):
            return source(ids)

    return LocalRunner.from_definitions(
        {"city": create_plugin()},
        resource_overrides={"city_streets": FakeCityProvider},
    )
```

The runner initializes and closes the fake provider. It still validates the fake’s returned frame against the registered provider contract. Append a known-result test:

```python title="test_graph.py" {12-14}
request = {
    "streets": {
        "provider": "city_streets",
        "ids": ["street-1"],
    }
}


def test_city_graph():
    source = Mock(return_value=street_frame(load_city()).iloc[[0]])
    tables = make_runner(source).run("city_street_maintenance", inputs=request).tables
    assert tables["maintenance"].maintenance_length_m.tolist() == [100.0]
    source.assert_called_once()
    assert source.call_args.args[0] == ["street-1"]
```

Append an acquisition failure and verify the node context:

```python title="test_graph.py" {2-6}
def test_city_source_failure():
    source = Mock(side_effect=ValueError("Fixture source is unavailable"))
    with pytest.raises(GraphExecutionError) as error:
        make_runner(source).run("city_street_maintenance", inputs=request).tables
    assert error.value.node == "city_street_maintenance"
    assert error.value.path == ("city_street_maintenance",)
```

No source client opens a real connection. Keep the service-access guard enabled and replace the acquisition boundary. In-process HTTP transports and temporary files are suitable for integration checks.

## Test location resolution

Use in-memory fixtures to check that location resolution meets these requirements:

- Duplicate IDs appear once, in first-occurrence order.
- Unknown IDs and IDs with the wrong type cause an error.
- Returned records match the registered provider schema.
- Acquired resources close after success or failure.
- Empty selections and operation results retain their columns, dtypes, and CRS.

The city tests also cover shared canonical targets and an empty block’s zero summary.

## Check the public result contract

Verify output names, identifier columns, units, empty-table dtypes, and spatial coordinate reference systems. Exercise public serialization for server results: a native table and its JSON representation have different preservation guarantees. The [results guide](../../use/results/) explains reconstruction.

## Generate valid tables with Hypothesis

Use `schema.strategy()` or `Model.to_schema().strategy()` for valid examples. Custom checks and geometry may need explicit strategies. Include invalid results as well; schema-generated data alone does not verify scientific choices or row correspondence.

## Verify the plugin

Run tests and inspect the live definitions:

```sh
uv run pytest
uv run lyra-plugin describe
```

Run these checks in CI, then follow [Package a plugin](../publish-and-debug/) to prepare the handoff.
