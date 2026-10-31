# Integrated storage acceptance and performance

Phase 14 completes the supported acceptance contract. No production format,
public API, dependency, or executor changes were needed. The added tests combine
the existing declared fixture across boundaries; the benchmark now normalizes
expected indexes and declaration order, constructs explicitly typed empty IDs,
keeps supported fidelity separate from category observations, and preserves the
phase 01 raw results by writing a distinct final results file.

## Acceptance evidence

| Requirement | Passing behavioral coverage |
| --- | --- |
| Nullable native types; empty/all-null columns; signed/unsigned boundaries; widths; signed zero; microsecond UTC timestamps; date-only values; index normalization; column and row order; spatial metadata | `test_integrated_storage_acceptance.py`: 15 cases through fresh locations, slices, reversed ID selections, standalone artifacts, cache reads, current schema validation, fresh metrics, cached dependencies, downstream consumers and final cache hits. Exact frame and zero-sign assertions use independent declarations. |
| Native precision and timezone variants; exact columns; nullability; coercion; infinity and out-of-range rejection; explicit index rejection | `test_arrow_conversion.py`, `test_pandera_contract.py`, `test_location_construction.py`, `test_location_ingestion.py` |
| Ownership and mutation isolation, immutable buffers, subset contracts | `test_typed_storage.py`, `test_location_snapshot.py`, `test_integrated_storage_acceptance.py` |
| Exact fingerprints, scalar types and bits, scope and geometry sensitivity, null payload normalization, chunk/slice independence | `test_content_fingerprints.py`, `test_typed_storage.py` |
| Integrity before current executable validation, invalid artifacts and cache recovery | `test_ipc_codec.py`, `test_location_artifacts.py`, `test_table_cache.py`, `test_location_boundary_reuse.py`, `test_location_cache.py` |
| Lazy inspection, selective decoding, bounded exports | `test_lazy_locations.py`, `test_result_inspection.py`, `test_buffer_validation_exports.py`, `test_artifact_reader.py` |
| Single geometry ingestion, snapshot reuse, no repeated cached hashing, intermediate hits and revision invalidation | `test_location_ingestion.py`, `test_location_boundary_reuse.py`, `test_location_performance_acceptance.py`, `test_cache_execution.py` |
| Local/worker mixed graphs, named outputs, provider and consumer behavior | `test_typed_application.py`, `test_worker_graph_integration.py`, `test_location_providers.py` |
| Basic native categories | `test_ipc_codec.py`, `test_table_cache.py`, `test_arrow_conversion.py`; nonempty benchmark observations are also required to pass. |

All 15 category-free fidelity observations pass. The category-inclusive campaign
passes all 12 nonempty observations. Its three empty observations (ordinary and
spatial metric cache, and location export) lose the declared category domain on
native reconstruction. This remains the explicitly excluded native empty-category
limitation. Full category comparison is retained in the raw observations; no
repair codec, skip, or weakened category equality was introduced. Required empty
fidelity for every other declared dtype passes independently.

The storage, codec, schema and cache implementations contain no unfinished
implementation placeholders or compatibility readers. Existing rejection tests
continue to reject unsupported protocols. No subsequent phase was started.

## Measurement method

Run `uv run python -m benchmarks.schema_storage.run` separately from checks.
[Final raw samples](integrated_acceptance_results.json) retain three repetitions
at 100 and 1,000 rows, the three-row metric boundaries, selection samples,
artifact bytes, environment versions, fidelity errors, and a separate diagnostic
pass. Compare with [phase 01 samples](integrated_baseline_results.json).
Python and pandas/Pandera/Arrow/GeoPandas versions match the recorded baseline.

The rich location workload preserves the same IDs, scalar values, row order and
mixed geometries, with untimed exact-content gates. Chain, fanout and shared graphs
exercise uncached execution, cold publication, full hits, intermediate hits,
revision invalidation and corrupt recovery. Corruption diagnostics are expected.
Phase 01 could not preserve the full declared location fixture: those failures
are fidelity observations, never an equivalent faster typed baseline. The rich
workload comparison measures the cost of adopting the stricter typed contract on
equivalent representable contents, not equal validation or hashing algorithms.

Each timing cell is median [minimum, maximum] milliseconds across three samples.
This small same-process campaign has warm filesystem caches and allocator reuse;
it provides observations, not confidence intervals or performance thresholds.
No codec-only result is used to claim end-to-end speedup.

### 100 location rows

| Operation | Phase 01 ms | Phase 14 ms |
| --- | ---: | ---: |
| construction | 2.53 [2.37, 2.56] | 11.11 [10.93, 11.11] |
| validation | 8.95 [8.81, 9.83] | 6.66 [6.59, 6.71] |
| fingerprint_compute | 0.33 [0.33, 0.33] | 3.55 [3.50, 3.63] |
| write | 0.59 [0.50, 0.61] | 0.38 [0.30, 0.39] |
| read | 1.39 [1.37, 1.41] | 4.77 [4.76, 4.80] |
| to_frame | 2.01 [1.99, 2.05] | 1.87 [1.83, 2.37] |
| features | 0.21 [0.21, 0.21] | 0.20 [0.20, 0.23] |
| chain (off) | 30.50 [30.05, 30.86] | 63.34 [42.47, 63.54] |
| chain (empty) | 33.75 [33.55, 36.35] | 47.84 [46.56, 47.86] |
| chain (populated) | 2.31 [2.30, 2.41] | 1.90 [1.87, 2.38] |
| chain (intermediate) | 29.63 [26.74, 49.63] | 30.77 [30.38, 31.21] |
| fanout (off) | 24.06 [23.46, 46.52] | 31.09 [30.73, 31.48] |
| shared (off) | 32.55 [31.90, 32.76] | 46.35 [45.27, 65.92] |

