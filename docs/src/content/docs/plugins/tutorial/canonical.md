---
title: Accept block selections for street calculations
description: Let callers select blocks and receive maintenance results for their streets.
contentType: Tutorial
contentPlan: plugins/tutorial/canonical
sidebar: { label: "6. Accept Block Selections" }
---

Someone reviewing a neighborhood may know the block IDs but not the IDs of every street within them. You’ll let that person request your street maintenance report using blocks, while still receiving one result per street. Start with [your block calculation](../blocks/) and its passing tests; you’ll reuse its street-selection operation.

## Offer your selector for public selection

With [canonical selection](../../canonical-selection/), Lyra automatically converts the caller’s selection into the locations your metric needs. In `city_example/tutorial.py`, set `canonical=True` in the `streets_for_blocks` decorator. This makes the operation available when Lyra needs to turn a public block selection into streets. Replace the decorator with:

```python title="city_example/tutorial.py" {4}
@location_operation(
    code_revision="1",
    name="city_streets_for_blocks",
    canonical=True,
    description="Select each street belonging to any supplied block once.",
    inputs={"blocks": LocationDependency(provider=BLOCKS)},
    target_provider=STREETS,
)
```

Keep the handler immediately after its decorator. It returns target IDs grouped by source block:

```python title="city_example/tutorial.py" {2-7}
def streets_for_blocks(blocks: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
    return {
        block.location_id: tuple(
            entry["id"] for entry in json.loads(block.street_records)
        )
        for block in blocks.itertuples(index=False)
    }
```

The operation accepts block records and returns street IDs grouped by block. Lyra resolves the street records, so callers need no extra arguments. Keep your factory’s `location_operations=[streets_for_blocks]` registration and its existing metrics.

The street metrics can now accept block IDs and return the streets belonging to those blocks. The block summary still uses the operation through its own dependency declaration, then applies membership weights to calculate totals.

## Run your street metric with block IDs

Create `canonical_request.py` in the project root. Import your factory from the tutorial module:

```python title="canonical_request.py" {9-10}
from lyra.runtime.local import LocalRunner
from city_example.tutorial import create_plugin

runner = LocalRunner.from_definitions({"city": create_plugin()})
tables = runner.run(
    "city_street_maintenance",
    inputs={
        "streets": {
            "provider": "city_blocks",
            "ids": ["block-1", "block-2"],
        }
    },
).tables
print(tables["maintenance"].to_dict("records"))
```

Run `uv run python canonical_request.py`. The supplied reference command is `uv run python -m city_example 6`. The result contains `street-1`, `street-2`, and `street-3`, each once, with maintenance lengths 100 m, 0 m, and 300 m. The shared `street-2` appears once because this request asks for streets, not a separate allocation to each block.

## Test the preserved output meaning

Add this check to `tests/test_tutorial.py` to confirm that selecting two blocks returns their combined street list without duplicate rows:

```python title="tests/test_tutorial.py" {12-17}
def test_block_selection_returns_street_rows():
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

Add a request for `block-empty` to check what happens when the selection finds no streets. The street metrics still run and must return an empty table with the declared columns and types:

```python title="tests/test_tutorial.py" {12-19}
def test_empty_block_returns_typed_empty_streets():
    runner = LocalRunner.from_definitions({"city": create_plugin()})
    table = runner.run(
        "city_street_maintenance",
        inputs={
            "streets": {
                "provider": "city_blocks",
                "ids": ["block-empty"],
            }
        },
    ).tables["maintenance"]
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
| `city_street_maintenance` with block selections | One row per street across the selected blocks |
| `city_block_maintenance` with block selections | One row per block, with its share of street workload |

Choosing blocks changes how Lyra finds the streets; it does not change what the street metric returns. To get block totals, request the block metric that performs that calculation. Read [canonical selection rules](../../canonical-selection/) for which operations qualify and how Lyra handles equally short conversion paths, or continue to [inspect and test your graph](../testing/).
