# Opt-in traceability implementation ledger

Lyra should support tracing declared row dependencies through a calculation graph
to source records and effective parameters. Traceability is optional so ordinary
plugin authoring remains accessible. This ledger records the first implementation's
accepted direction, working contract, and remaining decisions; it does not describe
an implemented capability.

Keep the implementation small and place responsibility for accurate declarations
on plugin authors. Closed-alpha status permits breaking changes without migration
or compatibility machinery.

1. **Keep opaque and traceable authoring in one runtime.**

   **Accepted.** Opaque nodes retain ordinary inputs, parameters, output schemas,
   and the handler return contract for their node kind. All nodes declare
   location-based partitioning and output ownership. Traceable nodes additionally
   provide row identity and lineage.
   Both use the same dependency resolution, executor, resource lifecycle,
   validation, storage, and transports.

   Ordinary executions may mix node roles without exposing a complete trace.
   Traced executions require every reachable computational node to be traceable,
   with explicit source nodes permitted as boundaries. Check the complete selected
   dependency graph, including location operations, before resource acquisition or
   handler execution. Reject an opaque dependency with its offending path.
   Eligibility applies to an execution graph; plugins and deployments may contain
   all roles.

2. **Represent external origins with explicit source nodes.**

   **Accepted.** Use three traceability roles: opaque, traceable, and source. These
   roles are independent of the location-provider, metric, and location-operation
   contracts, which describe what a node produces. Source nodes use the shared
   executor and follow the same location-based partition rules.

   A source node supplies identified outputs, location associations,
   a source identifier, and a revision when available. Its internal queries, file
   reads, API calls, and processing are opaque. Lyra attaches source-invocation
   provenance to its rows without requiring lineage for those internal steps.
   Unversioned origins remain explicit; tracing does not guarantee historical
   reproducibility.

   A source boundary preserves dependencies inside Lyra. A loader receiving a
   computed location selection still depends on that selection and its parameters.
   Its upstream graph must satisfy the tracing rules. Authors explicitly declare
   dependencies on supplied Lyra inputs, using individual row references or
   whole-input references to particular named inputs as described in item 4.
   Lyra does not automatically link every source output to all supplied inputs.
   These declarations describe dependencies outside the source boundary; internal
   acquisition and processing remain opaque. Calculations expose data reads
   through named source/provider boundaries rather than hidden acquisition.

   Authors decide where boundaries belong. Inspection must display them clearly:
   the guarantee is completeness within declared boundaries, not visibility into
   every underlying system.

3. **Give outputs explicit identity and location associations.**

   **Working contract.** Every traceable table has a non-null, unique row identity.
   A row reference includes its table instance, producing invocation, and output
   name; keys need not be globally unique. DataFrame indexes and row positions are
   not identities. Concrete row-key declarations and reference encoding, including
   the operation-pair identities below, remain under R1.

   **Accepted unordered collections and explicit identity.** Location selections,
   operation target groups, partition groups, and table rows have no semantic
   ordering. Lyra makes no developer-facing guarantee about input or result row
   order, target-tuple order, dictionary iteration order, or column positions.
   Associate records through qualified location IDs, operation input/target pairs,
   and declared metric-row identities; address inputs, outputs, and columns by
   name. Cache lookup, forwarding, assembly, and lineage must not match records by
   position. Reordering a collection does not change its logical membership or
   identity, and execution or storage may change its physical arrangement.

   Handlers must produce the same logical keyed results for permutations of the
   same supplied rows. Authors represent meaningful sequence information as
   explicit data, such as timestamps, ranks, priorities, or ordered parameter
   values, and use those values in their calculations. Explicit sequences within
   data or parameters retain their meaning; collection-order independence does
   not make every list-valued field or parameter unordered. Internal sorting for
   key serialization or record alignment is an implementation detail and creates
   no ordering guarantee for authors or callers.

   Every node has a location-based partition key, independently of its caching
   strategy or traceability role. A partition is identified by
   `(dataset_id, revision, location_id)`. Select a particular named location input
   as the partitioning input, not merely a location type; two inputs can have the
   same type or dataset while serving different roles.

   **Accepted location ID type for the initial implementation.** Individual
   location IDs are strings. Validate with Pydantic's strict mode at the shared
   validation boundary, without coercing other types to strings. Plugin authors
   expose numeric or composite source identifiers as stable, unambiguous strings.

   The operation mapping can use the same alias for input and target IDs:

   ```python
   from pydantic import ConfigDict, TypeAdapter

   LocationID = str

   operation_mapping = TypeAdapter(
       dict[LocationID, tuple[LocationID, ...]],
       config=ConfigDict(strict=True),
   )
   ```

   Pydantic owns type validation and schema generation. Use standard string
   encoding in JSON and Arrow, preserving ID spelling. The types of other
   metric-row keys and their reference representation remain under R1 and R7.

   Location nodes are provider/dataset nodes that return location records. They
   are partitioned by their own locations, with exactly one returned row per
   successfully resolved location key. This uniqueness rule applies to location
   records, not operation correspondence rows: operations may return several
   relationships for one input location. Metric and operation nodes must have at
   least one location input. Select the sole location input automatically; when
   several exist, the developer explicitly selects one for partitioning.

   Output schemas associate columns with named location inputs, such as
   `building_id` with `buildings`. Each metric output row belongs to exactly one
   key of the selected partitioning input. Each key may have zero, one, or several
   rows in each named output. Multiple inputs and multiple named outputs remain
   supported. A finding can have its own row identity and a separate building
   association; rows may also reference other locations without changing their
   partition owner. Operation correspondence rows follow the pair model below.

   **Accepted input selection bindings.** Declare selection behavior on each
   consumer's named input, independently of the producer's caching strategy or
   other consumers. A partition-related binding supplies rows associated with the
   requested primary partition keys through a known correspondence. A shared
   binding supplies the complete bound input selection unchanged across primary
   partitions; it does not imply loading the entire dataset.

   Infer straightforward cases from the actual binding declarations:

   - The primary location input follows the node's partition declaration.
   - A dependency that explicitly forwards that primary input into its own
     partitioning input follows the binding automatically.
   - An operation deriving locations from that primary input supplies the
     corresponding targets through its mapping, including through bound chains.

   For other inputs whose relationship to the primary partition is unclear,
   require an explicit shared binding or a declared correspondence. Matching
   argument names, location types, or IDs alone does not establish that
   correspondence. Authors may explicitly choose shared selection to override
   partition-following behavior when broader context is needed.

   Extend the existing input declarations with an optional selection setting;
   `selection="partition"` and `selection="shared"` are illustrative spellings,
   not a finalized API. This behavior applies across `off`, `full`, and
   `per_location`. Selecting the rows a consumer receives does not establish that
   an upstream producer can compute only those rows. Apply the accepted upstream
   execution rules in item 9 and R4 when cache hits reduce downstream work.

   Outputs are location scoped. Request-wide results are outside Lyra's supported
   contract and belong in an external pipeline.

   Associations describe what a row represents. Dependencies describe what
   contributed to it. Identifying a building and its nearest hospital does not
   capture the dependency on all candidate hospitals.

