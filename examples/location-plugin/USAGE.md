# Run the location examples

Use the in-memory city fixtures to explore named editions, location selection, caching, and Python-client requests. Run the examples from the repository root using the existing project environment:

```sh
PYTHONPATH=examples/location-plugin uv run python -m location_plugin.capabilities
uv run pytest tests/test_location_examples.py tests/test_capability_examples.py
```

## Compare metric editions

`capabilities.create_plugin()` reuses the city geometry and membership fixtures. It registers the `street_rating` family with two named editions: `standard` and `double`. Both return one row per selected street; each edition fixes the multiplier applied to the independently selected street context. The private calculation receives native inputs through its concrete dependency.

## Follow location selection

The local entry point selects blocks for `streets` and a native street for `surroundings`. It returns a pandas table and inspects native-input summaries separately. Both `member_streets` and `alternate_member_streets` offer canonical routes; the declared tie preference selects `member_streets`.

The original `graph.py` demonstrates explicit noncanonical dependencies and weighted block aggregation. The capabilities example’s `block_count` metric has one implicit edition and accepts native blocks. Selecting `b-empty`, an existing block with no related streets, produces a valid empty intermediate selection.

## Understand revision coverage

`BLOCK_DATA.data_revision` covers block geometry and membership. Membership is a private dependency with a block input, so it inherits that revision. Street data has no declared revision; its observation period and release describe the source without establishing freshness.

Use `cache_revision` for external changes or deliberate recomputation. Repeating its value permits cache reuse when the other cache inputs match. Earlier cache namespaces remain available.

## Exercise the client without services

`remote.street_rating(client, edition="double")` searches the catalog, inspects the selected edition, submits a job, polls, and retrieves the result. `test_remote_consumer_with_in_process_transport` runs that function with a fake transport backed by the same plugin and validates real response models.

The fake returns queued, running, and succeeded statuses without a network connection, credentials, or worker service. The original example tests separately cover submission and worker execution with fake job infrastructure. Keep the repository’s service-access guard enabled in all example tests.
