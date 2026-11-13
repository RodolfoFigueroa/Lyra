---
title: Derive locations inside a graph
description: Map blocks to street IDs and resolve fixed target-provider records.
contentType: Tutorial
contentPlan: plugins/location-operations
sidebar: { label: "Derive Locations Inside A Graph" }
---

Build and run a block calculation that derives its street inputs internally. You need the repository checkout and Python environment from [Install Lyra for Python](../../start/installation/). This tutorial uses in-memory fixtures from `examples/location-plugin/location_plugin/graph.py`, including `BlockProvider` and `StreetProvider`, and requires no external services.

[Providers](../location-providers/) resolve exact IDs, operations return source-to-target ID mappings, and metrics produce tables. You’ll connect these components to select streets for each block.

## Declare a selection operation

Create `derived_example.py` at the repository root. Import the fixture schemas and producers alongside the SDK declarations:

```python title="derived_example.py"
import geopandas as gpd
import pandas as pd
from lyra.runtime.local import LocalRunner
from lyra.sdk import (
    Dependency,
    Input,
    LocationDependency,
    PluginDefinition,
    location_operation,
    metric,
)
from location_plugin.graph import (
    BLOCKS,
    STREETS,
    BlockProvider,
    StreetProvider,
    street_mapping,
    SCORES,
    membership,
    street_score,
)
```

The providers declare strict schemas for `location_id`, geometry, and dataset attributes. Lyra validates acquired target records against the target provider’s schema.

Write the operation callback with one positional source GeoDataFrame. If it needs parameters, add keyword-only `parameters` annotated with a `MetricParameters` subclass and define defaults on that model. The [operation signature reference](../../reference/parameters/#operation-callback-signatures) lists the callback constraints; metric handlers support `context: RunContext` for reporting.

The `select_streets` operation maps block IDs to street IDs. Each block’s attributes contain its street records, so the operation can extract their IDs without acquiring data:

```python title="derived_example.py" {5-6,8-9}
@location_operation(
    code_revision="1",
    name="select_streets",
    description="Select streets belonging to selected blocks.",
    inputs={"blocks": LocationDependency(provider=BLOCKS)},
    target_provider=STREETS,
)
def select_streets(blocks: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
    return street_mapping(blocks)
```

The operation returns a tuple of target IDs for each source ID. An empty tuple completes a source with no matches. When sources share a target, Lyra preserves each source/target pair, called a correspondence. Each forwarded row includes `source_location_id` and the target’s `location_id`.

## Bind derived streets

The fixture `street_score(streets, surroundings)` returns one row per result street. Its score counts supporting streets. A direct request supplies both selections; broader `surroundings` changes scores without expanding result IDs.

Append the block metric’s declaration. Both street arguments use the same operation invocation, so execution reuses the validated result:

```python title="derived_example.py" {1-3,13}
selected = LocationDependency(
    operation="select_streets",
).bind(blocks=Input("blocks"))

@metric(
    code_revision="1",
    name="block_score",
    description="Aggregate derived street scores with membership weights.",
    inputs={
        "blocks": LocationDependency(provider=BLOCKS),
        "scores": Dependency(
            "street_scores", producer="street_score",
        ).bind(streets=selected, surroundings=selected),
        "members": Dependency("membership", producer="street_membership"),
    },
    outputs={"block_scores": SCORES},
)
```

Append the function immediately after its decorator. Membership and weights remain visible table dependencies; the output population comes from `blocks`:

```python title="derived_example.py" {6-7,12}
def block_score(
    blocks: gpd.GeoDataFrame,
    scores: pd.DataFrame,
    members: pd.DataFrame,
) -> pd.DataFrame:
    joined = members.merge(scores, left_on="street_id", right_on="id")
    totals = (joined.score * joined.weight).groupby(joined.block_id).sum()
    return pd.DataFrame(
        {
            "id": pd.Series(blocks.location_id, dtype="str"),
            "score": pd.Series(
                [float(totals.get(key, 0.0)) for key in blocks.location_id],
                dtype="float64",
            ),
        }
    )
```

Every `Input` nested in this metric’s declaration refers to `block_score`’s effective inputs. Here, `Input("blocks")` supplies `block_score`’s blocks to `select_streets`, even though `street_score` has no `blocks` argument. An operation’s own bindings refer to that operation’s inputs.

Explicit bindings override matching-name forwarding. Ordinary parameters then fall back to the receiving node’s defaults. Use `Input("parameters.year")` for an owner’s parameter field; literal parameter values replace whole fields. See [dependency binding rules](../dependencies/) for nested examples.

## Run public ID selections

Register the provider, operation, and metrics separately. Append this local request, which exposes only the block metric’s own input:

```python title="derived_example.py" {10-16}
plugin = PluginDefinition(
    metrics=[membership, street_score, block_score],
    location_operations=[select_streets],
    resources={"blocks": BlockProvider, "streets": StreetProvider},
)
runner = LocalRunner.from_definitions({"city_example": plugin})
tables = runner.run(
    "block_score",
    inputs={
        "blocks": {
            "provider": "city_blocks",
            "ids": ["b1", "b2", "b-empty"],
        }
    },
).tables
assert tables["block_scores"].to_dict("records") == [
    {"id": "b1", "score": 4.5},
    {"id": "b2", "score": 4.5},
    {"id": "b-empty", "score": 0.0},
]
```

Run the script from the repository root with the fixture package on the import path:

```sh
PYTHONPATH=examples/location-plugin uv run python derived_example.py
```

Public selections contain a provider ID and a list of nonempty string IDs. The `b-empty` fixture resolves to a block with no streets, so its aggregate returns zero.

Server requests use the same metric and input object. The server validates request structure at submission; workers resolve IDs during execution. If resolution or resource acquisition fails, the error identifies the node and input.

## Resolve existing target records

Return IDs belonging to the fixed target provider. Lyra resolves its complete native records, preserving schema and scope even when all groups are empty. Missing target IDs fail acquisition.

The fixture’s `site_buffers` maps site IDs to existing ten-metre buffer records in `analysis_buffers`. The buffer provider owns the projected geometry; the operation only computes the mapping.

In `site_coverage`, forward the selected sites through that operation:

```python
areas = Dependency("buffer_areas", producer="buffer_area").bind(
    buffers=LocationDependency(operation="site_buffers").bind(
        sites=Input("sites"),
    ),
)
```

The public request selects site IDs from `analysis_sites`. Plugins own reprojection, geometry repair, topology checks, and spatial algorithms.

## Inspect and test the graph

Inspect the existing fixture plugin and export both table and location edges:

```sh
uv run lyra-plugin describe --project examples/location-plugin --json
uv run lyra-plugin diagram --project examples/location-plugin --detailed
```

Inspect the project after changing declarations to check operation schemas and bindings without acquiring resources. Diagram edges identify receiving arguments, and detailed nodes show operation output metadata.

Use [isolated plugin tests](../testing/) for direct handlers and orchestration. `tests/test_location_examples.py` exercises these fixtures through local execution and a fake worker, including empty results, reuse, context, and constructed scopes. Read [graph execution and caching](../../concepts/graphs/) before enabling persistent reuse.

## Offer canonical public selection

Use [Accept additional location types](../canonical-selection/) to opt selectors into automatic routing. Selection supplies native locations while the metric retains its declared output granularity.

## Choose a route when shortest paths tie

Declare a connected shortest-route preference as described in [route preferences](../canonical-selection/#choose-a-route-when-shortest-paths-tie).
