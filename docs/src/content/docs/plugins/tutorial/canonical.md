---
title: Accept block selections for street calculations
description: Offer canonical selection while preserving the street metric's output granularity.
contentType: Tutorial
contentPlan: plugins/tutorial/canonical
sidebar: { label: "6. Accept Block Selections" }
---

Let callers choose blocks when requesting your existing street calculation. Start with [your block graph](../blocks/) and its passing tests. This chapter changes the selector’s registration while preserving the metric’s street-level output.

## Offer your selector for public selection

In `city_example/tutorial.py`, change `canonical=False` to `canonical=True` in the `streets_for_blocks` decorator. The complete replacement is:

```python title="city_example/tutorial.py" {3}
@location_operation(
    name="city_streets_for_blocks",
    canonical=True,
    description="Select each street belonging to any supplied block once.",
    locations={"blocks": BLOCKS},
    inputs={
        "membership": Dependency(
            "membership",
            producer="city_street_membership",
        ),
    },
    output=STREET_SCHEMA,
    output_contract=STREETS,
)
def streets_for_blocks(
    blocks: Locations, membership: pd.DataFrame, context: LocationContext
) -> gpd.GeoDataFrame:
    del blocks
    ids = list(dict.fromkeys(membership.street_id))
    return context.resolve_locations(
        "example:city",
        "street",
        ids,
    ).to_geodataframe()
```

The operation has one location input and one location output. Its dependency resolves from the block input without extra caller arguments. Keep your factory’s `location_operations=[streets_for_blocks]` registration and its existing metrics.

The street metrics now accept block selections through this route. Your block summary retains its explicitly declared methodological dependency.

## Run your street metric with block IDs

Create `canonical_request.py` in the project root. Import your factory from the tutorial module:

```python title="canonical_request.py" {10-11}
from lyra.sdk import LocalRunner
from city_example.tutorial import create_plugin

runner = LocalRunner.from_definitions({"city": create_plugin()})
tables = runner.run(
    "city_street_maintenance",
    {
        "streets": {
            "collection": "example:city",
            "unit_type": "block",
            "ids": ["block-1", "block-2"],
        },
    },
)
print(tables["maintenance"].to_dict("records"))
```

Run `uv run python canonical_request.py`. The supplied reference command is `uv run python -m city_example 6`. The result contains `street-1`, `street-2`, and `street-3`, each once, with maintenance lengths 100 m, 0 m, and 300 m.

## Test the preserved output meaning

Append a union-selection check to `tests/test_tutorial.py`:

```python title="tests/test_tutorial.py" {13-18}
def test_block_selection_returns_street_rows():
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

Append an empty canonical-selection case. Downstream handlers still run and must return a valid empty table:

```python title="tests/test_tutorial.py" {13-20}
def test_empty_block_returns_typed_empty_streets():
    runner = LocalRunner.from_definitions({"city": create_plugin()})
    table = runner.run(
        "city_street_maintenance",
        {
            "streets": {
                "collection": "example:city",
                "unit_type": "block",
                "ids": ["block-empty"],
            }
        },
    )["maintenance"]
    assert table.empty
    assert table.columns.tolist() == [
        "street_id",
        "length_m",
        "condition_score",
        "needs_maintenance",
        "maintenance_length_m",
    ]
```

Run `uv run pytest tests/test_tutorial.py`. The previous block tests also stay valid:

| Request | Result |
| --- | --- |
| `city_street_maintenance` with block selections | A union of street rows |
| `city_block_maintenance` with block selections | Explicitly weighted block summaries |

Canonical selection supplies native inputs. Aggregation requires a calculation that declares and produces the aggregate. Read [canonical selection rules](../../canonical-selection/) for eligibility and route ties, or continue to [inspect and test your graph](../testing/).
