# 12. Retained location inputs

Let clients retrieve the geographic inputs associated with a completed result to
map nonspatial outputs, inspect operation relationships, and compare runs across
provider revisions. Extend retained result access with the root metric's named
location inputs, using native tables and the existing store and transport contracts.

## Accepted scope

### Named root inputs and logical selections

Address a location input by `(result_ref, input_name)`. Include every named
`LocationDependency` of the root metric: primary inputs, reference inputs, direct
provider inputs, and inputs supplied through explicit operations or automatic
routing. Ordinary metric-table dependencies are outside this interface. A root
without location inputs has an empty `location_inputs` description mapping.

An available input represents the complete logical selection associated with the
result, including cached and freshly computed portions. For a request covering
A/B/C with only C requiring calculation, retrieving the primary input returns
A/B/C. Preserve complete shared reference inputs and the selected groups of
partition-related inputs under milestone 6's binding rules. Do not expose invocation
batches, leak additional groups from a full upstream computation, or concatenate
repeated shared inputs once per invocation.

Direct provider inputs have unique `location_id` records. Operation-derived inputs
preserve immediate `(source_location_id, location_id)` pairs, both provider scopes,
and the target's geometry and attributes. A target shared by several sources
appears once per pair. A multi-hop route retains the final hop's source/target
meanings rather than expanding ancestral relationships. Provider scope qualifies
each ID role; bare ID equality does not merge different providers or revisions.

Capture validated values and resolved descriptions before handler mutations.
Retrieve independent GeoDataFrames using retained schemas, nullability, CRS, code
revisions, and origin identities, without loading current plugins or acquiring
external data. Preserve milestone 11's logical types and native reconstruction.
Reads remain usable after the store or client closes, and caller mutations cannot
change retained values, descriptions, or later reads. Frame attributes establish
no execution identity. Input tables have no metric `row_key` or invented ownership.

### Availability and empty inputs

Every named location input has a retained description with one of two states:

| `availability` | Meaning |
| --- | --- |
| `"available"` | The complete logical input is retrievable, including a known empty input. |
| `"not_materialized"` | Execution skipped work that would have supplied the input, and its complete values were never materialized. |

Known empty selections and retained completed zero-match correspondences return
valid empty frames with their schema, scopes, and CRS. An empty metric output does
not imply an empty input. For example, blocks producing no findings remain
retrievable if they were supplied to the calculation.

A zero-primary-location request can skip a location-scoped root entirely while
leaving a nonempty reference input unacquired. Its primary input is known empty
and available; that reference input is `"not_materialized"`. Do not acquire or run
upstream nodes merely to populate inspection. A derived selection whose contents
were never resolved is not known empty just because execution skipped it.

Reading an input marked `"not_materialized"` raises `LocationInputUnavailableError`.
Never return a partial selection or substitute an empty frame for unavailable
values. Availability is fixed in the published result description; later runs do
not fill it in opportunistically. Missing data required by an available input is
an integrity failure, not a reason to downgrade its availability or recompute it.

Availability describes retrievable values, not observed work in this execution.
An input can be available for a fully reused result without fresh acquisition or
handler invocation. Execution reports retain milestone 10's honest outcomes.

### Required retention and cache independence

This milestone extends milestones 8 and 10's ordinary retrieval and durable-success
guarantees. Retain the provider records, operation pairs, schemas, and selection
references needed to reconstruct available location inputs. Publish their retrieval
descriptions with the final result and complete report after required values are
durable. Required persistence failure follows `ExecutionPersistenceError` locally
and the ordinary failed-job path remotely; it cannot publish successful but
incomplete input access.

Each retained metric computation carries the location-input reconstruction
references needed for later reuse, including when it was first evaluated as an
intermediate dependency. A later run selecting that metric as its root must obtain
its available inputs through those values and references. Per-location assembly
selects and combines the required groups, retaining complete shared context without
copying it per partition. These are retrieval relationships, not historical
invocation links or a reason to retain every intermediate metric output.

