# 04 · Route and discover metrics by provider ID

A route connects a source provider to a target provider through canonical operations. Use provider IDs at both ends in graph compilation, planning, and serialized route descriptions. Remove routing fields for collection, source, unit type, and geographical edition, along with per-input `source_bindings` and their resolver. SDK route preferences still choose between equally short paths.

## What clients can discover

For each target provider, compile a sorted list containing itself and every provider that can reach it through canonical operations in the active catalog. Publish the list alongside its record schema, CRS, and query and membership capabilities.

Detailed metric inspection gives each `location_inputs` entry a `target_provider`. For a caller-supplied input, the included target provider description supplies accepted sources. An explicitly operation-derived input also exposes `operation` and its immediate `source_provider`. Derive these fields from SDK declarations and compiled routes. Its correspondence rows represent distinct `(source_location_id, location_id)` pairs. Compact metric summaries expose `accepted_selections`, mapping each caller-supplied input name to its target's accepted sources.

## Listing metrics

HTTP and MCP use one discovery operation that counts all matching public metrics and copies just the requested page of summaries, ordered by metric name. Both reject negative offsets. HTTP keeps its maximum page size of 100 and returns a list with the catalog fingerprint in `ETag`. Replace the versioned cursor on `lyra_list_metrics` with a nonnegative `offset` starting at zero. MCP caps pages at 20 and returns `metrics`, `total_count`, `catalog_fingerprint`, and `next_offset`. The last page has a null next offset.

At or beyond the match count, HTTP returns 200 with `[]` and the current `ETag`. MCP returns `metrics=[]`, `next_offset=null`, the actual count, and the current fingerprint. This includes offset zero when nothing matches. A catalog change between pages can skip or repeat a metric. Callers requiring a consistent inventory can compare fingerprints and restart at zero.

## Computation identity

Dataset scope includes provider ID and code revision, origin IDs and revisions, and any applicable intervention identity. Exercise a changed origin revision that preserves routing and changes affected keys, and an editorial description change that preserves reuse. In `compile_computation`, use contract strings locally to construct node hashes. Remove the unused returned `contracts` and `versions` mappings and the distribution lookups used only to populate them. Admin responses still obtain the installed versions they display.

Update routing fixtures, discovery clients, examples, and plugin authoring references to use provider IDs. Verify multi-hop reachability, ties resolved by SDK preferences, accepted selections, and pagination boundaries through the public interfaces.
