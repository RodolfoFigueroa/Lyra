# Integrated storage baseline

Run `uv run python -m benchmarks.schema_storage.run` from the repository root,
separately from tests. Raw samples and environment versions are in
[integrated_baseline_results.json](integrated_baseline_results.json). No timing
thresholds apply. Production storage is unchanged.

The declared fixtures in `benchmarks/schema_storage/fixtures.py` cover nullable
8/64-bit signed integers, unsigned 64-bit boundaries, 32/64-bit floats, signed
zero, booleans, strings, UTC microsecond timestamps, Arrow dates and ordered
categories. Ordinary and spatial frames include populated, empty, all-null,
shuffled-column and incidental-index cases. Named outputs pair both frame kinds.
The dtype-invalid test is separate from valid fixtures.

All ten metric-cache fidelity cases passed. All five location cases failed:
nonempty exports use object properties; empty exports omit declared properties.
These cases are observations, excluded from location speed comparisons. They
must not be simplified to claim schema fidelity. The location performance
workload instead reuses the existing independent rich scalar/geometry contract
from `benchmarks.location_baseline.acceptance`, checking exact contents outside
measured intervals. It includes chain, fanout and shared dependencies, fresh
execution, cold publication, warm restoration and validation, intermediate hits,
revision invalidation and deliberately corrupt cache recovery. Corruption logs
are expected during the campaign.

Three repetitions measure 100 and 1,000 location rows. Representative medians
at 1,000 rows in this run (milliseconds): construction 15.53; validation 28.21;
fingerprint computation 0.81; cache write 1.05; cache read 3.33; native frame
export 8.37; feature export 1.01; uncached chain execution 132.89. Selection of
100 ordered IDs from 1,000 is recorded separately. Metric boundary samples use
three rows of the full declared fixture and measure validation, locked write,
and read plus current declaration validation, after an exact round-trip gate.
These tiny samples establish overhead, not throughput claims.

Artifact byte counts and cumulative process RSS high-water marks are recorded
by the reused location harness. RSS includes setup and previous operations;
it is not isolated operation allocation or incremental peak memory. Warm file
cache and allocator reuse affect these same-process samples. Construction
combines ownership, ingestion and fingerprinting; validation may rebuild data;
cache writes include conversion and publication. Only explicit fingerprint
computation is isolated. Graph timings include validation and execution; do not
subtract boundary timings to infer isolated costs.

Phase 02 can import `declarations`, `frame` and `named_outputs` directly. Current
index retention and column ordering are recorded without imposing future
normalization behavior. Re-run this harness against the implementation as it
changes; no old backend is retained. The separate codec comparison remains
codec evidence, not an integrated performance comparison.
