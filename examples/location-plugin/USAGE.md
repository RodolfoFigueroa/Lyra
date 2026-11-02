# Running the examples

From the repository root, use the existing project environment:

```sh
PYTHONPATH=examples/location-plugin uv run python -m location_plugin.capabilities
uv run pytest tests/test_location_examples.py tests/test_capability_examples.py
```

`capabilities.create_plugin()` reuses the original example's city geometry and
membership fixtures. It registers the `street_rating` family with explicit
`standard` and `double` editions. Both return one row per selected street; the
edition fixes the multiplier applied to the independently selected street context.
The private calculation receives native inputs through its concrete dependency.

The local entry point selects a batch of blocks for `streets` and a native street
for `surroundings`, returns a pandas table, and inspects native-input summaries
separately. `member_streets` wins the declared tie with
`alternate_member_streets`. Both operations opt into canonical routing; the
original `graph.py` demonstrates explicit noncanonical dependencies and weighted
block aggregation. `block_count` has one implicit edition and accepts native
blocks. `b-empty` is an existing block with no related streets and produces a
valid empty intermediate selection.

`BLOCK_DATA.data_revision` covers block geometry and membership. Membership is a
private dependency with a block input, so it inherits that revision. Street data
has no declared revision; its observation period and release describe the source,
not freshness. `cache_revision` supplies a run-wide namespace for external changes
and deliberate recomputation. Reusing its value allows cache reuse, and older
namespaces remain available.

`remote.street_rating(client, edition="double")` demonstrates Python-client
search, concrete edition inspection, submission, polling, and result retrieval.
`test_remote_consumer_with_in_process_transport` runs that function with a fake
transport backed by this same plugin and validates real response models. The fake
returns queued, running, and succeeded statuses without HTTP, credentials, or a
worker service. The original example tests separately cover submission and worker
execution with fake job infrastructure. All example tests retain the repository's
service-access guard.