Reuse the existing computation keys and qualified record identities. Cache lookup
and result publication need not load input geometry or follow historical reports.
Preserve full hits, partial hits, reduced execution, and the existing rules for
complete shared inputs. Input inspection must not rerun handlers, selectors,
predicate queries, or provider acquisition to repair missing retained data.

Persistence for inspection is independent of execution reuse. With provider
caching disabled, ordinary computation still acquires its needed records under
milestone 9's policy; retaining those records does not authorize provider cache
hits. Reusing a downstream metric can still skip that acquisition. Declared
provider/code/origin changes continue to change identities through existing rules.

### Storage and inspection costs

Use milestone 8's relational store: retain each provider record once per existing
identity, store correspondence pairs separately, and reconstruct joined forwarding
tables on read. Result descriptions reference values and selected groups without
storing assembled copies. Share retained selection descriptions across partitions
instead of multiplying large reference-input memberships by the partition count.
Selection storage details are internal and introduce no new public identity format.

Indefinite retention remains the policy. Small outputs can require large retained
inputs; dense correspondences can exceed either provider's record count; changed
scopes and computation selections add distinct retained values. Deduplication
prevents avoidable duplication but does not bound storage growth. Initial writes,
serialization, indexes, and writer contention can increase even when the public
API is small. Do not promise negligible overhead.

Keep descriptions separate from values. `RunResult.tables` and `read_result()`
continue exposing named metric outputs; input descriptions accompany the metadata,
while input tables load only through explicit retrieval. Description reads do not
load input geometry or joined tables. HTTP/MCP inspection uses bounded SQL-selected
pages and Arrow bulk downloads under milestone 11's limits and authentication.
Native table reads and exports can still require memory proportional to the
requested input. Stored Arrow artifacts and permanent joined tables are unnecessary.

## API sketches

These examples assume a successful result whose root has a direct `city_blocks`
input named `blocks` and an operation-derived `trees` input named `nearby_trees`,
with immediate source scope `city_blocks`. The names illustrate declared inputs;
they are not additional lookup identities.

### Descriptions and local reads

Extend `RunResult.metadata` and remote `results.describe()` responses with
`location_inputs`, keyed by every root location-input name. Each entry uses
ordinary Pydantic field access:

| Field | Meaning |
| --- | --- |
| `kind` | `"provider"` for direct records or `"correspondence"` for operation-derived records, including routing. |
| `table_schema` | Retained `OutputMetadata` generated from the provider or forwarding schema, including types, nullability, units, and CRS. |
| `provider_scope` | The records' provider scope; the target scope for correspondences. |
| `source_scope` | The immediate source provider scope for correspondences; `None` for direct inputs. |
| `availability` | `"available"` or `"not_materialized"`. |

Scopes include provider ID, code revision, and declared origin identities with
applicable provider/origin descriptions. Descriptions remain available for empty
and never-materialized inputs without loading their values.

```python
from lyra.runtime.results import ResultStore

blocks_description = result.metadata.location_inputs["blocks"]
trees_description = result.metadata.location_inputs["nearby_trees"]

blocks_description.kind                         # "provider"
blocks_description.provider_scope.provider_id   # "city_blocks"
blocks_description.source_scope                 # None
trees_description.kind                          # "correspondence"
trees_description.source_scope.provider_id      # "city_blocks"
trees_description.provider_scope.provider_id    # "trees"

with ResultStore.open(result.metadata.store_path) as store:
    blocks = store.read_location_input(result.metadata.result_ref, "blocks")
    trees = store.read_location_input(result.metadata.result_ref, "nearby_trees")
```

`blocks` has unique provider records. `trees` includes `source_location_id`,
`location_id`, target attributes, and active `geometry`, preserving repeated
targets under different sources. Neither read loads metric output tables or
requires a runner. Result references and input names use shared strict string
validation, with input names resolved against the root's declared location inputs.

### Remote native tables, rows, and downloads

