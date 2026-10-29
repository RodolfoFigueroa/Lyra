---
title: Derive locations inside a graph
description: Select streets for block calculations and construct internal locations from tables.
contentType: Tutorial
contentPlan: plugins/location-operations
sidebar: { label: "Derive Locations Inside A Graph" }
---

Build and run a block calculation that derives its street inputs internally. You need the repository checkout and Python environment from [Install Lyra for Python](../../start/installation/). This tutorial uses in-memory fixtures from `examples/location-plugin/location_plugin/graph.py`, including `CityProvider`, and requires no external services.

[Providers](../location-providers/) resolve exact IDs, operations return `Locations`, and metrics produce tables. You’ll connect these components to select streets for each block.

## Declare a selection operation

Create `derived_example.py` at the repository root. Import the fixture schemas, provider, and table producers alongside the SDK declarations:

```python title="derived_example.py"
import pandas as pd
from lyra.sdk import (
    Dependency,
    Input,
    LocalRunner,
    LocationContext,
    LocationDependency,
    Locations,
    PluginDefinition,
    location_operation,
    metric,
)
from location_plugin.graph import (
    CityProvider,
    SHAPES,
    SCORES,
    membership,
    street_score,
)
```

`SHAPES` declares string `id` and geometry columns, with descriptions and explicit null units. Every operation requires an `output` Pandera schema or model. Use `LocationOutputSchema` for custom ID or geometry column names. Every provider scope also requires a schema.

Append an operation that discovers street IDs from an ordinary relationship table, then resolves them through the managed context:

```python title="derived_example.py"
@location_operation(
    name="select_streets",
    description="Resolve streets belonging to selected blocks.",
    output=SHAPES,
    inputs={"members": Dependency("membership")},
)
def select_streets(
    blocks: Locations,
    members: pd.DataFrame,
    context: LocationContext,
) -> Locations:
    ids = members.loc[members.block_id.isin(blocks.ids), "street_id"].tolist()
    return context.resolve_locations("city:entities", "street", ids)
```

The resolver deduplicates IDs in first-occurrence order and returns fully resolved locations in that order. An empty lookup validates the declared scope and schema without acquiring the provider. Operations may return empty locations when their schema and consumers permit them.

`LocationContext` combines reporting with resolution. Operations can use `RunContext` for reporting alone or omit context. Metrics use `RunContext`. Initial selections and operation lookups share one resolver and managed resource scope during local or worker execution.

## Bind derived streets

The fixture `street_score(streets, surroundings)` returns one row per result street. Its score counts supporting streets. A direct request supplies both selections; broader `surroundings` changes scores without expanding result IDs.

Append the block metric’s declaration. Both street arguments use the same operation invocation, so execution reuses the validated result:

```python title="derived_example.py"
selected = LocationDependency(
    "select_streets", locations={"blocks": Input("blocks")},
)

@metric(
    name="block_score",
    description="Aggregate derived street scores with membership weights.",
    inputs={
        "scores": Dependency(
            "street_scores",
            locations={"streets": selected, "surroundings": selected},
        ),
        "members": Dependency("membership"),
    },
    outputs={"block_scores": SCORES},
)
```

Append the function immediately after its decorator. Membership and weights remain visible table dependencies; the output population comes from `blocks`:

```python title="derived_example.py"
def block_score(
    blocks: Locations,
    scores: pd.DataFrame,
    members: pd.DataFrame,
) -> pd.DataFrame:
    joined = members.merge(scores, left_on="street_id", right_on="id")
    totals = (joined.score * joined.weight).groupby(joined.block_id).sum()
    return pd.DataFrame(
        {
            "id": pd.Series(blocks.ids, dtype="str"),
            "score": pd.Series(
                [float(totals.get(key, 0.0)) for key in blocks.ids],
                dtype="float64",
            ),
        }
    )
```

Every `Input` nested in this metric’s declaration refers to `block_score`’s effective inputs. The nested selection reads `block_score`’s `blocks` argument, even though `street_score` has no such argument. When `select_streets` evaluates its own dependencies, those declarations use the operation’s effective inputs.

Explicit bindings override matching-name forwarding. Ordinary parameters then fall back to the receiving node’s defaults. Use `Input("parameters.year")` for an owner’s parameter field; literal parameter values replace whole fields. See [dependency binding rules](../dependencies/) for nested examples.

## Run public ID selections

Register the provider, operation, and metrics separately. Append this local request, which exposes only the block metric’s own input:

```python title="derived_example.py"
plugin = PluginDefinition(
    metrics=[membership, street_score, block_score],
    location_operations=[select_streets],
    resources={"city": CityProvider},
    location_provider="city",
)
runner = LocalRunner.from_definitions({"city_example": plugin})
tables = runner.run(
    "block_score",
    {
        "blocks": {
            "collection": "city:entities",
            "unit_type": "block",
            "ids": ["b1", "b2", "b-empty"],
        },
    },
)
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

Public selections require nonempty string ID lists. The `b-empty` fixture resolves to a block with no streets, so its aggregate returns zero.

Server requests use the same metric and input object. The server validates request structure at submission; workers resolve IDs during execution. If resolution or resource acquisition fails, the error identifies the node and input.

## Convert tables and construct entities

Use `Locations.from_geodataframe(frame, collection=..., unit_type=..., id_column=...)` to convert a GeoDataFrame into locations. Specify a string ID column and ensure the frame has active geometry and a known coordinate reference system (CRS). Select columns before conversion: columns other than ID and geometry become properties.

Conversion preserves row order and normalizes pandas and NumPy scalars and missing properties to JSON-compatible values. If a value is unsupported, the error identifies its column.

`locations.to_geodataframe(id_column="id", geometry_column="geometry")` returns an independent frame with properties and CRS. Column names must not collide. Empty values retain scope and CRS; source indexes and exact dtypes need not round-trip. Validation reconstructs schema data types, checks missing and unexpected columns, and applies declared coercion to returned locations.

IDs must be unique nonempty strings within a collection and unit type. Geometry coordinates must be finite and CRS known. Treat shared values as read-only. Plugins own reprojection, geometry repair, topology checks, and spatial algorithms.

The fixture operation `sites_from_table` converts `site_coordinates` rows to points in `analysis:sites`. Its `site_buffers` operation projects geometry and constructs ten-metre buffers in `analysis:buffers`. Selection preserves entity scope; construction uses a distinct scope because buffers represent different entities.

The fixture `site_coverage` metric binds those operations recursively:

```python
areas = Dependency(
    "buffer_areas",
    locations={
        "buffers": LocationDependency(
            "site_buffers",
            locations={"sites": LocationDependency("sites_from_table")},
        ),
    },
)
```

The metric accepts an empty public input object. You cannot request its internal sites and buffers through a provider, even when Lyra caches them. Provider preparation uses configured resources without invoking the graph; construction from graph tables belongs in an operation.

## Inspect and test the graph

Inspect the existing fixture plugin and export both table and location edges:

```sh
uv run lyra-plugin describe --project examples/location-plugin --json
uv run lyra-plugin diagram --project examples/location-plugin --detailed
```

Descriptions and manifests expose operation schemas and bindings without acquiring resources. Diagram location edges include receiving arguments; detailed nodes include operation output metadata. Regenerate manifests after changing declarations.

Use [isolated plugin tests](../testing/) for direct handlers and orchestration. `tests/test_location_examples.py` exercises these fixtures through local execution and a fake worker, including empty results, reuse, context, and constructed scopes. Read [graph execution and caching](../../concepts/graphs/) before enabling persistent reuse.
