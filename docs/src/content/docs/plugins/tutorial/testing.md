---
title: Inspect and test the graph
description: Explain which inputs a run used, which calculations ran, and why a data lookup failed.
contentType: Tutorial
contentPlan: plugins/tutorial/testing
sidebar: { label: "7. Inspect And Test" }
---

When a report surprises you, the result table alone may not explain what happened. You’ll inspect which blocks Lyra accepted, which streets it used, and which calculations ran, then test a data-loading failure. Start with [your block selection for street reports](../canonical/) and keep the existing tests; these checks use bundled data and local fakes.

## Inspect your completed run

Create `inspect_tutorial.py` in the project root. Construct the runner from your factory and run the block-to-street request:

```python title="inspect_tutorial.py"
from lyra.runtime.local import LocalRunner
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

Add the following inspection to the same file. [Provenance](../../../concepts/provenance/) is the record of the inputs and execution behind a result; `runner.inspect()` lets you read it after the run:

```python title="inspect_tutorial.py" {3-9}
provenance = runner.inspect()
if provenance is not None:
    if provenance.accepted is not None:
        for accepted in provenance.accepted.inputs:
            print(accepted.name, accepted.ids, accepted.binding.operations)
    for native in provenance.execution.native_inputs:
        print(native.name, native.count)
    for observation in provenance.execution.observations:
        print(observation.node, observation.outcome)
```

Run `uv run python inspect_tutorial.py`. The accepted plan records the two block IDs you requested and the operation chosen to find their streets. The native input summary reports three streets: these are the locations the maintenance calculation actually uses.

Execution observations record outcomes for the street-selection operation and the metrics that supply and use tables. A dependency diagram shows what a calculation needs; these observations show whether a handler completed or Lyra reused a result. The plan alone does not prove that a function ran.

## Inspect declared dependencies

To see what your plugin declares before considering a particular run, add this inspection to `inspect_tutorial.py`:

```python title="inspect_tutorial.py"
description = plugin.describe("city_street_maintenance")
print(description.manifest.inputs)
```

Run the script again. The declaration names `lengths` and `conditions` as table dependencies. The block-to-street selection tells Lyra how to obtain locations. It does not add another table input to the maintenance metric.

## Test observed native inputs

Add this test to `tests/test_tutorial.py`. It checks that the block request supplied three streets and that the maintenance handler completed:

```python title="tests/test_tutorial.py" {16-23}
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
    native_counts = {
        item.name: item.count for item in provenance.execution.native_inputs
    }
    assert native_counts == {"streets": 3}
    assert any(
        item.node == "city_street_maintenance" and item.outcome == "completed"
        for item in provenance.execution.observations
    )
```

The result table checks what the calculation returned. This test separately checks which locations it used and whether the handler ran.

## Test a data-loading failure <span id="replace-a-failing-acquisition-boundary"></span>

A real data source can fail even when your request is valid. Add these imports and a fake provider to `tests/test_tutorial.py` so you can reproduce that failure without contacting a service:

```python title="tests/test_tutorial.py"
import pytest
from lyra.runtime.execution import GraphExecutionError
from lyra.contracts.errors import LocationAcquisitionError
from city_example.provider import CityLocationProvider


class FailingCityProvider(CityLocationProvider):
    def resolve(self, contract, ids):
        raise ValueError("Fixture source is unavailable")
```

Add a test that replaces the provider for this run using `resource_overrides`. Your plugin still registers the original provider; the override makes its data lookup fail during the test:

```python title="tests/test_tutorial.py" {4,17-21}
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

The assertions check that the error identifies the requested metric and retains the original provider failure. That information helps you distinguish a data-loading problem from a mistake in the calculation. Lyra preserves the underlying error through Python’s exception chaining.

Run all the tests you have written:

```sh
uv run pytest tests/test_first_metric.py tests/test_selected_metric.py \
  tests/test_maintenance_metric.py tests/test_tutorial.py
```

These tests read bundled files or raise errors locally. Keep the project’s service-access guard enabled so tests cannot accidentally contact a real service. [Plugin testing](../../testing/) covers more ways to check data loading and result serialization. Continue to [package your plugin](../package/) and inspect its saved manifest.
