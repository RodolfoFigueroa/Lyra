---
title: Inspect and test the graph
description: Inspect accepted inputs and observed execution, and verify the city calculation without services.
contentType: Tutorial
contentPlan: plugins/tutorial/testing
sidebar: { label: "7. Inspect And Test" }
---

Inspect your graph's accepted request and actual execution, then test a failing acquisition boundary. Start with [your canonical selector](../canonical/). Keep the existing tests; all acquisition remains local or uses in-process fakes.

## Inspect your completed run

Create `inspect_tutorial.py` in the project root. Construct the runner from your factory and run the block-to-street request:

```python title="inspect_tutorial.py"
from lyra.sdk import LocalRunner
from city_example.tutorial import create_plugin

plugin = create_plugin()
runner = LocalRunner.from_definitions({"city": plugin})
tables = runner.run(
    "city_street_maintenance",
    {
        "streets": {
            "collection": "example:city",
            "unit_type": "block",
            "ids": ["block-1", "block-2"],
        }
    },
)
print(tables["maintenance"].to_dict("records"))
```

Append provenance inspection to the same file:

```python title="inspect_tutorial.py"
provenance = runner.inspect()
if provenance is not None:
    if provenance.accepted is not None:
        for accepted in provenance.accepted.inputs:
            print(accepted.name, accepted.ids, accepted.binding.operations)
    for native in provenance.native_inputs:
        print(native.name, native.count)
    for observation in provenance.observations:
        print(observation.node, observation.outcome)
```

Run `uv run python inspect_tutorial.py`. The accepted plan records the original block selections and bound routes. The native input summary reports three streets. Execution observations identify the selector, table producers, and consumer, with their observed outcomes. A planned dependency alone is not evidence that its handler ran.

## Inspect declared dependencies

Append a contract inspection to `inspect_tutorial.py`:

```python title="inspect_tutorial.py"
description = plugin.describe("city_street_maintenance")
print(description.inputs)
```

Run the script again. The declaration names `lengths` and `conditions` as table dependencies. Canonical routes describe available public selection; they do not add a new declared table dependency to this metric.

## Test observed native inputs

Append this test to `tests/test_tutorial.py`:

```python title="tests/test_tutorial.py"
def test_inspection_reports_native_streets():
    runner = LocalRunner.from_definitions({"city": create_plugin()})
    runner.run(
        "city_street_maintenance",
        {
            "streets": {
                "collection": "example:city",
                "unit_type": "block",
                "ids": ["block-1", "block-2"],
            }
        },
    )
    provenance = runner.inspect()
    assert provenance is not None
    assert provenance.accepted is not None
    assert {item.name: item.count for item in provenance.native_inputs} == {
        "streets": 3,
    }
    assert any(
        item.node == "city_street_maintenance" and item.outcome == "completed"
        for item in provenance.observations
    )
```

This test checks resolved input counts and an actual completion observation independently of the returned table.

## Replace a failing acquisition boundary

Add these imports and fake provider to `tests/test_tutorial.py`:

```python title="tests/test_tutorial.py"
import pytest
from lyra.sdk import GraphExecutionError, LocationAcquisitionError
from city_example.provider import CityLocationProvider


class FailingCityProvider(CityLocationProvider):
    def resolve(self, contract, ids):
        raise ValueError("Fixture source is unavailable")
```

Append a graph test that injects the fake without changing your plugin's registration:

```python title="tests/test_tutorial.py"
def test_acquisition_failure_identifies_the_root():
    runner = LocalRunner.from_definitions(
        {"city": create_plugin()},
        resource_overrides={"city_data": FailingCityProvider},
    )
    with pytest.raises(GraphExecutionError) as error:
        runner.run(
            "city_street_maintenance",
            {
                "streets": {
                    "collection": "example:city",
                    "unit_type": "street",
                    "ids": ["street-1"],
                }
            },
        )
    assert error.value.node == "city_street_maintenance"
    assert error.value.path == ("city_street_maintenance",)
    acquisition = error.value.__cause__
    assert isinstance(acquisition, LocationAcquisitionError)
    assert isinstance(acquisition.__cause__, ValueError)
```

Lyra wraps the provider failure as an acquisition error and adds graph node and path context. Exception chaining preserves the underlying failure.

Run all the tests you have written:

```sh
uv run pytest tests/test_first_metric.py tests/test_selected_metric.py \
  tests/test_maintenance_metric.py tests/test_tutorial.py
```

The fixture provider reads bundled files; the failing provider raises locally. Keep the project's service-access guard enabled. [Plugin testing](../../testing/) covers additional acquisition and serialization boundaries. Continue to [package your plugin](../package/) and inspect its saved manifest.
