---
meta:
  contentType: How-to
---

<!-- Content plan
Overview: Execute the in-memory city examples to inspect location selection and results.
Goal: Run local examples and submit equivalent requests without live services.
Audience: Plugin authors working in the Lyra repository.
Content: Compare concrete metrics; follow selection; manage revisions; exercise a fake client; run hybrid summaries; route dependency inputs.
Open questions: None.
-->

# Run the location examples

Use the in-memory city fixtures to explore concrete metrics, location selection, caching, and Python-client requests. Run the examples from the repository root using the existing project environment:

```sh
PYTHONPATH=examples/location-plugin \
  uv run python -m location_plugin.capabilities
uv run pytest -n auto \
  tests/test_location_examples.py tests/test_capability_examples.py
```

## Compare concrete metrics

`capabilities.create_plugin()` reuses the city geometry and membership fixtures. It registers two metrics: `street_rating_standard` and `street_rating_double`. Both return one row per selected street. Each counts the streets in `surroundings` and multiplies that count by a fixed factor: 1 for standard, 2 for double.

Each public metric depends on a private calculation. Lyra passes the resolved street inputs to that dependency.

## Follow location selection

The local entry point selects blocks for `streets` and a native street for `surroundings`. It returns a pandas table and inspects native-input summaries separately. Both `member_streets` and `alternate_member_streets` offer canonical routes; the declared tie preference selects `member_streets`.

The original `graph.py` demonstrates explicit noncanonical dependencies and weighted block aggregation. The capabilities example’s `block_count` metric accepts native blocks. Selecting `b-empty`, an existing block with no related streets, produces a valid empty intermediate selection.

## Understand revision coverage

`CapabilityBlocks.code_revision` identifies the fixed block geometry and relationship logic. `CapabilityStreets.origins` declares the fixed city survey release. Provider records retain these scopes through selection and dependency execution.

Update an origin revision when its external contents change. Change a node ID or `code_revision` when implementation changes affect outputs, including changes to either operation callback. Return equal values for equal identities and effective parameters, even with caching off.

Local runs save final outputs automatically in `.lyra/results.sqlite3`. Keep `result.metadata.store_path` and `result.metadata.result_ref` together to [reopen those results](../../docs/src/content/docs/use/results.md#reopen-a-local-result).

## Exercise the client without services

Call `remote.street_rating(client, metric="street_rating_double")` to run the consumer workflow:

1. Search the catalog and inspect the selected metric.
2. Submit a job and poll its status.
3. Retrieve the result.

The example test runs this function with a fake transport backed by the same plugin. It validates the responses against the client’s response models.

The fake returns queued, running, and succeeded statuses without a network connection, credentials, or worker service. The original example tests separately cover submission and worker execution with fake job infrastructure. Keep the repository’s service-access guard enabled in all example tests.

## Run a hybrid summary

Use `hybrid.create_plugin()` to calculate block areas and summarize the selection. Save this script as `hybrid_summary.py` at the repository root. The request counts blocks meeting the area threshold and totals their projected area:

```python
from location_plugin.hybrid import create_plugin
from lyra.runtime.local import LocalRunner

runner = LocalRunner.from_definitions({"hybrid": create_plugin()})
result = runner.run_development(
    "area_summary",
    inputs={"blocks": {"provider": "city_blocks", "ids": ["b1", "b2"]}},
    parameters={"minimum_area": 100.0, "include_requested": False},
)
print(result.tables["total"])
print(result.tables["count"])
print(result.metadata.metric_name, result.metadata.parameters)
print(result.metadata.outputs["total"].scope)
```

Run the script with the example package on Python’s import path:

```sh
PYTHONPATH=examples/location-plugin uv run python hybrid_summary.py
```

The summary returns an area of 200 m² and a count of 2. Both outputs are unpartitioned, meaning the rows describe the complete selection. The `block_area` dependency assigns each measurement to a block, with the stable row key `area`.

The plugin also registers `street_relationships`, which retains both block relationships for shared street `s2`. It uses the source block ID as each relationship’s row key. Empty target groups complete successfully, and empty outputs retain their schemas and owning provider scopes.

## Route dependency inputs

Run a summary that converts selected blocks into streets before calculating their lengths:

```sh
PYTHONPATH=examples/location-plugin uv run python -m location_plugin.receiving
```

The `routed_street_summary` metric calls `street_lengths` with two selections:

- All requested blocks, bound directly to the street dependency
- Blocks selected by two nested `large_blocks` operations

The nested operations use typed `AreaParameters`: the first applies a zero threshold, and the second applies your `minimum_area`. Both operations return block IDs. Because `street_lengths` requires streets, Lyra then applies the canonical `member_streets` conversion.

The example returns 30&nbsp;m for all streets and 0&nbsp;m for blocks meeting the 101&nbsp;m² minimum. The calculation measures shared street `s2` once. In the request below, set `minimum_area` to `100.0` to include all three streets in both totals.

Empty block selections return zero totals with the same schema. The printed `RunResult.metadata` records the effective threshold and describes the output as unpartitioned: it summarizes the complete selection.

You can also run `street_lengths` directly with a block selection to use the same conversion at the public input. Selecting native streets requires no conversion. In both cases, Lyra chooses the shortest canonical route and uses declared preferences to resolve ties. Missing or ambiguous routes fail before acquisition.

Submit the summary using this JSON object as the HTTP `POST /jobs` body or Model Context Protocol (MCP) `lyra_run_metric` arguments:

```json
{
  "metric": "routed_street_summary",
  "inputs": {"blocks": {"provider": "city_blocks", "ids": ["b1", "b2"]}},
  "parameters": {"minimum_area": 101.0}
}
```

## Configure managed collections

Use `location_plugin.managed` to select trees from a Parquet file for blocks stored in PostGIS. The example declares providers and operations; register a consuming metric before creating a runner that executes calculations.

From the repository checkout, add `examples/location-plugin` to `PYTHONPATH` before importing the package. Importing and compiling the plugin requires neither external source. This snippet configures a runner for the example’s declarations:

```python
from lyra.runtime.local import LocalRunner
from location_plugin.managed import plugin, resource_config

runner = LocalRunner.from_definitions(
    {"managed_city": plugin}, resource_config=resource_config
)
```

Before executing a consuming metric, set the database credentials and file path in the example’s resource configuration. The [resource configuration guide](../../docs/src/content/docs/operate/configuration.md#configure-plugin-resources) describes settings and secret references.

Choose the operation that matches your calculation:

- `nearby_trees` filters trees by height and distance, then maps matches to individual blocks
- `assigned_trees` resolves the tree ID already recorded on each block

In your consuming metric’s `inputs` mapping, bind the operation’s `blocks` input to the metric’s own blocks:

```python
from lyra.sdk import Input, LocationDependency

selected_trees = LocationDependency(operation="nearby_trees").bind(
    blocks=Input("blocks")
)
```

For an unparameterized mapping handler, `select_all_trees` and `select_tall_trees` demonstrate alternative selectors. The [target selection reference](../../docs/src/content/docs/reference/target-selection.md) explains candidate membership and CRS rules, with callback examples.