```python
from lyra.api import LyraClient

with LyraClient("lyra.example.com", agent_api_key="agent-key") as client:
    description = client.results.describe(result_ref)
    scope = description.location_inputs["blocks"].provider_scope
    blocks = client.results.location_input(result_ref, "blocks")
    page = client.results.location_input_rows(
        result_ref, "nearby_trees", limit=100, cursor=None,
    )
    client.results.download_location_input(
        result_ref, "nearby_trees", "block-tree-correspondences.arrow",
    )
```

`location_input()` returns an independent GeoDataFrame. `location_input_rows()`
returns milestone 11's `rows`/`next_cursor` page with its JSON cell encodings.
`download_location_input()` writes an Arrow IPC stream to a local `str` or `Path`
destination and returns `None`. It follows milestone 11's temporary-file,
replacement, cancellation-cleanup, and ordinary filesystem-error contract. Native
reads and downloads share the Arrow endpoint and preserve geometry and CRS through
standard GeoArrow WKB encoding. Known empty inputs have an empty JSON page and a
valid Arrow stream containing their declared schema.

`AsyncLyraClient` exposes the same methods, arguments, and return values with
`await`, including descriptions, pages, and downloads:

```python
from lyra.api import AsyncLyraClient


async def read_blocks(result_ref: str):
    async with AsyncLyraClient(
        "lyra.example.com", agent_api_key="agent-key"
    ) as client:
        return await client.results.location_input(result_ref, "blocks")
```

### HTTP and MCP access

Reuse `GET /results/{result_ref}` and `lyra_describe_result(result_ref)` for the
extended description. Add these read-only interfaces:

| Operation | HTTP | MCP tool |
| --- | --- | --- |
| Read input rows | `GET /results/{result_ref}/location-inputs/{input_name}/rows?limit=100&cursor=...` | `lyra_get_location_input_rows(result_ref, input_name, limit=100, cursor=None)` |
| Download an input | `GET /results/{result_ref}/location-inputs/{input_name}/download` | `lyra_download_location_input(result_ref, input_name)` returns a download link. |

The download tool returns `result_ref`, `input_name`, and `download_url`. URLs are
absolute, use the deployment's public API base URL, and require ordinary
authentication. Success uses HTTP `200`, with Arrow downloads served as
`application/vnd.apache.arrow.stream`. MCP retains SDK-generated schemas,
argument validation, and protocol envelopes from milestones 3 and 11.

Apply milestone 11's row-count and encoded-response limits, complete-row pages,
oversized-row error, and Arrow fallback. Cursors bind to the result, input name,
and location-input interface; a named-output cursor is invalid here even if its
name matches. Complete traversal preserves each provider record or correspondence
pair once, without deduplicating targets across sources or promising row order.

### Unavailable inputs and lookup errors

Expose `LocationInputUnavailableError` from `lyra.contracts.errors`, under
`ResultAccessError`, with `result_ref` and `input_name` attributes. For a result
whose root skipped acquisition of its nonempty `reference_blocks` input:

```python
from lyra.contracts.errors import LocationInputUnavailableError

with ResultStore.open(result.metadata.store_path) as store:
    try:
        reference = store.read_location_input(
            result.metadata.result_ref, "reference_blocks",
        )
    except LocationInputUnavailableError as error:
        print(error.result_ref, error.input_name)
```

Local and remote methods share the existing result-access exceptions and error
categories, adding the explicit unavailable condition:

| Condition | HTTP | Error code | Python exception |
| --- | --- | --- | --- |
| Malformed result reference | 422 | `invalid_result_reference` | `InvalidResultReferenceError` |
| Missing result or undeclared location-input name | 404 | `result_not_found` | `ResultNotFoundError` |
| Input marked `"not_materialized"` | 409 | `location_input_unavailable` | `LocationInputUnavailableError` |
| Damaged required input data or relationships | 500 | `result_integrity_error` | `ResultIntegrityError` |
| Invalid cursor or page limit | 422 | `invalid_pagination` | `InvalidPaginationError` |
| Next row cannot fit in an otherwise empty JSON page | 422 | `row_too_large` | `RowTooLargeError` |

