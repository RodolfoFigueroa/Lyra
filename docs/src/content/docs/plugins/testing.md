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
from city_example import create_plugin
from city_example.checkpoints import direct_maintenance
from city_example.data import load_city, street_frame
from city_example.schemas import STREET_SCHEMA, STREETS


@pytest.fixture
def streets():
    return STREET_SCHEMA.build_contract(street_frame(load_city()), STREETS)
```

Append a test of parameter preparation, calculation, result validation, and index ownership:

```python title="test_plugin.py"
def test_table_metric(streets):
    plugin = create_plugin()
    parameters = plugin.prepare_parameters(
        "city_street_maintenance",
        {"condition_threshold": 50},
    )
    frame = direct_maintenance(streets=streets, parameters=parameters)
    frame.index = ["first", "second", "third"]
    outputs = plugin.normalize_result("city_street_maintenance", frame)
    assert outputs["maintenance"].maintenance_length_m.tolist() == [100, 0, 300]
    assert outputs["maintenance"].street_id.tolist() == [
        "street-1",
        "street-2",
        "street-3",
    ]
    assert outputs["maintenance"].index.tolist() == [0, 1, 2]
    assert frame.index.tolist() == ["first", "second", "third"]
```

Direct calls do not orchestrate dependencies or validate returned values. `prepare_parameters` checks the parameter model; `normalize_result` checks named tables and their declarations. Compare with the original function as well when adapting an existing workflow.

## Test the complete graph

Create `test_graph.py` with a fake provider class that forwards acquisition to a mock:

```python title="test_graph.py"
from unittest.mock import Mock
import pytest
from lyra.sdk import GraphExecutionError, LocalRunner
from city_example import create_plugin
from city_example.data import load_city, street_frame
from city_example.provider import CityLocationProvider


def make_runner(source):
    class FakeCityProvider(CityLocationProvider):
        def setup(self):
            pass

        def resolve(self, contract, ids):
            return source(contract, ids)

    return LocalRunner.from_definitions(
        {"city": create_plugin()},
        resource_overrides={"city_data": FakeCityProvider},
    )
```

The runner owns the fake's setup and cleanup. It still validates the fake's returned frame against the registered provider contract. Append a known-result test:

```python title="test_graph.py"
request = {
    "streets": {
        "collection": "example:city",
        "unit_type": "street",
        "ids": ["street-1"],
    }
}


def test_city_graph():
    source = Mock(return_value=street_frame(load_city()).iloc[[0]])
    tables = make_runner(source).run("city_street_maintenance", request)
    assert tables["maintenance"].maintenance_length_m.tolist() == [100.0]
    source.assert_called_once()
    assert source.call_args.args[1] == ["street-1"]
```

Append an acquisition failure and verify the node context:

```python title="test_graph.py"
def test_city_source_failure():
    source = Mock(side_effect=ValueError("Fixture source is unavailable"))
    with pytest.raises(GraphExecutionError) as error:
        make_runner(source).run("city_street_maintenance", request)
    assert error.value.node == "city_street_maintenance"
    assert error.value.path == ("city_street_maintenance",)
```

No source client opens a real connection. Keep the service-access guard enabled and replace the acquisition boundary. In-process HTTP transports and temporary files are suitable for integration checks.

## Test location resolution

Check duplicate IDs, unknown and wrong-type IDs, request ordering, contract validation, and cleanup after success or failure. Test empty internal operation results even though public selections require nonempty IDs. The city tests cover canonical unions and an empty block's zero summary.

## Check the public result contract

Verify output names, identifier columns, units, empty-table dtypes, and spatial coordinate reference systems. Exercise public serialization for server results: a native table and its JSON representation have different preservation guarantees. The [results guide](../../use/results/) explains reconstruction.

## Generate valid tables with Hypothesis

Use `schema.strategy()` or `Model.to_schema().strategy()` for valid examples. Custom checks and geometry may need explicit strategies. Include invalid results as well; schema-generated data alone does not verify scientific choices or row correspondence.

## Keep the manifest current

Run tests, then generate and check the manifest:

```sh
uv run pytest
uv run lyra-plugin build-manifest
uv run lyra-plugin check-manifest
```

Include the freshness check in continuous integration. Follow [packaging](../publish-and-debug/) when code, declarations, and saved contracts agree.
