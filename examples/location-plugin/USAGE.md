# Run the location examples

Use the in-memory city fixtures to explore concrete metrics, location selection, caching, and Python-client requests. Run the examples from the repository root using the existing project environment:

```sh
PYTHONPATH=examples/location-plugin uv run python -m location_plugin.capabilities
uv run pytest tests/test_location_examples.py tests/test_capability_examples.py
```

## Compare concrete metrics

`capabilities.create_plugin()` reuses the city geometry and membership fixtures. It registers two metrics: `street_rating_standard` and `street_rating_double`. Both return one row per selected street. Each counts the streets in `surroundings` and multiplies that count by a fixed factor: 1 for standard, 2 for double.

Each public metric depends on a private calculation. Lyra passes the resolved street inputs to that dependency.

## Follow location selection

The local entry point selects blocks for `streets` and a native street for `surroundings`. It returns a pandas table and inspects native-input summaries separately. Both `member_streets` and `alternate_member_streets` offer canonical routes; the declared tie preference selects `member_streets`.

The original `graph.py` demonstrates explicit noncanonical dependencies and weighted block aggregation. The capabilities example’s `block_count` metric accepts native blocks. Selecting `b-empty`, an existing block with no related streets, produces a valid empty intermediate selection.

## Understand revision coverage

`BLOCK_DATA.data_revision` covers block geometry and membership. Membership is a private dependency with a block input, so it inherits that revision. Street data has no declared revision; its observation period and release describe the source without establishing freshness.

Use `cache_revision` for external changes or deliberate recomputation. Repeating its value permits cache reuse when the other cache inputs match. Earlier cache namespaces remain available.

## Exercise the client without services

Call `remote.street_rating(client, metric="street_rating_double")` to run the consumer workflow:

1. Search the catalog and inspect the selected metric.
2. Submit a job and poll its status.
3. Retrieve the result.

The example test runs this function with a fake transport backed by the same plugin. It validates the responses against the client’s response models.

The fake returns queued, running, and succeeded statuses without a network connection, credentials, or worker service. The original example tests separately cover submission and worker execution with fake job infrastructure. Keep the repository’s service-access guard enabled in all example tests.