Extend applicable existing result-access exceptions with optional `input_name`.
For location-input calls, expose `result_ref`, `input_name`, and `output_name=None`;
pagination errors also expose `parameter`, and oversized-row errors expose the
input's Arrow `download_url`. Whole-result and named-output calls have
`input_name=None` and preserve their existing behavior and payloads. The new
unavailable exception needs only `result_ref` and `input_name`.

HTTP/MCP failures use milestone 11's bounded structured payloads with the relevant
input attributes; Python clients map codes without parsing messages. SDK argument
validation remains separate. Unavailability is fixed for that result and retrying
does not materialize it. Integrity failures never fall back to provider acquisition
or silently invalidate a valid computation for rerunning. Oversized-row errors
retain the cursor position and complete Arrow fallback from milestone 11.

## Completion boundary

Successful managed results expose durable location-input descriptions and complete
reads for available inputs across local, development, and remote execution.
Previously returned frames and metadata cannot alter retained data. Reopening the
store, job expiry, and changed active registrations preserve the captured scopes,
schemas, and availability without current plugins or external acquisition.

Isolated behavioral checks cover direct inputs, references, explicit and routed
operation inputs, repeated targets, immediate scopes in multi-hop routes, empty
selections, completed zero matches, and nonempty inputs producing empty outputs.
Include roots with no location inputs, unpartitioned roots, and skipped roots with
never-materialized reference inputs. Distinguish known empty, unavailable, and
damaged available inputs; reject unknown names and ordinary metric dependencies.

Verify full and partial cache hits, complete shared context, group selection from
full upstream computations, disabled provider caching, and a retained intermediate
metric later reused as a root. Reads and cache-hit publication must perform no
additional acquisition, selector construction, or handler invocation. Test handler
and caller mutations, changed provider/code/origin revisions with equal bare IDs,
and required persistence failures using the existing success-publication contract.
Input references remain usable independently of historical producing reports.

Verify description reads without input materialization, native frame isolation,
empty Arrow schemas, CRS, JSON encodings, bounded pages, cross-interface cursor
rejection, oversized rows, and complete downloads. Cover synchronous and
asynchronous clients, both new HTTP endpoints and MCP tools, authentication,
shared errors and attributes, and download replacement/failure/cancellation rules.
Use mocked clients, acquisition, connections, and in-process fakes with temporary
files; keep the service-access guard enabled and use no live services or real
database connections. Do not pin documentation wording with tests.

Use existing benchmark facilities to assess initial persistence, fully cached and
mixed execution, inspection, and retained storage growth. Include large shared
reference inputs across many partitions, dense correspondences, repeated runs,
and changed revisions. Check that records, shared selection descriptions, and
joined geometries are not copied per partition or execution unnecessarily.
Measure write time, memory, cached execution, and stored size without timing gates,
invented limits, or claims that indefinite retention is bounded.

## Deferred or excluded work

GeoParquet export of these retained inputs follows in
[milestone 13](13-geoparquet-exports.md). This milestone provides data access and
does not store generated Arrow or GIS files.

Fresh provider browsing or acquisition, arbitrary internal graph-node access,
ordinary metric-table dependency retrieval, candidate/rejection histories,
finding-level contributing rows, and full invocation snapshots are outside scope.
Inputs describe the logical selections associated with a result, not which rows
an arbitrary handler actually used in each calculation. No public group/row trace
identities, graph-navigation framework, or historical producer traversal is added.
Retention switches, automatic cleanup, and historical-store compatibility remain
deferred under the current-store contract.

## Reference points

Build on [native input bindings](06-unified-graph-execution.md),
[shared storage](08-sqlite-result-storage.md),
[cache assembly](09-node-caching.md),
[durable success](10-execution-reports-and-durable-success.md), and
[result retrieval](11-result-and-report-retrieval.md).
Current implementation boundaries include [execution](../src/lyra/runtime/execution.py),
[location handling](../src/lyra/runtime/locations.py), and
[result transport](../src/lyra/server/results.py).