4. **Describe immediate row dependencies with one relation table.**

   **Working contract.** Traceable handlers supply lineage alongside their named
   outputs. Each relation identifies an output name and row key, an immediate
   input argument, and an input row key. A null input key means the entire supplied
   input, including its selected membership and explicit data. Actual row keys
   cannot be null.

   For a nearest-hospital result, the relations can be:

   | Output | Output row | Input | Input row |
   | --- | --- | --- | --- |
   | nearest | B17 | buildings | B17 |
   | nearest | B17 | hospitals | null: whole input |

   Several relations can identify several contributing rows. Whole-input
   references cover aggregate, ranking, and selection dependencies without
   expanding every pair of rows. Authors describe immediate dependencies; Lyra
   traverses their composition across nodes. Contributions affecting selection or
   explicit ranking values must be declared even when their values are absent
   from the output.

   **Accepted explicit whole-input dependencies.** Authors may declare dependence
   on one named input as bound, including its selected membership and explicit
   data. This does not automatically include every input in the request or every
   location in the underlying dataset. For example, a nearest-hospital result for
   building A may depend on A and the entire supplied hospital selection. Different
   building partitions may share that hospital input while remaining independently
   computable. Whole-input references avoid enumerating every contributing row;
   empty-result and empty-input lineage remain under R3.

   **Accepted operation return contract.** The Python authoring representation of
   an operation's correspondence output is
   `dict[LocationID, tuple[LocationID, ...]]`: each key is an input location ID and
   its value is a tuple containing the corresponding target location IDs.
   `LocationID` is `str`, so the concrete mapping type is
   `dict[str, tuple[str, ...]]`. The tuple is a Python container for an unordered
   target group; each individual target ID is a string.

   ```python
   {
       "A": ("H1", "H2"),
       "B": ("H1",),
       "C": (),
   }
   ```

   Keys must belong to the partitioning input supplied to that invocation;
   returning an unsupplied key is a structural error. A successful return covers
   every supplied input key. Omitting a key and returning an empty tuple for it
   both mean that no target location corresponds to that input. Normalize omitted
   keys to empty groups and record them as successfully computed, eligible for
   caching. Omission does not mean unprocessed work, and this completion rule
   applies only to inputs supplied to the current invocation.

   Require unique target IDs within each tuple; duplicates are a structural error.
   The same target ID may appear under several different input keys. Target-tuple
   permutations and dictionary insertion order have no semantic significance.
   Assemble operation groups by their explicit input and target IDs.

   Target IDs refer to the operation's declared target dataset and revision.
   Every target must be an existing location resolvable through that dataset's
   provider. The operation returns IDs, and Lyra obtains geometry and attributes
   from the provider when needed. Retain the correspondence separately from target
   records, with both operation lineage and provider source provenance.

   **Accepted target forwarding.** Materialize each distinct target location once
   in the downstream location table, without a row-order guarantee. For example,
   `{"A": ("X", "Y"), "B": ("Y",)}` supplies location records X and Y while
   retaining all three correspondence pairs. Use the mapping to associate the
   shared records with each input as needed. Apply the same rule along operation
   chains, retaining each operation's immediate relationships without expanding
   every possible path in advance.

   Keep location-data identity separate from the provenance of selecting that
   location. A shared target X may be reached through both A and B; retain the
   corresponding selection relationships for each use while sharing X's records.
   Reuse preserves the original result's production history and records its use
   with the current request's associations and selection history.

   **Accepted target resolution failures.** An unresolved target produces an error
   during normal acquisition. Do not silently drop it or treat it as a successful
   zero-match result. No separate preflight existence lookup is required.

   **Accepted mapping encoding.** The JSON payload is an object with string keys
   and arrays of target strings. Pydantic's declared mapping type restores the
   tuples. Store grouped mappings in Arrow using a string input-ID column and a
   list-of-strings target-ID column, preserving membership and empty groups. The
   physical sequence in tuples, JSON arrays, or Arrow lists has no semantic
   significance for target groups. Use the libraries' built-in serialization
   without a custom location-ID codec. Operation row references carry separate
   input and target IDs, qualified by their
   dataset/revision and invocation/output scopes; concrete reference envelopes
   remain under R1. Traceable operations supply lineage alongside the mapping
   under the shared traceable return contract.

   A location operation's correspondence output is conceptually a set of distinct
   pairs `(input_location_id, output_location_id)`. The declared input and output
   dataset/revision scopes qualify each ID. The first component identifies the
   partition owner; each input location maps to zero, one, or several target
   locations. An input with no corresponding target has an empty group of pairs.
   Different input locations may reference the same target location: `(A, H)` and
   `(B, H)` are distinct relationship rows owned by A and B respectively. Repeated
   target IDs across partitions do not create overlapping relationship rows or
   require a different caching strategy from metrics. The pair identifies the
   relationship; the target location retains its own location-data identity.

   These correspondences describe associations, not complete dependency lineage.
   Operation rows follow the same immediate-dependency model as metric rows,
   including contributors that affect selection but are absent from the pairs.
   Authors supply the correspondence mapping; Lyra does not infer it from equal
   IDs or geometry. Every named output participates.

   **Accepted location boundary for the initial implementation.** Operations
   describe relationships between existing locations. They may perform spatial
   calculations internally to determine those relationships. One-to-many and
   many-to-one mappings remain ordinary correspondences and require no special
   splitting or merging semantics.
   Lyra does not provide operation mechanisms for creating locations, assigning
   their identities, or registering their geometries and attributes, including
   locations formed by merging or splitting. Workflows needing newly constructed
   locations prepare them externally and expose the results as a registered
   location dataset. Lyra traces computations and relationships within its declared
   source boundaries; it does not explain that external preparation process.

5. **Record conservative parameter dependencies and trust authors.**

   **Working contract.** Record effective parameter values and their resolved
   origins once per invocation: caller inputs, upstream bindings, literals, or
   defaults. Every output row is potentially dependent on all parameters supplied
   to that invocation. A row dependency covers the entire input row.

   Authors own semantic completeness. Existing schema checks can enforce declared
   structure, and graph checks enforce eligibility. Lyra does not inspect handler
   code, prove independence, or verify that every actual contributor was reported.
   Broad whole-input dependencies are valid conservative declarations. Fine-grained
   column and parameter influence tracking is outside the initial implementation.

6. **Give each validation step one owner.**

   **Accepted.** Validate each producer result once at a shared boundary. Include
   output tables or operation mappings and, where applicable, declared row keys
   and the structural lineage contract. Validate and normalize operation mappings
   at this same boundary. Pass owned validated data with its matching storage
   schema to the writer. Publication consumes this result without repeating producer
   validation. Lineage accuracy and completeness remain the author's responsibility.

   Give the executor sole ownership of execution-plan validation, including
   traceability eligibility, before resource acquisition. Remove the worker's
   duplicate plan-validation call and translate executor failures at the worker
   boundary. Independent entry points, including cache reads and public interfaces
   accepting arbitrary data, still validate their inputs.

   Verify that mutation and alternate call paths cannot bypass required checks.
   This boundary applies to opaque, traceable, and source outputs as appropriate;
   adding lineage must not create parallel validation paths in adapters, execution,
   and storage. Internal representation and error propagation are implementation
   details within this scope.

   [Producer validation](src/lyra/sdk/results.py),
   [storage preparation](src/lyra/sdk/storage_schema.py),
   [worker execution](src/lyra/server/worker.py).

