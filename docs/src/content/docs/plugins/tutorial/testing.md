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

```python title="inspect_tutorial.py" {8-14}
from lyra.runtime.local import LocalRunner
from city_example.tutorial import create_plugin

plugin = create_plugin()
runner = LocalRunner.from_definitions({"city": plugin})
result = runner.run(
    "city_street_maintenance",
    inputs={
        "streets": {
            "provider": "city_blocks",
            "ids": ["block-1", "block-2"],
        }
    },
)
tables = result.tables
print(tables["maintenance"].to_dict("records"))
```

Add this inspection to the same file to read the attached [execution report](../../../concepts/provenance/). It shows your accepted request and the work Lyra observed:

```python title="inspect_tutorial.py" {1,4-7}
report = result.report
print(result.metadata.job_id)
print(report.request.inputs["streets"])
for node in report.nodes:
    print(node.use_id, node.node_id, node.outcome)
    if node.operation is not None:
        print(node.operation.target_scope.provider_id, node.operation.mapping)
```

Run `uv run python inspect_tutorial.py`. The request records your two selected block IDs, and the operation’s target identifies the street provider. The node bindings describe how Lyra converts those blocks into streets and supplies tables to the maintenance calculation.

Save the store path and job ID to [reopen the report and tables](../../../use/results/#reopen-a-local-result) later. You can inspect `result.report` directly during this run.

To count the input records or diagnose the latest attempt, call `runner.inspect()`. These transient diagnostics describe the latest run and can include observations from a failed attempt:

```python title="inspect_tutorial.py" {1,6-9}
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

The native input summary counts four block/street relationships covering three distinct streets. The shared street appears under both source blocks, while the maintenance output contains three street rows.

Diagnostics use labels such as `completed` and `cache_hit`. The saved report summarizes each use with [four outcomes](../../../concepts/provenance/#interpret-the-four-outcomes). A declared plan alone doesn’t prove that a function ran.

A later attempt replaces `runner.inspect()` diagnostics. Your existing result keeps its own report.

## Inspect declared dependencies

To see what your plugin declares before considering a particular run, add this inspection to `inspect_tutorial.py`:

```python title="inspect_tutorial.py"
description = plugin.describe("city_street_maintenance")
print(description.manifest.inputs)
```

Run the script again. The declaration names `lengths` and `conditions` as table dependencies. The block-to-street selection tells Lyra how to obtain locations. It does not add another table input to the maintenance metric.

## Test observed native inputs

Add this test to `tests/test_tutorial.py` to check the saved request and successful execution. The helper runs the block selection and returns its result with the latest diagnostics:

```python title="tests/test_tutorial.py"
def run_block_selection():
    runner = LocalRunner.from_definitions({"city": create_plugin()})
    result = runner.run(
        "city_street_maintenance",
        inputs={
            "streets": {
                "provider": "city_blocks",
                "ids": ["block-1", "block-2"],
            }
        },
    )
    return result, runner.inspect()
```

Use the helper to verify that the report identifies the result and preserves your block selection:

```python title="tests/test_tutorial.py" {4-5,8}
def test_report_preserves_block_selection():
    result, _ = run_block_selection()
    report = result.report
    assert report.job_id == result.metadata.job_id
    assert report.request.inputs["streets"].ids == ["block-1", "block-2"]
    root_id = report.root_use_id
    root = next(node for node in report.nodes if node.use_id == root_id)
    assert root.outcome == "executed"
```

Add a separate check for the four input relationships and the completed maintenance handler:

```python title="tests/test_tutorial.py" {8-12}
def test_inspection_reports_native_streets():
    _, provenance = run_block_selection()
    assert provenance is not None
    assert provenance.accepted is not None
    native_counts = {
        item.name: item.count for item in provenance.execution.native_inputs
    }
    assert native_counts == {"streets": 4}
    assert any(
        item.node == "city_street_maintenance" and item.outcome == "completed"
        for item in provenance.execution.observations
    )
```

The table assertions from earlier chapters check what the calculation returned. These tests check the saved request, observed input relationships, and handler execution.

## Test a data-loading failure <span id="replace-a-failing-acquisition-boundary"></span>

A real data source can fail even when your request is valid. Add these imports and a fake provider to `tests/test_tutorial.py` so you can reproduce that failure without contacting a service:

```python title="tests/test_tutorial.py"
import pytest
from lyra.runtime.execution import GraphExecutionError
from lyra.contracts.errors import LocationAcquisitionError
from city_example.provider import CityStreetProvider


class FailingCityProvider(CityStreetProvider):
    def resolve(self, ids):
        raise ValueError("Fixture source is unavailable")
```

Add a test that replaces the provider for this run using `resource_overrides`. Your plugin still registers the original provider; the override makes its data lookup fail during the test:

```python title="tests/test_tutorial.py" {4,16-20}
def test_acquisition_failure_identifies_the_root():
    runner = LocalRunner.from_definitions(
        {"city": create_plugin()},
        resource_overrides={"city_streets": FailingCityProvider},
    )
    with pytest.raises(GraphExecutionError) as error:
        runner.run(
            "city_street_maintenance",
            inputs={
                "streets": {
                    "provider": "city_streets",
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

These tests read bundled files or raise errors locally. Keep the project’s service-access guard enabled so tests cannot accidentally contact a real service. [Plugin testing](../../testing/) covers more ways to check data loading and result serialization. Continue to [package your plugin](../package/) and inspect its live definitions.