Location artifact bytes: 9578 → 20282.

### 1000 location rows

| Operation | Phase 01 ms | Phase 14 ms |
| --- | ---: | ---: |
| construction | 15.53 [14.07, 36.80] | 22.24 [22.17, 22.39] |
| validation | 28.21 [28.06, 28.74] | 14.65 [14.56, 14.70] |
| fingerprint_compute | 0.81 [0.78, 0.87] | 33.66 [33.30, 33.70] |
| write | 1.05 [0.91, 1.09] | 0.67 [0.67, 0.68] |
| read | 3.33 [3.32, 3.57] | 37.01 [36.89, 37.33] |
| to_frame | 8.37 [8.34, 8.50] | 3.27 [3.17, 3.33] |
| features | 1.01 [1.00, 1.12] | 2.07 [2.01, 2.08] |
| chain (off) | 132.89 [120.62, 144.44] | 161.87 [157.32, 162.95] |
| chain (empty) | 130.86 [129.41, 147.72] | 166.10 [164.46, 180.86] |
| chain (populated) | 2.51 [2.50, 2.53] | 2.04 [2.01, 2.12] |
| chain (intermediate) | 119.04 [111.57, 144.63] | 173.07 [152.55, 175.84] |
| fanout (off) | 161.84 [143.01, 170.08] | 174.67 [174.12, 176.21] |
| shared (off) | 175.96 [165.52, 195.16] | 222.16 [221.93, 223.54] |

Location artifact bytes: 26922 → 181203.

### Metric and memory observations

| Three-row metric boundary | Phase 01 median ms | Phase 14 median ms |
| --- | ---: | ---: |
| producer_validation | 0.93 | 0.96 |
| cache_write | 1.36 | 3.61 |
| cache_read_validation | 3.35 | 2.34 |

Metric directory bytes: 34816 → 14152. This includes retained publication generations and manifests, not one IPC payload.

Maximum cumulative process RSS in the 1,000-row campaign: 297.6 MiB → 192.8 MiB.
This high-water mark includes setup, previous operations and library allocation;
it is not isolated operation allocation or evidence of an equivalent reduction
in peak working memory.

## Cost interpretation and handoff

Construction combines schema preparation, ownership and geometry ingestion;
its JSON fixture adapter now constructs a declared native frame. Fingerprinting
is lazy, so construction and explicit fingerprint timing must not be treated as
the same cost split as phase 01. Validation includes full Pandera checks and
native frame reconstruction, but reuses the owned snapshot and geometry.
Metric writes combine conversion, ownership, hashing, IPC encoding and locked
publication. Reads combine artifact verification, reconstruction and hashing;
metric validated reads add the current Pandera declaration. Graph timings include
validation, execution and applicable cache operations. These measurements do not
isolate conversion from ownership or filesystem cost from codec cost.

Exact fingerprint computation and location reads regress materially. Inspection
of `content_fingerprints._typed_array` explains a likely source: canonical Arrow
scalar reconstruction recursively normalizes null payloads, slice offsets and
nested geometry before hashing. This is substantially more work than the prior
representation's fingerprint. The 1,000-row read cost tracks the independently
measured fingerprint cost. This attribution is supported by code inspection and
boundary timings, not a sampled CPU profile. Uncompressed artifacts also grow;
the format decision intentionally accepts larger files. Metric publication now
pays typed conversion and integrity costs. Fresh graphs and intermediate hits
therefore do not show a uniform improvement, despite faster validation and
native frame exports. Full root hits avoid dependency traversal and are cheaper
in this campaign.

Diagnostic counts confirm one typed snapshot hash on each location restoration
and full root hit, none on cached fingerprint reuse or invocation identity, and
no snapshot hashing during unchanged validation. The deterministic tests also
reject repeated geometry ingestion and snapshot rebuilding. No avoidable repeated
work was identified that justified a production change in this phase. Future
fingerprint optimization would need to preserve protocol bytes, null handling,
signed zero, temporal precision and layout independence; it is not a prerequisite
or an unimplemented part of this phase.

## Verification

- `uv run ruff format`: passed; only the changed test formatting needed adjustment.
- `uv run ruff check`: passed across the repository.
- `uv run ty check`: passed across the repository.
- `uv run pytest tests/test_integrated_storage_acceptance.py tests/test_schema_storage_fixtures.py -q`: 34 passed.
- `uv run pytest --cov=lyra_app --cov=lyra --cov-report=term-missing --cov-report=xml`: 2,329 passed, 94% coverage; `coverage.xml` generated, using required sandbox escalation.
- Final benchmark campaign: completed separately from checks; supported fidelity passed and expected corruption recovery completed.

Two existing dependency warnings remain: Google's future Python 3.11 support
sunset and Pandera's categorical coercion warning for future pandas behavior.
There are no outstanding required checks or supported-fidelity failures. Native
empty categories and the measured performance regressions remain explicit
limitations. No live services, wheel checks or new dependencies were used.