7. **Use entry points for plugin discovery and Pydantic for parameters.**

   **Accepted.** Discover explicitly enabled installed plugins through standard
   Python entry points and load their definitions in API and worker processes.
   Live definitions carry node roles, row-key declarations, and location
   associations in the shared catalog. Let Pydantic validate parameters and apply
   defaults, generating JSON Schema for discovery. Remove mandatory generated
   plugin manifests, their synchronization machinery, parallel JSON Schema
   validation, and custom default handling. Any retained manifest export is an
   optional inspection tool.

   Relax restrictions imposed solely by parallel validation while preserving a
   usable JSON interface and discovery schema. Capture effective parameter values
   and supplied/default field presence during normalization for queued requests
   and trace records. Historical inspection uses persisted descriptions without
   requiring the producing plugin to be installed.

   Plugin imports, registration, and parameter validation must be lightweight and
   avoid external I/O. Acquire data services and other resources during execution.
   The API must be able to import enabled plugins and their dependencies. Increased
   startup time and memory use, and startup failures caused by broken plugin
   imports, are accepted tradeoffs. The deployment example already shares one
   image between API and workers. Entry-point naming, startup diagnostics, and
   internal catalog structure remain implementation details.

   [Manifest verification](src/lyra/runtime/loading.py),
   [parameter restrictions](src/lyra/sdk/parameters.py),
   [Python plugin discovery](https://packaging.python.org/en/latest/guides/creating-and-discovering-plugins/).

8. **Queue compact requests tied to deployment revisions.**

   **Accepted.** Require the API and workers to use the same immutable plugin
   build for ordinary and traced jobs. Jobs are inexpensive and short; rejecting
   pending work after an incompatible deployment is an acceptable cost. Remove
   per-job captured executable graph contracts and their comparison and
   reconstruction machinery.

   Queue the normalized request, requested tracing mode, build revision, and
   required dataset revisions. Preserve supplied parameter fields or equivalent
   origin flags during normalization so explicit values remain distinguishable
   from defaults. Carry the effective values without losing this distinction.

   Reject a build mismatch before resource acquisition or handler execution,
   using the existing terminal failure status with a clear `deployment_changed`
   reason. Callers can resubmit. Prepare compatible jobs against the worker's
   catalog and enforce the requested traceability requirements before execution.
   The build revision covers executable code, routing policy, and declarations
   affecting traceability. The public discovery fingerprint serves a different
   purpose. Build identification and compatibility checks remain implementation
   details. The deployment build revision serves job compatibility and provenance;
   computation-cache implementation identity uses the author-managed code revision
   specified below.

   Persist the request, execution identity, outcome, and output descriptions,
   including the selected node, route, and relevant revisions. Traced results
   additionally retain actual invocation node/build identities, declared code
   revisions and roles, resolved input and parameter bindings, effective values
   and their origins, output row identities and location associations, dependency
   relations, and source metadata.
   Record parameter origins when resolved, including upstream bindings, literals,
   and defaults.

   Store these descriptions independently of queued jobs and the live plugin
   catalog. Historical inspection must survive RQ expiry and plugin changes or
   removal, without importing the producing plugin. Persist descriptive metadata
   sufficient to interpret the trace; do not restore executable contract snapshots
   or deployment-synchronization machinery.

   [Acceptance models](src/lyra/contracts/accepted.py),
   [plan preparation](src/lyra/runtime/planning/preparation.py),
   [job observations](src/lyra/server/job_store.py).

9. **Let authors select caching per node and preserve cached ancestry.**

   **Accepted.** Make caching available at intermediate and final nodes through
   three developer-selected strategies:

   | Strategy | Behavior |
   | --- | --- |
   | `off` (default) | Execute the complete input batch on each invocation. |
   | `full` | Reuse a complete invocation when its effective inputs match. |
   | `per_location` | Reuse matching locations, compute the missing subset, and assemble the requested result. |

   **Accepted node-local meaning of `off`.** This strategy disables the node's
   own computation-cache reuse. The node executes whenever it is invoked, but a
   valid downstream cache hit may bypass that invocation entirely. `off` does not
   propagate a freshness requirement or invalidate dependent cached results.
   Authors requiring fresh external observations must disable caching on affected
   downstream computations or represent changes through declared input
   identities. Lyra does not infer that freshness requirement from `off`.

   All caching strategies use the node's declared partition mapping. When
   executed, `off` and `full` handlers receive the complete bound inputs; a
   partition mapping alone does not establish independent computation.
   `per_location` uses the location keys
   and output ownership defined above. Each cache entry contains all results
   owned by one location across the node's named outputs, including an empty
   group when a location successfully produces no rows. Record completion so
   that an empty group can be reused and is distinguishable from a cache miss.
   Operation correspondence pairs are grouped by their input-location component
   and follow the same rule. Normalize omitted dictionary keys to completed empty
   groups for the inputs supplied to a successful invocation. Assemble operation
   and metric groups through explicit keys, without promising result order. Other
   inputs still contribute to computation identity. Consumer inputs follow the
   accepted partition-related or shared selection bindings in item 3.

   **Accepted order-independent cache identity.** Reordering the same location
   selection, operation target group, or keyed input rows does not by itself change
   computation identity. Keys account for the selected identities and membership,
   not incidental collection order. Explicit sequence values in data or parameters
   remain significant. Internal canonicalization may sort identities for a stable
   key representation; it does not promise any handler input or output order.

   **Accepted upstream execution scope.** Preserve each producer's original bound
   invocation for the request before cache hits reduce downstream work. That
   binding describes a particular input selection, not the entire dataset. When
   an upstream result is needed, only a producer declaring `per_location` may
   receive a reduced partition request based on the consumer's missing keys.
   `off` and `full` do not establish the required independence promise and retain
   their complete original bound inputs.

   For example, a request covers A, B, and C, but the downstream result for A is
   a valid cache hit. The downstream handler needs partition-related findings for
   B and C from a producer originally bound to A, B, and C:

   | Producer strategy | Upstream behavior |
   | --- | --- |
   | `off` | Execute with A, B, and C. |
   | `full` | Look up the complete A/B/C invocation; execute all three on a miss. |
   | `per_location` | Request B and C, check the producer's own cache, and compute only its missing keys. |

   In each case, supply only the findings associated with B and C to that
   partition-related consumer input. Shared inputs retain their complete bound
   selections, including when a `per_location` producer executes a reduced batch.
   Apply the same rule at each upstream dependency; a downstream cache hit must
   not silently change the bound inputs of an `off` or `full` producer.

   Authors choose the strategy and own the independence promise required by
   per-location reuse, including dependencies. Authors validate this independence;
   Lyra does not prove it or automatically select a safer strategy. Lookup,
   partitioning, and assembly complexity is accepted because it enables reuse
   across overlapping requests.
   Use the shared immutable artifact infrastructure; caching policy does not
   determine artifact retention.

   **Accepted shared operation storage and caching.** Metrics and operations use
   the same cache lookup, publication, retention, and reuse machinery, with result
   serializers appropriate to tables or mappings. An operation mapping is a stored
   result owned by the shared result layer. Cache lookup associates a computation
   key with a stored result reference; provenance references that same mapping.
   Preserve mapping membership and completed empty groups, and avoid separate
   copies solely for cache and provenance use. Cache matching and eligibility
   still determine reuse; retaining a historical mapping is not sufficient by
   itself.

   Mapping storage supports caching independently of trace retention, including
   opaque operations. A mapping retained for inspection under `off` does not
   authorize that operation's own cache reuse: it still executes when invoked.
   A valid downstream cache hit may avoid invoking it. `full` reuses a matching
   complete invocation's mapping, and `per_location` reuses matching input-location
   groups and computes missing groups.

   Each executable node may declare an optional opaque code revision, including
   metrics, operations, and provider/source handlers. Its implementation identity
   for caching is `(node_id, code_revision)`. This identifies the implementation;
   `(dataset_id, revision, location_id)` identifies a location record. A complete
   computation identity also includes the effective parameters and inputs.
   An omitted code revision is represented by `None`, a stable value that permits
   cache reuse. Plugin authors own changes to this token whenever implementation
   or behavior-affecting declarations change; assigning a different node ID also
   changes implementation identity. Lyra compares the declared tokens for equality
   without interpreting version numbers, hashing code, inspecting package versions,
   or detecting changes automatically. Authors may automate token updates in their
   own tooling.

   Relevant upstream implementation identities must contribute to dependent
   computation identities so a changed upstream code revision prevents reuse of
   dependent cached results. Deployment build revisions are recorded for job
   compatibility and provenance, not used as additional computation-cache
   invalidation tokens. No automatic cache clearing on code changes is required;
   cache reuse follows the declared identities.

   Cached traceable results retain enough lineage to reach their declared source
   boundaries. An opaque cache entry cannot satisfy traced execution. Reuse
   identifies the original production rather than suggesting the handler executed
   again. Combining cached and newly computed rows preserves both origins. Retain
   the current use's associations and selection history separately from the
   reused result's original production history. Sharing location records does not
   merge their different selection paths or establish that complete traces match.

   Traceability and caching policy remain independent. Opaque nodes may support
   per-location caching using their partition mappings without supplying full
   lineage. Lineage alone does not establish that a node is safe to cache per
   location.

   **Open.** Complete cache-key composition and the backend remain to be specified
   with the per-location implementation, using the accepted implementation and
   location-data identities. These decisions do not select a storage backend.

   [Cache implementation](src/lyra/runtime/cache.py),
   [executor](src/lyra/runtime/execution.py).

10. **Always return lineage from traceable handlers.**

    **Accepted.** Whenever a traceable handler executes, it returns outputs and
    lineage together, including during ordinary execution. Opaque handlers retain
    their ordinary return contract. The additional CPU and memory cost for ordinary
    callers using traceable nodes is accepted in exchange for one traceable return
    contract and simpler execution and caching.

    Requesting a traced execution controls graph completeness checks and whether
    the complete final trace is retained and exposed. It does not toggle lineage
    generation inside handlers. Conditional generation can be reconsidered if real
    workloads justify it.

    Decorator names, result-class names, and field spelling are implementation
    details within the agreed contract.

11. **Retain shared artifacts indefinitely in the initial implementation.**

    **Accepted.** Use an append-only store of immutable artifacts, reusing existing
    artifacts across requests through cache and result references. Retain stored
    artifacts, result manifests, and trace metadata indefinitely. Invalidating cache
    mappings or expiring job records does not delete underlying artifacts. Trace
    traversal resolves persisted identities and relationships independently of
    transient RQ records.

    Cache lookup, result assembly, and tracing still require references. Automatic
    artifact cleanup, retention counts, and ownership-based deletion are deferred.
    An explicit whole-store reset can serve disposable development environments.
    Storage growth, including unused artifacts, is accepted for the first
    implementation. No per-result copying of shared artifacts is required.

    Indefinite retention preserves data that was written. Intermediate-value
    persistence follows the policy below. Arrow tables and small metadata records
    fit the existing storage approach. The physical layout remains an
    implementation detail.

12. **Retain lineage without requiring intermediate-value snapshots.**

    **Accepted.** Traced executions retain row identities, input membership,
    dependency relationships, effective parameters, and source information.
    Intermediate metric values and location geometry/attribute tables follow the
    caching policy. With caching off, these intermediate values are used in memory
    without requiring snapshots solely for tracing. Final job outputs remain
    persisted for retrieval.

    Retaining an operation's correspondence identities and membership can
    preserve its complete mapping. Store that mapping once in the shared result
    layer when required for provenance, even when operation caching is off. Trace
    records and any eligible cache entries reference the same stored result.
    Trace retention does not enable cache reuse, and cache storage does not depend
    on trace retention. This mapping retention does not require geometry or
    attribute snapshots. Explanatory dependencies for empty groups remain under R3.

    Cached artifacts can also supply intermediate values for inspection. Cached
    traceable results preserve lineage through uncached predecessors even when
    those predecessors' table values are unavailable. Traversing the declared
    dependencies does not require retaining every referenced table's contents.

    **Deferred.** An explicit per-run diagnostic option could capture all node
    outputs for historical value inspection. The initial implementation does not
    guarantee recovery of every intermediate value or snapshot external source data
    solely for tracing.
    Capturing node outputs would not by itself guarantee reproducible execution.

13. **Inspect immediate upstream dependencies through one shared operation.**

    **Accepted.** Expose one bounded, read-only operation through Python, HTTP, and
    MCP. Given a row reference, return its producing node and invocation, effective
    parameters, immediate upstream dependencies as followable references, source
    identity and revision at a boundary, and whether its actual values are stored
    and available for inspection.

    Clients follow references to walk upstream. Whole-input dependencies return
    compact references with paginated access to their members. Bound responses,
    including large lists of direct dependencies. Missing intermediate values do
    not make the lineage incomplete, and inspection never reruns computations to
    recover them. Agents can inspect traces through MCP without code execution.

    Exploring a large graph requires multiple calls. Automatic recursive expansion,
    graph visualization, and dedicated downstream-impact search remain deferred.
    Reference encoding, method names, and pagination details are implementation
    choices within this contract.

14. **Use Arrow bulk downloads and paginated JSON row access.**

    **Accepted.** Serve named output tables as Arrow bulk downloads and provide
    bounded, paginated JSON rows through HTTP and MCP. Keep status, schemas,
    provenance, lineage, previews, and download links in JSON. Agents can read
    result values through MCP without code execution or an Arrow reader. Row access
    complements the accepted lineage-inspection operation.

    Workers already persist Arrow IPC files. Replace the full-table
    Arrow-to-DataFrame-to-JSON-to-DataFrame round trip with Arrow downloads and
    standard table conversion in the Python client. Both transport formats read
    the same stored tables through shared retrieval logic. JSON serialization and
    supported spatial conversion remain necessary for row access, so the code
    reduction is smaller than an Arrow-only interface would provide.

    Serve an existing file directly when it matches the requested table. Results
    assembled from shared artifacts, including future per-location cache hits,
    require key-based row selection and combination before delivery. This need
    not create an additional permanently stored copy of the result.

    Pagination must traverse a fixed result without skipping or repeating row
    identities. Its cursor and traversal arrangement are implementation details;
    the API does not promise any relative row order. Apply the same coverage rule
    to paginated lineage members. Separate reads and transport formats need not
    present rows in the same physical arrangement.

    Bulk consumers need an Arrow reader; Arrow downloads retain stored types and
    native CRS. A local city-example check using `pyarrow.ipc.open_file` and
    `GeoDataFrame.from_arrow` preserved IDs, geometry, and native
    EPSG:32614 CRS. Broader dtype coverage remains part of implementation
    verification. Pagination and JSON row encoding details remain implementation
    choices within this client contract.

    [Server conversion](src/lyra/server/results.py),
    [client reconstruction](src/lyra/api/client/_tables.py),
    [GeoPandas Arrow reader](https://geopandas.org/en/stable/docs/reference/api/geopandas.GeoDataFrame.from_arrow.html).

15. **Separate artifact integrity, location equality, and cache identity.**

    **Accepted.** Use a standard checksum of the actual stored bytes for artifact
    integrity. Compare location contents by aligning records through qualified IDs
    and columns through names, then using Arrow's table comparison. Physical row
    and column positions do not determine semantic equality; typed values,
    geometry, location scope, CRS, and explicit sequence-valued data remain
    significant. Use library alignment and comparison operations instead of
    custom normalization rules for every scalar representation. Potentially slower
    repeated table comparisons are an accepted tradeoff.

    Remove custom Arrow-buffer normalization where it exists solely for integrity
    or equality. Current fingerprints normalize offsets, null payloads, dictionaries,
    and nested arrays. Fully removing that implementation depends on the remaining
    cache-key decision. A byte checksum identifies stored bytes; different physical
    arrangements of logically equal records can have different checksums.

    Trace invocation, table, and row references remain independent of content
    hashes. Equal values do not establish that records have the same provenance.

    Location-data identity is fully captured by
    `(dataset_id, revision, location_id)`, including `revision=None`, for
    provider-loaded and caller-supplied locations, including locations prepared
    externally. Trust this identity without content hashing, comparison, or
    additional identity verification. Location content comparisons and artifact
    integrity checks serve their separate purposes; neither is required to
    establish location-data identity.

    **Open.** Specify the complete cache key with the per-location caching design.
    Combine the accepted location-data identities with author-managed
    implementation identities, parameters, selected input membership, and other
    effective dependencies as required by the chosen strategy.

    [Fingerprint implementation](src/lyra/data/content_fingerprints.py),
    [location equality](src/lyra/data/locations.py),
    [IPC verification](src/lyra/data/ipc_codec.py).

16. **Keep synchronous and asynchronous clients with simpler transports.**

    **Accepted.** Retain synchronous and native asynchronous consumer and
    administrator clients, backed directly by HTTPX's `Client` and `AsyncClient`.
    Remove the background event-loop thread and synchronous-to-asynchronous bridge.
    Share endpoint definitions, request construction, response validation, error
    interpretation, and table conversion across both interfaces, including Arrow
    downloads, paginated rows, and trace inspection.

    Replace the generator-based polling protocol with two ordinary loops sharing
    retry, progress-comparison, and deadline helpers. Accept small amounts of
    duplication around requests, sleeping, streaming, and context management;
    verify both interfaces rather than introducing machinery solely to eliminate
    those differences.

    Adopt standard HTTPX connection, read, write, and pool timeouts, with polling
    deadlines checked between operations. An in-flight request or cleanup may
    exceed the overall polling deadline; strict total wall-clock bounds are not
    guaranteed. Async requests run in the caller's task and follow ordinary
    cancellation and cleanup behavior. Callers finish or cancel and await
    outstanding operations before closing the client. Remove Lyra's custom task
    tracking and background cleanup ownership; immediate return independent of
    cleanup completion is no longer a guarantee. Local cancellation does not
    cancel a remote job.

    [Synchronous bridge](src/lyra/api/client/bridge.py),
    [transport lifecycle](src/lyra/api/client/http.py),
    [polling protocol](src/lyra/api/client/polling.py),
    [HTTPX async support](https://www.python-httpx.org/async/),
    [HTTPX timeouts](https://www.python-httpx.org/advanced/timeouts/).

17. **Let FastMCP own tool registration and validation.**

    **Accepted.** Register typed handlers through FastMCP bundled with the MCP
    SDK. Derive tool schemas from function signatures and response models, and
    delegate argument validation and dispatch to the SDK. Remove the separate
    tool-contract registry, duplicate schema adapters, name-based dispatcher,
    and low-level registration code.

    Preserve authentication, tool annotations, bounded responses, and shared domain
    operations. Use the same approach for paginated row access and trace inspection.
    MCP remains part of the existing API deployment; no additional service is
    required.

    Adopt standard SDK validation errors in place of custom argument-correction
    payloads. Accept straightforward SDK-compatible response shapes, including a
    `result` wrapper where required for union responses. Exact models remain an
    implementation detail. Verify advertised schemas, actual result envelopes,
    authentication, and response limits through in-process MCP integration checks.

    [Tool contracts](src/lyra/server/mcp/models.py),
    [dispatcher](src/lyra/server/mcp/tools.py),
    [server registration](src/lyra/server/mcp/server.py),
    [FastMCP implementation](https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/src/mcp/server/fastmcp/server.py).

18. **Preserve automatic location route discovery.**

    **Rejected:** requiring each metric input to declare its supported conversion
    sequences. Automatic composition is essential to the developer workflow.
    Retain shortest-route discovery across installed plugins, ambiguity errors,
    explicit preferences for resolving ties, and distinct operation and dataset
    identities. Developers need not enumerate every supported conversion chain.

    Traceability records the selected operations and their declared dependencies;
    it does not require replacing automatic route discovery.

    [Route compiler](src/lyra/runtime/planning/catalog.py),
    [routing behavior tests](tests/test_canonical_routes.py).

19. **Remove stale feature-payload validation and simplify compatibility checks.**

    **Accepted.** Replace production `LocationPayload` calls, which pass empty
    feature lists, with focused scope and CRS validation. Remove the unused
    feature-list validation path and update test helpers to construct locations
    through supported public APIs. Preserve required validation of actual location
    contents at the GeoDataFrame/Arrow construction boundaries and the JSON
    geometry conversion needed for previews and paginated row access.

    Reduce the authoring compatibility checker to a supported-version check and
    a representative public authoring workflow, including GeoDataFrame-to-`Locations`
    construction. Replace checks coupled to internal payload schemas with checks
    of the supported workflow. Less exhaustive SDK compatibility diagnosis is an
    accepted tradeoff; ordinary authoring behavior and required data validation
    remain supported. Apply the accepted single-owner validation principle when
    consolidating these boundaries.

    [Payload model](src/lyra/data/location_payload.py),
    [location construction](src/lyra/data/locations.py),
    [schema builder](src/lyra/sdk/location_schema.py),
    [compatibility checker](skills/lyra-metric/scripts/check_compatibility.py).

20. **Register datasets once with minimal metadata and geometry schemas.**

    **Accepted.** Replace separate provider `collections`, `outputs`, and
    `contracts` declarations with one dataset registration list. Each registration
    pairs a small dataset descriptor with its executable schema:

    | Field | Purpose |
    | --- | --- |
    | `id` | Required stable, unique dataset identifier, such as `city.streets`. |
    | `revision` | Optional opaque version token; `None` means unversioned. |
    | `description` | Optional plain-text context about origin, coverage, or interpretation. |
    | `schema` | Executable structure, validation, row identity, and geometry requirements. |

    Derive discovery metadata and runtime lookups from these registrations.
    Replace the compound family/source/edition identity with the dataset ID;
    separate geographical editions, observation periods, releases, and
    field-definition mappings are omitted from the descriptor. Multiple datasets
    can share a schema while retaining distinct IDs. Provider acquisition remains
    responsible for returning the requested dataset revision and rows.

    Require the provider schema's geometry column to declare its expected CRS.
    Derive advertised CRS and correctly tagged empty results from that schema;
    do not add a separate CRS field to dataset registration. Different CRSs need
    corresponding schema variants. Geometry constraints belong in the schema.

    `(dataset_id, revision, location_id)` fully captures location-data identity and
    identifies frozen contents, including when the revision is `None`. This
    contract applies equally to provider-loaded and caller-supplied locations,
    including locations prepared externally. Newly constructed locations receive
    their identities outside Lyra's location operations and are exposed through
    registered datasets, following the initial implementation boundary in item 4.
    Producers own the identity guarantee.
    Lyra trusts the declared tuple without reading, hashing, comparing, or otherwise
    verifying the contents to establish identity. Existing schema and structural
    validation remains independent of this trusted identity contract.

    Unversioned locations have the same immutability guarantee: `None` is a stable
    identity value, not a reference to changing latest contents. A change to covered
    geometry, attributes, or relationship fields stored in the dataset record
    requires a different revision value, location ID, or dataset ID. Producers and
    callers own this guarantee; Lyra neither interprets version tokens nor detects
    unreported changes. A revision identifies data without creating a historical
    snapshot. Record dataset ID and revision alongside invocation and row lineage.
    Include descriptions in discovery and retained provenance, but exclude their
    text from dataset identity, routing, and cache keys.

    Computed operation mappings are computation results with their own identity,
    separate from the identities of the participating location records. Changing
    an operation's implementation or parameters can change its mapping without
    changing those records. For example, different search radii can map the same
    building to different hospital selections while all location identities remain
    unchanged. Record the operation implementation, effective parameters, and
    inputs in its computation identity as specified in item 9.

    Dataset ID and revision do not constitute a complete cache key; location
    selection, calculation identity, parameters, and other effective inputs still
    matter. Exact keys remain open for the per-location specification.

    Metric inputs and location operations reference dataset IDs. Preserve automatic
    discovery of operation chains between them. Matching row IDs across datasets
    or revisions establishes no geographic equivalence automatically; authors
    declare needed relationships explicitly. Losing the built-in distinction
    between shared geography and different observations is an accepted concession.
    Constructor naming and internal indexes remain implementation details.

    [Provider declarations](src/lyra/sdk/locations.py),
    [dataset contracts](src/lyra/contracts/locations.py),
    [geometry schema validation](src/lyra/sdk/location_schema.py),
    [city provider](examples/city-plugin/city_example/provider.py).

21. **Use GeoParquet exports with small embedded metadata records.**

    **Accepted.** Keep GeoParquet as Lyra's managed GIS export through a thin
    GeoPandas/PyArrow implementation. Callers use GeoPandas `to_file` directly for
    Shapefile, GeoPackage, or other formats and accept the driver's conversion
    behavior. Remove custom driver restrictions, format-specific conversions, and
    routine write/read/fingerprint verification. Adopt library preservation
    semantics, with representative round-trip tests for supported behavior.

    Produce a single GeoParquet file with a small Lyra metadata record embedded
    through GeoPandas `attrs`; remove the separate scope sidecar. Include dataset
    ID, revision, and description when exporting a registered dataset, and an
    originating Lyra table/result reference when available. Preserve declared row
    keys as ordinary columns. Geometry, CRS, and column types use standard
    GeoParquet representation. Do not assign a computed table one input dataset's
    identity when it combines several sources.

    Full lineage remains in Lyra's retained store. Full trace inspection therefore
    requires access to that store; an export does not bundle its dependency graph.
    Third-party software may discard custom metadata when rewriting the file.
    These portability limits and the removal of Lyra's exact-preservation guarantee
    are accepted concessions. Arrow downloads and JSON inspection remain separate
    supported interfaces.

    Require a GeoPandas version supporting Parquet attribute persistence. A small
    in-memory check with the installed version preserved the metadata, CRS, and
    row ID through a write/read round trip. Broader verification belongs to
    implementation; metadata field spelling and reference encoding remain details.

    [GIS exporter](src/lyra/data/location_exports.py),
    [GeoPandas Parquet writer](https://geopandas.org/en/stable/docs/reference/api/geopandas.GeoDataFrame.to_parquet.html),
    [GeoPandas attribute persistence](https://geopandas.org/en/stable/docs/changelog.html).

22. **Remove unused types and share result-reference handling.**

    **Accepted.** Delete the unused `ResultReference` and `Bounds` types and the
    empty `server/loaders` package. Centralize live result-reference formatting
    and parsing in the shared contracts layer, replacing overlapping definitions
    in contracts, MCP, and the Python client. Retain raw-job-ID convenience at
    the client boundary, normalizing it before shared reference handling.

    New table and row references follow the same ownership principle: shared
    definitions and validation used by each transport. Exact syntax remains an
    implementation decision. Removing unused public names is acceptable in closed
    alpha; active reference functionality remains supported.

    [Result contracts](src/lyra/contracts/jobs.py),
    [geometry contracts](src/lyra/contracts/geometry.py),
    [client parser](src/lyra/api/client/base.py),
    [MCP references](src/lyra/server/mcp/tools.py).

23. **Test supported behavior instead of historical structure.**

    **Accepted.** Remove obsolete API-absence checks and assertions that preserve
    incidental implementation choices, such as exact fingerprint-helper call
    counts. Apply this cleanup selectively during the relevant refactors. Preserve
    checks of supported public interfaces, returned values, mutation isolation,
    cache behavior, and meaningful resource boundaries. When one test mixes these
    concerns, retain its behavioral assertions. A cache hit skipping the handler
    remains a useful behavioral requirement.

    Measure execution time and memory through the existing ASV benchmarks.
    Optimization regressions may appear in benchmark comparisons rather than fail
    unit tests immediately; this is an accepted concession. Retain targeted
    regression tests for deliberate performance requirements.

    **Implementation requirement.** When implementing this step, update
    [AGENTS.md](AGENTS.md) with guidance against adding tests whose only purpose
    is to enforce retired API/module absence, historical internal organization,
    or incidental algorithm choices and helper call counts. Direct future tests
    toward supported behavior and observable invariants. Allow internal assertions
    when they enforce an explicit correctness, resource, or performance requirement,
    with that requirement made clear in the test.

    [Import tests](tests/test_public_imports.py),
    [fingerprint call-count tests](tests/test_location_performance_acceptance.py),
    [benchmark guidance](benchmarks/USAGE.md).

24. **Keep the first implementation focused.**

    **Deferred from the initial implementation.** Graph visualization, automatic
    recursive expansion, dedicated downstream search, partial traces for failed
    executions, arbitrary file-artifact lineage, explicit intermediate-output capture,
    fine-grained cell or parameter influence tracking, and automatic artifact
    cleanup can follow real usage. Automatic lineage inference and semantic
    completeness checks are outside scope. Differentiability is a separate
    long-term goal and is not specified here.

    Creating and managing new locations through operations, including location
    merging and splitting, is outside the initial implementation. Operations map
    existing locations; external preparation and dataset registration supply newly
    constructed ones.

    Per-node caching modes, trusted location-data identities, and author-managed
    implementation identities are accepted. Complete cache-key composition and the
    cache backend remain open for the per-location specification.

Implementation touches the existing [node definitions](src/lyra/sdk/plugin.py),
[output contracts](src/lyra/contracts/outputs.py),
[dependency bindings](src/lyra/contracts/dependencies.py),
[provenance records](src/lyra/contracts/provenance.py), and
[executor](src/lyra/runtime/execution.py). The accepted direction still requires
resolution of the review issues below, alongside cache keys and the backend for
the accepted per-node caching strategy. Incorporate those decisions into the
working contract before translating it into concrete authoring interfaces,
runtime and storage changes, and end-to-end verification. Deferred features remain
outside that implementation scope.

## Open review issues

These issues track potential conflicts, remaining behavioral decisions, and
accepted constraints incorporated into the feature contracts above.
The `R1`–`R10` identifiers are stable and separate from the feature numbers above.
Resolve each issue individually by recording its decision, updating the affected
feature contracts, and marking the issue resolved.

### R1. Row identity across assembled cache fragments

**Open — potential conflict.** Affects items 3, 4, 9, and 14.

Row keys must be unique within a table but may repeat across producing
invocations. Per-location caching assembles rows from multiple invocations into
one result. Two independently valid fragments could each contain row key `1`,
leaving the assembled table with duplicate keys. Downstream lineage identifies an
input row by argument and key, so the ambiguity also affects dependency references.

**Accepted constraint.** Operation correspondence rows are distinct input/target
location pairs. Sharing a target ID across different input locations does not
duplicate relationship-row identity. Their dataset/revision scopes qualify both
ends of the pair.

**Decision needed.** Specify metric row-key stability and uniqueness across cache
fragments and the concrete encoding of operation-pair row references. Define how
downstream handlers address assembled rows and how those identities map to the
original producing invocations without losing either origin.

### R2. Cached ancestry after ordinary execution with opaque dependencies

**Open — potential conflict.** Affects items 1, 9, 10, and 12.

Ordinary execution permits opaque dependencies, while cached traceable results
are promised lineage through to their declared source boundaries. A traceable
node downstream of an opaque node cannot necessarily provide that ancestry,
even though its own handler returns lineage. The producer's traceability role
alone does not establish that a cached entry has a complete trace.

**Decision needed.** Distinguish producer role from cache-entry trace completeness.
Specify whether incomplete entries remain usable for ordinary execution, what
ancestry they retain, and how traced execution rejects them. Clarify which
ancestry ordinary executions retain for caching independently of optional final
trace retention.

### R3. Dependencies of empty results and table membership

**Open — specification gap.** Affects items 4, 5, 12, and 13.

Every lineage relation requires an output row. If a filter returns no rows for a
building and a downstream calculation returns `count = 0` for that building, the
count still depends on the filter's inputs and parameters. Following the empty
table's members supplies no rows whose lineage can explain that result. Retaining
membership records the outcome but does not by itself describe what caused it.

**Accepted requirement.** Successful empty operation groups must remain
explainable: identify the operation and input location that produced no
correspondences, together with the contributing inputs and effective parameters.
An empty group is distinct from a target-resolution error. The representation and
authoring API for these dependencies remain open for a later discussion; accepting
this requirement does not select a partition-level lineage mechanism.

**Decision needed.** Define how whole-input references expose dependencies of
table membership and emptiness, including upstream parameters when
there are no output rows. Decide whether partition-, table-, or invocation-level
dependencies are needed, and how the shared inspection operation traverses them
without requiring intermediate values to be stored.

### R4. Per-location caching behavior and eligibility

**Open — specification gap.** Affects items 1, 2, 3, 4, 8, 9, 14, 15, and 20.

Complete cache-key composition and the backend remain open. The partition rules
and input selection bindings below establish unordered output membership, the rows
a consumer receives, and upstream execution scope. Dependency identities still
require the decision listed at the end of this issue.

**Accepted constraint — trusted location-data identity.** The tuple
`(dataset_id, revision, location_id)` fully captures location-data identity and
identifies frozen contents, including when the revision is `None`. Apply this
contract equally to provider-loaded and caller-supplied locations, including
locations prepared externally. Producers and callers must use a different tuple
when covered contents change. Use the tuple directly for cache identity without
content hashing, comparison, or additional identity verification. Producers and
callers own this guarantee. Existing schema and structural validation remains
independent of it.

The frozen contents are the dataset record, including relationship fields stored
in that record. Computed operation correspondences have their own computation
identity; changing a correspondence through different parameters or implementation
does not require changing the participating location records' identities.

**Accepted constraint — author-managed implementation identity.** Use
`(node_id, code_revision)`, where the code revision is an optional opaque token
and `None` is a stable identity value. This applies to metrics, operations, and
executable provider/source handlers. It identifies an implementation, while the
location-data tuple identifies a record; the complete computation identity also
accounts for effective parameters and inputs. Plugin authors are responsible for
changing the token or node ID when implementation or behavior-affecting declarations
change.
Lyra trusts that declaration without automatic change detection, code hashing,
or inferred versions. Authors may automate token updates in their own tooling.
Include relevant upstream implementation identities in dependent computation
identities. Deployment build revisions retain their job-compatibility and
provenance purposes without acting as additional cache-invalidation tokens.

**Accepted constraint — mandatory location partitions.** Every node has a
location-based partition mapping, independently of caching strategy and
traceability role. Location-provider nodes are partitioned by their own location
identities, with one returned row per successfully resolved location. Metrics and
operations have at least one location input and partition by one named input.
Lyra selects the sole input automatically; otherwise the developer explicitly
selects one. Each metric output row has one primary partition owner, with zero or
more rows per key in each named output. Other location associations do not change
that ownership. Request-wide outputs are outside Lyra's supported contract.

**Accepted constraint — operation correspondence pairs.** Represent an operation's
correspondences as distinct `(input_location_id, output_location_id)` pairs with
declared dataset/revision scopes. Partition relationship rows by their first
component. Each input location has zero or more pairs, and different partitions
may reference the same target location without sharing the same relationship row.
This follows the same ownership and caching model as metric result rows. These
associations do not replace dependency lineage. Targets must be existing locations
resolvable through the declared target dataset's provider. Creating locations
through operations, including merging or splitting, is outside the initial
implementation; the accepted boundary is specified in item 4.

**Accepted constraint — empty groups and independence.** A successful empty group
is a completed result that can be cached and reused, distinct from a cache miss.
Each per-location entry contains the location's complete group across all named
outputs. Authors are responsible for validating the independence needed for
`per_location`; Lyra does not infer or prove it. Declaring a partition mapping
alone does not make a node independently computable per location.

**Accepted constraint — operation dictionary interface.** Authors provide an
operation's correspondence payload as a dictionary from input location IDs to
tuples of target IDs. Keys must belong to the supplied partitioning input. A
successful return covers every supplied key; omitted keys and empty tuples both
normalize to completed empty groups. Reject repeated target IDs within a tuple;
allow the same target under different input keys. Target-tuple order and dictionary
insertion order have no semantic significance; assemble groups by explicit keys.
These rules apply to the current invocation's supplied inputs.
The dictionary is a Python interface using string location IDs with strict
Pydantic validation. JSON uses string keys and arrays of target strings; Arrow
uses string input IDs and lists of target strings, as specified in item 4.
Traceable operations also provide lineage.

**Accepted constraint — explicit input dependencies.** Source and traceable
authors declare dependencies on supplied inputs. Lyra does not automatically link
every source output to every supplied input. Explicit references to an entire
named input as bound remain supported, including its selected membership and
explicit data. Different location partitions may depend on a shared input without
depending on one another. Authors remain responsible for the independence promise.
Input selection and upstream execution scope follow the accepted rules below.

**Accepted constraint — consumer input selection.** Each consumer's named input
has partition-related or shared selection semantics, as specified in item 3.
Partition-related inputs supply the rows associated with requested primary keys;
shared inputs retain their complete bound selection across partitions. Infer
partition-following behavior when a dependency explicitly forwards the consumer's
primary location input into its own partitioning input, or an operation derives
targets from that primary input through its correspondence mapping. The primary
input itself follows the node's partition declaration.

For other inputs with no established relationship, require an explicit shared
binding or a declared correspondence. An explicit shared declaration can override
partition-following behavior. Matching names, types, or IDs alone is insufficient.
Put the optional selection setting on existing input declarations; concrete API
spelling remains an implementation choice. These semantics apply independently of
cache mode. They determine consumer input selection; reducing producer execution
also requires the independence promise described below.

**Accepted constraint — upstream execution scope.** Cache-driven reduction of
downstream work may narrow an upstream partition request only when that producer
declares `per_location`. The producer then checks its own cache and computes only
its missing requested keys. Producers using `off` or `full` retain their original
bound invocation for the request, before cache-driven reduction. When the upstream
result is needed, `off` executes that invocation, while `full` reuses a matching
complete result or executes it on a miss. Original bound inputs describe the
request's selection, not the entire dataset.

For a producer originally bound to A/B/C when only downstream B/C remain to be
computed, `off` and `full` operate on A/B/C; `per_location` receives B/C and may
compute fewer keys after its own cache lookup. The partition-related consumer
receives only the B/C groups in every case. Shared inputs remain at their complete
bound selections, including during reduced execution. Apply these rules at each
upstream dependency.

**Accepted constraint — node-local `off`.** Disabling a producer's own cache does
not require its execution when a valid downstream cache hit satisfies the request.
`off` means execute whenever invoked; it does not propagate freshness or disable
downstream caching. Authors needing fresh external observations must disable
caching on affected downstream computations or expose changes through declared
input identities. Lyra does not infer freshness requirements from this mode.

**Accepted constraint — unordered collections.** Location selections, operation
target groups, and keyed table rows have no semantic ordering. Lyra makes no
developer-facing ordering guarantees for forwarding, execution, cache retrieval,
assembly, or transport. Resolve records and relationships by explicit keys and
columns by name. Reordering a collection alone does not change its computation
identity. Authors must make meaningful sequence information explicit in data or
parameters and produce the same logical keyed results for permutations of input
rows. Explicit sequence-valued fields and parameters remain significant. Internal
sorting for canonical key serialization or alignment does not establish a public
ordering contract. Pagination traverses a fixed result without missing or
repeating row identities, as specified in item 14.

**Accepted constraint — shared mapping storage and reuse provenance.** Operations
use the same cache machinery as metrics. Store an operation mapping once in the
shared result layer, with cache lookup and provenance referring to that result.
The mapping preserves membership and completed empty groups. Storage supports
caching without trace retention, and retaining a mapping for tracing under `off`
does not enable that node's own cache reuse. A valid downstream cache hit may
bypass its invocation. Reuse preserves the original production history together
with the current use's separate associations and selection history. Sharing
location records does not merge selection paths.

The complete computation key must still combine these implementation identities
with parameters, selected input membership, and other dependencies. The remaining
cache-key design and the rest of R4 stay open.

**Decision needed.** Specify how identities of the selected partition-related and
shared inputs, including explicit whole-input dependencies, participate in
per-location reuse while preserving the independence promise.

Use these rules to define which nodes and output declarations may select
`per_location`. Resolve assembled row identity separately under R1.

### R5. Caller inputs and provider acquisitions in the trace graph

**Open — specification gap.** Affects items 1, 2, 3, and 20.

Row references require a producing invocation, but caller-supplied native tables
and locations do not have a specified producer. Source nodes and dataset
providers are both introduced without defining whether provider acquisitions
automatically become source-node invocations or require author declarations.

**Accepted constraint.** Caller-supplied locations use the same trusted
`(dataset_id, revision, location_id)` identity as provider-loaded locations, as
specified in item 20 and R4.

**Decision needed.** Define how caller inputs and provider acquisitions enter the
graph, how their producing invocations and source metadata are assigned, and
which row-key requirements apply. Specify how their declarations participate in
eligibility checks before resource acquisition and how dataset identities relate
to source identities.

### R6. Location association targets and cardinality

**Open — specification gap.** Affects items 3, 4, and 20.

**Accepted constraints.** A primary association identifies a location in the
selected named input, scoped by dataset ID and revision. Every metric result row
has one partition owner, with zero or more rows per location in each named output.
An operation correspondence row contains input and target location IDs qualified
by their declared dataset/revision scopes. Several correspondence rows may refer
to the same target location while belonging to different input partitions.

Operation correspondences use the dictionary-of-tuples Python interface specified
in item 4. Omitted input keys and empty tuples both represent zero matches; the
structural key-membership and target-uniqueness checks are specified there.

**Accepted constraint — forwarding and resolution.** Downstream location tables
contain each distinct target once, without a row-order guarantee. Keep all
correspondence pairs separately, including relationships that share a target.
This preserves the one-row-per-location rule for dataset/provider records while
allowing multiple operation rows per input.
Retain immediate mappings along operation chains. Unresolved targets produce an
error during normal acquisition and are not silently converted into zero matches;
no separate preflight existence lookup is required.

**Accepted constraint — mapping representation.** Individual IDs are strings.
Python mappings use `dict[str, tuple[str, ...]]`; JSON uses objects with arrays of
target strings, and Arrow uses string input IDs and lists of target strings.
Preserve membership and empty groups. Use separate input and target fields for
relationship references, with their surrounding scopes, as specified in item 4.

**Accepted constraint — existing target locations.** Every operation target must
resolve through the declared target dataset's provider. One-to-many and
many-to-one correspondences between existing locations remain supported.
Operations may perform spatial calculations to determine correspondences, but
creating locations, assigning their identities, and registering their geometries
and attributes are outside the initial implementation's operation contract. Newly
constructed locations, including merged or split locations, are prepared externally
and exposed as a registered dataset. Their external preparation lies outside Lyra's
trace graph.

**Decisions needed.** Specify metric-association representation and validation,
and the cardinality and null handling for secondary location associations.
Concrete operation-row reference envelopes remain under R1. Keep a target
location's own identity distinct from its association rows and dependency lineage.

### R7. Mechanical lineage validation guarantees

**Open — specification gap.** Affects items 3, 4, 5, and 6.

Authors own semantic completeness. Location-ID types and operation-mapping
checks are specified below; remaining structural decisions concern other row-key
types, source-result row identity, and whether references to unknown output names,
input arguments, or nonexistent rows are rejected.

**Accepted constraint — location ID type for the initial implementation.** Use
strings for location records and operation mapping keys and targets. Delegate
validation and schema generation to Pydantic, using strict validation without
coercion from other types. Plugin authors expose numeric or composite source IDs
as stable, unambiguous strings. Standard JSON and Arrow types handle mapping
encoding as specified in item 4. The types of other metric-row keys and their
reference representation remain open.

**Accepted constraint.** Operation mappings reject input keys not supplied to the
invocation and repeated target IDs within a tuple. Omitted supplied keys normalize
to empty tuples after successful completion. These checks supplement the accepted
location-ID type contract; the remaining lineage-reference checks are still open.

**Decision needed.** Specify those validation guarantees and whether a row may
have no explicit input relations, such as a constant or parameter-only result.
Keep these mechanically enforceable rules distinct from the semantic completeness
that Lyra does not verify, and assign their checks to the shared validation
boundary.

### R8. Parameter normalization across the API and worker

**Open — specification gap.** Affects items 5, 7, and 8.

The API queues effective parameter values, while workers prepare execution using
live Pydantic definitions. The contract does not establish whether worker
validation may transform those values again, when defaults are evaluated, or
which relaxed parameter types and serializers must survive JSON transport.

Parameter origins also need a consistent interpretation for nested fields and
forwarded defaults. A downstream parameter may originate immediately from an
upstream binding and ultimately from an upstream default; both interpretations
currently fit the text.

**Decision needed.** Define the normalization and transport rule ensuring that
accepted effective values are the values executed. Specify default evaluation,
worker revalidation behavior, and the supported serialization contract. Define
the granularity of origin recording and how immediate and ultimate origins are
represented when values are forwarded.

### R9. Traceability and automatic route selection

**Open — specification gap.** Affects item 1 and the retained routing behavior in
item 18.

If the shortest route contains an opaque operation but a longer traceable route
exists, the contract does not explicitly say whether traced execution rejects
the normally selected route or searches among traceable routes. Filtering routes
for traceability could also resolve an otherwise ambiguous route. The wording
suggests ordinary route selection followed by eligibility checking, but does not
state that behavior directly.

**Decision needed.** Specify whether tracing mode affects route discovery,
shortest-route selection, tie preferences, or ambiguity handling, and when an
opaque selected route causes rejection.

### R10. Durable trace access from results and local executions

**Open — specification gap.** Affects items 8, 11, 12, 13, 14, and 22.

Inspection starts with a row reference, but the contract does not explain how a
caller obtains that reference from downloaded or paginated results, especially
when rows come from several producing invocations. Persistence expectations for
local executions without queued jobs are also unspecified.

**Decisions needed.** Specify:

- Whether result interfaces expose row references directly or support lookup by
  table reference and row key, including assembled results.
- What store and persistence behavior local traced executions require, and how
  callers subsequently resolve their references.
- When a traced result may be published as successful relative to durable storage
  of its complete, resolvable lineage.
- How inspection distinguishes intentionally unstored intermediate values from
  missing or damaged trace metadata.
