# 09 · Use one cache decision per metric invocation

A scheduled metric should use the cache decision that determined which inputs to acquire. Share partition lookup with direct execution of prepared inputs. When the scheduler has already identified misses, pass those misses to execution. A direct call with no prior decision performs its own lookup.

## Prerequisites for a lookup

Validate every submitted intervention. Reuse a matching saved validation if available. Otherwise ask the base provider to verify that added IDs are absent and removed IDs exist. Resolve the location IDs needed to identify metric partitions. When a routing operation supplies those IDs, reuse saved source-to-target mappings and run the operation for missing mappings. Both obligations apply even when the eventual metric lookup is a full hit.

| Lookup outcome | Execution behavior |
| --- | --- |
| Full hit | Return the retained partitions. Skip the metric handler, remaining dependencies, and acquisition needed solely to compute its outputs. |
| Partial hit | Acquire the missing partitions' inputs, including shared inputs. Compute those partitions, validate each fresh frame once, and combine fresh and retained output once. |
| Miss | Execute the requested partitions with their required inputs and validate their fresh output. |

For retained partitions, verify output names, partitions, applicable location IDs, completion row counts, and WKB integrity. Remove Pandera revalidation of cached outputs. Recheck each key inside the publication transaction to handle concurrent misses. For a previously committed key, verify header, registrations, partitions, and counts against the stored completion and compare input bindings. Conflicts fail the transaction. Matching retries keep the first committed rows.

After a successful publication, downstream metrics and the local return use the validated handler output for partitions this invocation computed. Plugin authors must produce the same logical values for a given computation identity. If concurrent executions violate that contract, the current run may observe different values from a later retained read.

## Per-location validation

By choosing `cache="per_location"`, the author guarantees that adding or removing other primary locations preserves each partition's values and the validity of the output. A batch of cache misses must satisfy the declared checks on its own. Combining its output with retained partitions must also satisfy those checks. Lyra relies on this guarantee when it assembles the result.

For example, uniqueness of `(location_id, row_key)` is preserved when combining valid partitions for distinct locations. If two locations can produce the same `code` and the metric must reject a selection containing both, use `cache="full"` so the uniqueness check sees the complete selection on a cache miss.

## Partition keys and verification

Snapshot mutable descriptor fields once and use the canonical `ComputationIdentity.key` encoder with each partition's inputs and scope. Keep the per-invocation memo of completed keys. Remove the fragment encoder in `runtime/planning/bindings.py`. The canonical encoder processes stable parameters on each partition's first lookup, so compare the implementations on the existing large-partition workload before shipping. [Benchmark guide](../benchmarks/USAGE.md#measure-partition-reuse-and-hybrid-graphs)

Verify byte-for-byte compatibility against the fixed hash vectors and mutate a nested parameter before the first key lookup to exercise snapshot isolation. Exercise cold, warm, and partial runs through scheduled and prepared paths. Include a full metric hit that still needs intervention validation or a missing correspondence mapping, and a concurrent miss resolved during publication.
