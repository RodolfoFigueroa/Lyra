# Execution reporting and caching implementation ledger

Lyra should automatically build an execution report for managed calculations.
The report describes the normalized request, selected computation graph, effective
parameters, declared sources and revisions, node execution/cache-reuse/skipped
outcomes, and final status. It supports location-scoped, unpartitioned, and hybrid
graphs. A durable execution ID provides access independently of transient job
records. Successful results require durable storage of the required outputs and
report. Finding-level provenance and interactive row/group trace inspection are
deferred until a concrete workflow justifies them.

Partitioning, stable row identities, operation correspondences, dependency
bindings, per-location caching, result completion, and ordinary result retrieval
remain part of the initial implementation. Retain their records wherever needed
for execution, caching, or retrieval, without additional retention solely for
finding-level explanations. This ledger records the accepted design and resolved
review decisions; it does not describe an implemented capability.

Keep the implementation small and place responsibility for accurate declarations
on plugin authors. Closed-alpha status permits breaking changes without migration
or compatibility machinery. There are no existing users or deployments to
preserve. Legacy-result readers and historical-result compatibility across
incompatible code or schema changes are outside scope; a development-store reset
is acceptable. Results and execution reports remain accessible within the
supported current store.

All executable nodes and data sources are assumed deterministic over frozen,
immutable inputs. The same metric or operation computation key identifies the
same logical output values and membership, including empty results. A provider
record's identity `(dataset_id, revision, location_id)` identifies its
immutable geometry and attributes independently of acquisition batches or
provider implementation. Authors represent every output-affecting change
through the applicable declared identities or effective parameters. These
guarantees apply with caching enabled or disabled; physical row order remains
insignificant.

1. **Use one runtime and report every managed execution.**

   **Accepted.** Node kinds define the provider, metric, and location-operation
   interfaces. Metrics declare one output scope: location scoped or unpartitioned.
   Every location-scoped metric declares the standard `location_id` and `row_key`
   columns in each named output's Pandera schema under item 3. Unpartitioned
   metrics return ordinary named tables without mandatory ownership or row keys.
   Providers and operations
   retain their location-record and correspondence identities. All use the same
   dependency resolution, executor, resource lifecycle, validation, storage, and
   transports, with ordinary return contracts for their node kinds.

   All authors declare graph dependencies and external origins under item 2,
   including for unpartitioned metrics and caching off. Authors own accurate
   declarations; Lyra does not inspect handler code to prove compliance.

   **Accepted automatic reporting.** Clients submit one request type. Lyra selects
   the graph using normal routing and validates the complete execution plan before
   resource acquisition or handler execution. Missing or incompatible structural
   column declarations are invalid definitions; do not silently change output scope.
   Output scope governs partitioning, validation, and caching, not eligibility for
   an execution report. Location-scoped, unpartitioned, and hybrid graphs all use
   the report contract in items 4, 12, and 13.

   Lyra constructs the report from the normalized request, resolved graph, effective
   parameters, declared sources, and actual execution/cache outcomes. Report reuse
   without claiming the handler ran or following the historical cache producer.
   Clients and handlers do not select a reporting mode or retention switch.

   **Accepted durable success.** Persist required final results, their retrieval
   descriptions, and the execution report before returning local success or
   reporting remote success. Failure to persist the required report fails the
   execution; a successful result cannot silently lack its report. Expose the
   durable execution ID through the run/result interface, alongside ordinary
   outputs, independently of transient job records. R10 settles access, local
   storage initialization, and publication behavior.

2. **Declare external origins on ordinary node definitions.**

   **Accepted external-origin metadata.** Provide a small, immutable `Origin`
   value type for external data that metrics and operations acquire directly.
   Each origin has a required stable `id`, an optional opaque `revision`, and an
   optional plain-text `description`. An origin need not be a registered location
   dataset. Authors may define an origin once and reuse it across node and
   resource-binding declarations. Origin identity uses `(id, revision)`,
   independently of Python object identity; separately constructed objects with
   the same identity are equivalent for cache-key purposes.

   Declare an origin on every node that directly acquires data from it, including
   when that origin is also exposed through an upstream dependency. A node that
   consumes the source only through upstream outputs needs no direct declaration
   for it. For example, metrics A and B may reuse the same census origin when both
   read census data directly; B needs no direct census declaration when it only
   consumes A's output. An empty effective direct-origin collection, after combining
   the declarations below, promises that the handler acquires no additional
   external data.

   **Accepted I/O resource integration.** Allow zero or more `Origin` values on
   each existing `Resource(...)` binding used by a metric or operation. The binding
   identifies both the named resource to inject and the external data that the
   consuming node may acquire through that resource. Authors may reuse the binding:

   ```python
   census_origin = Origin(id="national-census", revision="2025")
   census_access = Resource("census_db", origins=(census_origin,))
   ```

   A handler argument annotated with
   `Annotated[CensusDatabaseIO, census_access]` contributes the census origin to
   that node's declarations without repeating it in the metric or operation
   decorator. Explicit node-level `origins` remain supported, including for
   acquisition outside managed I/O resources. Using resources is not a prerequisite
   for declaring origins or performing declared external acquisition.

   The effective direct-origin collection is the union of explicit node origins
   and origins on its resource bindings, deduplicated by `(id, revision)` within
   that node. Use this same collection for discovery, execution reporting, and
   computation keys. Collect it during registration and plan preparation, before
   resource initialization, credential resolution, service access, or cache lookup.
   Cached and skipped nodes retain their declarations without claiming acquisition.

   Origins attached to a resource describe that node's use of the resource.
   Different nodes may declare different origins while sharing one database
   resource, and one binding may declare several origins. Resource names continue
   to control configuration and instance sharing; origin metadata does not change
   resource lifetime or create
   separate instances. Resource names, credentials, and connection settings do not
   become additional origin identity or computation-key inputs. Authors must
   represent any output-affecting differences through the applicable declared
   identities or effective parameters under the deterministic contract.

   This integration uses ordinary declaration processing and the existing resource
   lifecycle. It requires no separate origin registry, query interception,
   method-level source tracking, or per-call acquisition records. Existing resource
   operation logs remain diagnostics; reporting describes declared boundaries and
   observed node outcomes.

   These declarations are required whenever direct external acquisition occurs,
   including in unpartitioned metrics and with caching off. Authors own complete
   and accurate dependency and origin declarations. Undeclared external acquisition
   is a plugin-contract violation; Lyra does not inspect code or enforce I/O behavior
   to discover it. Internal queries, file reads, and processing remain outside the
   execution-report guarantee. Connection details, query text, file hashes, and
   explanations of internal processing are not required origin metadata.

   **Accepted provider origins.** Dataset registration automatically
   establishes the provider's external boundary. Derive its origin ID,
   revision, and description from the requested dataset registration, without
   another declaration or origin registry. A provider serving several datasets
   uses the requested dataset's metadata. Keep the provider's node ID and code
   revision as its implementation identity in the execution report. Provider
   record cache keys and downstream location-input identities use only the
   immutable location-data identity. Provider declarations participate in graph
   validation before resources are acquired.

   Origin IDs and revisions participate in the applicable value identities
   under item 9; descriptions are retained for discovery and execution reports
   but do not affect cache keys. Provider origin identity is already
   represented by its dataset ID and revision. `revision=None` identifies
   fixed, unversioned input, not changing latest data. Changed external
   contents require a changed declared identity or effective key input under
   the deterministic contract. Reporting neither archives external data nor
   guarantees its future availability. Exact declaration spelling remains an
   implementation choice.

   **Accepted acquisition and graph context.** The execution report identifies
   declared origins and whether their nodes executed, reused cached results, or
   were skipped. Record provider acquisition only when its handler runs. A cache
   hit or bypassed node does not claim a fresh acquisition. Direct-origin
   declarations describe a handler's external boundary; reporting does not observe
   its internal queries or prove which declared sources it accessed.

   External boundaries preserve all declared dependencies inside Lyra. The report
   describes the selected graph and named bindings, while the runtime uses actual
   location selections and table dependencies to execute the graph and compute
   cache keys. No durable row-level selection history is required for the report.

   Dataset providers follow the narrower lookup contract in item 20. An upstream
   computation may select IDs to retrieve, but a hospital record's contents do not
   depend on the building or radius that caused it to be selected. This introduces
   no arbitrary calculation parameters or upstream-table arguments into the
   provider interface. Metrics and operations acquiring external data retain their
   declared calculation inputs.

   **Accepted caller-input scope for the initial implementation.** All managed
   local and remote executions, including `run_development`, accept parameters
   and location selections identifying registered datasets and requested IDs.
   Location geometry and attributes enter through registered dataset providers,
   following the accepted execution, cache-reuse, and skipped-acquisition rules.
   Handlers receive validated location inputs with the standard columns and
   dataset/revision metadata in items 3 and 4. Direct provider inputs contain
   location records; operation-derived inputs contain correspondence rows joined
   to target records. Table dependencies are supplied by Lyra through execution
   or cache reuse of declared upstream nodes.
   External tabular data enters through declared nodes with external-origin
   metadata for their direct acquisitions.

   **Accepted development roots and shared execution.** `run()` selects public
   metrics. `run_development()` selects concrete metrics, including private
   metrics, while accepting the same location-selection form. Development root
   selection permits private roots before shared request preparation. Both paths
   share selection normalization, parameter and plan validation, provider
   acquisition, and the executor's dependency resolution, result validation,
   caching, and execution-report machinery. Public metric discovery and remote root
   submission retain their public-metric restrictions. Declared dependencies
   retain their ordinary execution behavior.

   Direct injection of full location records, arbitrary DataFrames, and
   caller-supplied stored-result references as table-dependency replacements is
   outside the initial managed-execution contract. Direct Python calls to handler
   functions remain outside managed graph execution, caching, and automatic
   execution reporting. R5 records the accepted provider-acquisition and
   external-origin contract within this scope; caller-selected IDs remain part
   of the current request's input context.

   Authors decide where boundaries belong. The execution report describes the
   selected graph, named bindings, and declared sources, distinguishing actual
   execution from reuse or skipped work. It does not trace individual findings or
   reconstruct the historical execution that populated a cache entry.

3. **Give outputs explicit scope, identity, and location ownership.**

   **Accepted standard structural columns.** Use these names consistently at
   authoring, handler-input, storage, and retrieval boundaries:

   | Table kind | Required structural columns | Row identity within its scope |
   | --- | --- | --- |
   | Provider location records | `location_id`, `geometry` | `location_id` |
   | Operation-derived location input | `source_location_id`, `location_id`, `geometry` | `(source_location_id, location_id)` |
   | Location-scoped metric output | `location_id`, `row_key` | `(location_id, row_key)` |
   | Unpartitioned metric output | No mandatory identity columns | No required author row identity |

   `location_id` identifies the represented location in a location input and the
   owning location in a location-scoped metric output. `source_location_id`
   identifies the immediate source of an operation correspondence; `location_id`
   identifies its target. `row_key` is the author's stable string key within one
   owning location partition of one named metric output. Every spatial table uses
   `geometry` for its active geometry column, including spatial metric outputs of
   either scope. Nonspatial metric outputs need no geometry column.

   Providers and metrics explicitly declare their structural columns alongside
   ordinary attributes in their Pandera schemas, with the type, nullability,
   uniqueness, and CRS rules in item 6. Authors supply values and rename
   source-system columns before returning
   tables. Structural column names are fixed; no configurable ID, ownership,
   row-key, or active-geometry column-name declarations are needed. Authors still
   select the primary named input and declare output scope and dataset bindings.

   Reserve `location_id`, `source_location_id`, `row_key`, and `geometry` for the
   structural roles above. Ordinary attributes use other names; reject collisions
   and incompatible uses without overwriting columns or adding automatic suffixes.
   Unpartitioned outputs may contain domain-named ID columns as ordinary data, and
   use `geometry` when spatial, without acquiring location ownership or row keys.

   Named input/output metadata supplies the dataset and revision for each
   location-ID role; do not repeat that scope in every data row. Operation-derived
   inputs carry separate source and target scopes. `row_key` is additionally
   scoped to its named output. Scope and the primary-input binding determine
   partition ownership; column spelling does not infer new dependency edges.
   These contracts apply to the tables visible to handlers; wrapper classes and
   accessor names remain implementation choices.

   **Accepted partition-scoped metric row identity.** Every named output of every
   location-scoped metric declares `location_id` and `row_key` in its Pandera
   schema; the handler supplies their values. `row_key` is a non-null string,
   unique within one owning location partition of one named output. Missing or
   incompatible structural declarations make the definition invalid, regardless
   of caching strategy.
   The row's identity within that output combines the qualified partition
   `(dataset_id, revision, location_id)` with `row_key`. Different
   partitions and different named outputs may reuse the same author key.

   For example, a findings output may contain `(building A, "roof")`,
   `(building A, "entrance")`, and `(building B, "roof")`. Two rows with
   `(building A, "roof")` in that output are invalid. Authors keep keys stable
   for the same logical rows when input order changes or independently computable
   partitions execute in different batches. DataFrame indexes, incidental row
   positions, and execution-batch membership do not establish row identity.

   Downstream handlers address a location-scoped metric input row by its qualified
   partition and author key within the named input. Assembly preserves those
   identities within the current result/table and named-output scope, including
   when combining cached and newly computed partitions. Cached data requires no
   link to its historical producing invocation. The execution report records node
   execution and reuse without attaching trace references to individual rows.
   Operation rows use the qualified input/target pairs in item 4. These identity
   and assembly rules resolve R1. Metric-row keys use strings under R7 and the
   fixed column names and Pandera declarations in this item and item 6.

   **Accepted unordered collections and explicit identity.** Location selections,
   operation target groups, partition groups, and table rows have no semantic
   ordering. Lyra makes no developer-facing guarantee about input or result row
   order, target-tuple order, dictionary iteration order, or column positions.
   Associate records through qualified location IDs, operation input/target pairs,
   and declared metric-row identities; address inputs, outputs, and columns by
   name. Cache lookup, forwarding, assembly, and retrieval must not match records by
   position. Reordering a collection does not change its logical membership or
   identity, and execution or storage may change its physical arrangement.

   Handlers must produce the same logical results for permutations of the same
   supplied rows, preserving declared identities where present. Authors represent meaningful sequence information as
   explicit data, such as timestamps, ranks, priorities, or ordered parameter
   values, and use those values in their calculations. Sequence-valued parameters
   retain their order; table data expresses sequence through supported scalar
   columns such as rank or timestamp text. Internal sorting for key serialization
   or record alignment is an implementation detail and creates no ordering
   guarantee for authors or callers.

   Providers, operations, and location-scoped metrics have a location-based
   partition key, independently of cache policy. A partition is identified by
   `(dataset_id, revision, location_id)`. Metrics and operations select a
   particular named location input as the partitioning input, not merely a
   location type; two inputs can have the same type or dataset while serving
   different roles. Providers partition by their own immutable location
   records.

   The distinct `location_id` values of the selected primary input identify a
   metric's or operation's input partitions. In an operation-derived primary
   input, repeated targets remain correspondence rows within their target's
   partition. Its `source_location_id` column does not implicitly select the
   downstream node's owning dataset. An operation's own returned mapping is
   partitioned by its keys, which become `source_location_id` when forwarded.

   **Accepted unpartitioned metrics.** A metric may explicitly select unpartitioned
   output scope, overriding automatic selection of a primary location input.
   All of that metric's named outputs then belong to the complete computation,
   without a required location-owner column or stable row key. Authors return
   ordinary named tables subject to the shared output-schema and supported-type
   validation. Unpartitioned metrics may accept location inputs, table dependencies,
   and parameters; they require no primary location input or location input at all.
   Each handler invocation receives its complete bound input selections, not entire
   external datasets. Authors may explicitly bind subsets, but Lyra does not split
   the metric's work or infer output subsets by location.

   Use one output scope per metric. Mixing location-owned and unpartitioned named
   outputs within one metric is outside the initial implementation; use separate
   metrics. Providers and location operations retain their location-based contracts.
   Unpartitioned metrics support `off` and `full` caching under item 9, ordinary
   storage and retrieval under items 11 and 14, and the execution report under
   item 1. This scope changes no declared-input, source-boundary, or
   deterministic-result obligations. The spelling of the output-scope setting
   remains an implementation choice; structural column names follow this item.

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
   encoding in JSON, SQLite text columns, and Arrow transport, preserving ID
   spelling. Metric-row keys also use strings, with the partition-scoped identity
   contract above. Authors represent numeric or composite row identifiers as stable,
   unambiguous strings. Validate the string key column at the shared producer boundary
   through the ordinary output-schema machinery; use the shared strict string types
   for references accepted by public interfaces. No custom identity codec or
   runtime string/repr fallback is required.

   Dataset-provider nodes return location records from registered datasets. They
   are partitioned by their own locations, with exactly one returned row per
   successfully resolved location key. This uniqueness rule applies to location
   records, not operation correspondence rows: operations may return several
   relationships for one input location. Location-scoped metrics and operation
   nodes must have at least one location input. Select the sole location input
   automatically; when several exist, the developer explicitly selects one for
   partitioning. Explicit unpartitioned metric scope bypasses this inference.

   **Accepted primary metric ownership.** Each named output of a location-scoped
   metric uses `location_id` as its scalar owner column, associated with the
   metric's selected primary location input, such as `buildings`. Every returned
   row has a non-null string `location_id` belonging to the distinct represented
   locations actually supplied through that primary input. The named input
   supplies the dataset and revision scope. Returning an owner from elsewhere in
   the dataset or request is invalid when it was not supplied to that invocation.

   Multiple rows may share an owner; their `row_key` values distinguish them.
   A supplied owner may produce zero rows in any named output. Validate owner
   membership and non-nullness at the shared producer boundary, without an
   additional provider lookup. Multiple inputs and multiple named outputs remain
   supported. The ownership and row-key column names are fixed by this item.

   Nonstructural columns are ordinary data, including domain-named columns
   containing IDs of other locations. Apply their declared schema checks without
   interpreting them as location references, acquiring records, or inferring
   dependencies. They are
   stored, returned, and forwarded as ordinary values. Named input dependencies,
   their selection rules, and operation correspondences retain their execution
   and cache-identity responsibilities. These rules resolve R6.

   **Accepted input selection bindings.** Declare selection behavior on each
   consumer's named input, independently of the producer's cache policy or
   other consumers. A partition-related binding supplies rows associated with
   the requested primary partition keys through a known correspondence. A
   shared binding supplies the complete bound input selection unchanged across
   primary partitions; it does not imply loading the entire dataset.

   Infer straightforward cases from the actual binding declarations:

   - The primary location input follows the node's partition declaration.
   - A dependency that explicitly forwards that primary input into its own
     partitioning input follows the binding automatically.
   - An operation deriving locations from that primary input supplies the
     corresponding targets through its mapping, including through bound chains.

   For other inputs of a location-scoped consumer whose relationship to the primary
   partition is unclear, require an explicit shared binding or a declared correspondence. Matching
   argument names, location types, or IDs alone does not establish that
   correspondence. Authors may explicitly choose shared selection to override
   partition-following behavior when broader context is needed.

   Extend the existing input declarations with an optional selection setting;
   `selection="partition"` and `selection="shared"` are illustrative spellings,
   not a finalized API. This behavior applies across metric and operation
   strategies `off`, `full`, and `per_location`, and with provider caching on
   or off. Selecting the rows a consumer receives does not establish that an
   upstream metric or operation can compute only those rows. Providers may
   always acquire a reduced selection because each location identity guarantees
   an independent immutable record. Apply the upstream execution rules in item
   9 and R4 when cache hits reduce downstream work.

   **Accepted whole-table forwarding.** An unpartitioned metric receives the
   complete selections bound to its inputs. Its outputs are whole-table
   dependencies, automatically supplied as shared inputs to consumers. Do not
   infer location ownership from column names or matching IDs, or filter these
   outputs when downstream cache hits reduce the consumer's work. Reject a
   partition-selection binding on an unpartitioned output. Consumers may perform
   their own calculations or filtering on the supplied table.

   Graphs may mix location-scoped and unpartitioned metrics. For example,
   per-location building measurements may feed an unpartitioned summary, which
   a per-location flagging metric consumes as shared context alongside its building
   inputs. The summary retains its complete initially bound selection, and each
   downstream computation includes the whole summary's computation identity.
   A changed summary selection therefore changes dependent computation keys.

   Metric ownership identifies the location partition to which a row belongs.
   Operation correspondences describe returned relationships between locations.
   Preserve these relationships for execution, caching, and result retrieval.
   Neither ownership, an operation correspondence, nor a node input binding
   asserts the exact rows that contributed to a value.

4. **Keep ordinary outputs and record execution outcomes automatically.**

   **Accepted metric return contract.** Metrics return ordinary named output
   tables. Location-scoped outputs declare row keys and ownership under item 3.
   Successful completion covers every supplied primary partition in every named
   output, including partitions with no rows. Preserve completion and membership
   wherever execution, caching, or result retrieval requires them. Authors provide
   no separate lineage payload or reporting wrapper.

   A metric handler must return every declared named output and no undeclared
   output names, for both location-scoped and unpartitioned metrics. Return a
   schema-valid empty table when an output has no rows. Missing a named output is
   invalid, not successful empty completion. Column acceptance remains governed
   by each Pandera schema. Operation mappings retain their separate rule that
   omitted supplied location keys normalize to empty target groups.

   For example, a defect metric receives buildings A and B with severity threshold
   3. Its `defects` output contains only
   `{"location_id": "B", "row_key": "roof", "severity": 4}`. A completed
   partition for A has no defect rows; B's partition contains `roof`. Persist these
   partition scopes when storing the result so both are reusable under the accepted
   cache rules. The report records the threshold and node outcome, without durable
   links from either partition to a finding-level trace. Completion applies only
   after successful return and validation, not to failed or unprocessed work.

   **Accepted unpartitioned completion.** A successful unpartitioned metric return
   completes every declared named output for the whole computation. Schema-valid
   empty tables are completed outputs, not unprocessed work. Empty bound inputs do
   not imply empty outputs: a summary may return a row containing a count of zero.
   When its computation is needed, execute the handler on those inputs unless a
   permitted full-result cache hit supplies the result. Do not fabricate location
   groups or row identities. The execution report uses the same contract for both
   metric output scopes.

   **Accepted execution report.** Retain the normalized request and selected graph,
   including its named dependency bindings, node kinds, output scopes, relevant
   implementation and dataset revisions, effective parameters, and declared
   external origins. Describe each resolved node use sufficiently to distinguish
   separate uses of the same definition. These are report entries, not public
   row/group or parameter-origin reference objects.

   Record observed execution, cache reuse, and skipped outcomes. A node may both
   execute and reuse results when only some partitions miss the cache; represent
   that mixture without requiring a durable per-partition event history. Record
   actual handler execution only when it happens. A shared dependency that executes
   for another consumer is not globally skipped; only work actually bypassed is
   described as skipped. Declared graph bindings do not imply that their values
   were acquired in this execution. Report final status without fabricating
   executions for empty selections, cache hits, or skipped work.

   Computation identities, row membership, partition completion, and operation
   correspondences retain their runtime and cache responsibilities. Physical
   batching does not change logical partition identity or independence. Reports
   need no per-row invocation links, acquired-selection member lists, historical
   cache-producer records, or copies of intermediate tables. R3 preserves empty
   result semantics within this smaller reporting scope.

   **Accepted operation return contract.** The Python authoring representation of
   an operation's correspondence output is
   `dict[LocationID, tuple[LocationID, ...]]`: each key is an input location ID and
   its value is a tuple containing the corresponding target location IDs.
   `LocationID` is `str`, so the concrete mapping type is
   `dict[str, tuple[str, ...]]`. The tuple is a Python container for an unordered
   target group; each individual target ID is a string. Operation handlers return
   this mapping directly. Lyra constructs the standardized downstream table from
   the validated mapping and resolved target records. Operation authors supply no
   additional DataFrame schema or table, separate row-lineage payload,
   group-dependency declarations, or provenance wrapper.

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

   **Accepted operation execution and reporting.** Keep correspondence mappings
   and completed groups for correct selection, forwarding, caching, and retrieval.
   For `{"A": (), "B": ("H1",)}`, A has a successful zero-match result and B
   maps to H1. These facts remain usable by downstream consumers and cache lookup.
   The execution report records the operation's definition, effective parameters,
   declared sources, and execution/reuse outcome. It does not retain a separate
   correspondence trace or expose a public empty-group inspection interface.

   Declared input bindings and computation identities retain all existing
   dependencies. Persist operation values according to the ordinary storage policy
   in item 12; reporting alone does not require storing an uncached intermediate
   mapping or its membership.

   Require unique target IDs within each tuple; duplicates are a structural error.
   The same target ID may appear under several different input keys. Target-tuple
   permutations and dictionary insertion order have no semantic significance.
   Assemble operation groups by their explicit input and target IDs.

   Target IDs refer to the operation's declared target dataset and revision.
   Every target must be an existing location resolvable through that dataset's
   provider. The operation returns IDs, and Lyra obtains geometry and attributes
   from the provider when needed. Keep the correspondence separately from target
   records for input selection and forwarding.

   **Accepted target forwarding.** Resolve each distinct target record once for
   the needed selection, using the provider's acquisition and cache policy. Build
   the handler-visible input table with one row per distinct correspondence and
   the standard columns `source_location_id`, `location_id`, and `geometry`, plus
   the target provider's ordinary attributes. The geometry and attributes in each
   row belong to `location_id`. Join through explicit target IDs; never align by
   position. Additional columns come from the provider records, not from an
   additional operation return payload.

   For `{"A": ("X", "Y"), "B": ("Y",)}`, the consumer receives:

   | `source_location_id` | `location_id` | Remaining columns |
   | --- | --- | --- |
   | A | X | X's `geometry` and attributes |
   | A | Y | Y's `geometry` and attributes |
   | B | Y | Y's `geometry` and attributes |

   Both Y rows remain visible to a batched consumer. The correspondence pair is
   unique; `location_id` alone need not be. Authoritative provider records retain
   their one-row-per-location rule and independent immutable cache identities.
   Repeating target values in a forwarded table does not create new location
   identities or require duplicate stored provider records. Lyra derives the
   forwarded schema under item 6; provider-table uniqueness checks do not apply
   unchanged to the correspondence rows. No row-order guarantee is introduced.

   A zero-match source contributes no rows, including no null target or fabricated
   geometry. Preserve its completed empty group for execution, caching, and
   retrieval. An entirely empty forwarded table retains its structural columns,
   attribute types, both dataset/revision scopes, and target CRS.

   **Accepted operation chains.** `source_location_id` always identifies the
   immediate source of the represented operation; `location_id` identifies that
   operation's target. Each named input carries both scopes. For A → B → C, the
   final hop's table contains B/C pairs and C's geometry and attributes. Preserve
   each hop's immediate mappings. Consumers needing an A/C association declare
   the relevant inputs and explicitly compose their correspondences using
   qualified IDs; do not silently relabel B as A or expand all paths in advance.
   Partition-related input selection may follow declared chains under item 3,
   while each forwarded table retains its immediate source/target meanings.

   Use these mappings during execution and retain them when caching or result
   retrieval requires them, without a separate durable selection-history graph.

   **Accepted target resolution failures.** An unresolved target produces an error
   during normal acquisition. Do not silently drop it or treat it as a successful
   zero-match result. No separate preflight existence lookup is required.

   **Accepted mapping encoding.** The JSON payload is an object with string keys
   and arrays of target strings. Pydantic's declared mapping type restores the
   tuples. Normalize and persist correspondences as ordinary rows with string
   `source_location_id` and `location_id` columns, qualified respectively by the
   input and target dataset/revision scopes and the result/output scope. These
   pair values are stored independently of target geometry and attributes;
   construct the joined forwarding table when needed.
   Preserve the partition/output scope even when it has no correspondence rows.
   A published cache entry with zero pairs is a reusable empty result; absence of
   an entry is a cache miss. A separate completion object is not required. The tuple/JSON interface
   does not require list-valued SQL cells or serialized table blobs. Target
   collection order has no semantic significance. Use standard library and
   database representations without a custom location-ID codec. Operation rows
   carry `source_location_id` and `location_id`, qualified by their dataset/revision
   and result/table and output scopes. Assembly preserves these fields and both ends
   of the pair. The cache representation needs no invocation field.

   A location operation's correspondence output is conceptually a set of distinct
   pairs `(source_location_id, location_id)`. The declared input and target
   dataset/revision scopes qualify each ID. The first component identifies the
   partition owner; each input location maps to zero, one, or several target
   locations. An input with no corresponding target has an empty group of pairs.
   Different input locations may reference the same target location: `(A, H)` and
   `(B, H)` are distinct relationship rows owned by A and B respectively. Repeated
   target IDs across partitions do not create overlapping relationship rows or
   require a different caching strategy from metrics. The pair identifies the
   relationship; the target location retains its own location-data identity.

   Authors supply the correspondence mapping; Lyra does not infer relationships
   from equal IDs or geometry. Every named output participates in completion and
   cache assembly, including its empty groups.

   **Accepted location boundary for the initial implementation.** Operations
   describe relationships between existing locations. They may perform spatial
   calculations internally to determine those relationships. One-to-many and
   many-to-one mappings remain ordinary correspondences and require no special
   splitting or merging semantics.
   Lyra does not provide operation mechanisms for creating locations, assigning
   their identities, or registering their geometries and attributes, including
   locations formed by merging or splitting. Workflows needing newly constructed
   locations prepare them externally and expose the results as a registered
   location dataset. The report describes declared source boundaries and does not
   explain that external preparation process.

5. **Record effective parameters and trust author contracts.**

   **Accepted.** Record effective parameter values, including resolved defaults,
   for each resolved node use in the execution report. The same values participate
   in computation keys and handler execution. Do not distinguish explicitly
   supplied values from equal defaults or retain supplied/default origin flags.

   Parameter forwarding and named dependency bindings remain supported by the
   execution graph. Resolve them normally and describe the selected graph's
   bindings in the report. Retain the resulting effective values without separate
   public parameter-origin references or a navigable forwarding-history graph.
   For A's `search_radius` forwarded to B's `radius`, then to C's `distance`, the
   report shows the graph bindings and each node use's effective parameters.

   Treat each named parameter as one complete value, including nested objects and
   lists. Do not track separate origins or influence for nested fields, list
   elements, or validator internals.

   Authors own result correctness and determinism, stable identities, partition
   independence, and complete dependency and source declarations. Schema and plan
   checks enforce the structural contract. Lyra generates the report from ordinary
   execution metadata without inspecting handler code, proving independence, or
   identifying every actual contributor to an output.

6. **Give each validation step one owner.**

   **Accepted.** Validate each producer result once at a shared boundary. Include
   output tables or operation mappings and, where applicable, declared row keys
   and location ownership. Validate and normalize operation mappings at this
   same boundary. Construct the execution report from the normalized request,
   resolved plan, and observed outcomes after the applicable validation. Pass owned
   validated data with its matching storage schema to the writer. Publication consumes this
   result without repeating producer validation. Authors remain responsible for
   result correctness and their identity, partitioning, and source-boundary promises.

   Reject references to unknown input or output names in definitions and resolved
   bindings during definition or plan validation, before resource acquisition.
   At the shared producer boundary, require a metric's returned named outputs to
   match its declarations exactly. Reject missing or unexpected output names;
   require explicit schema-valid empty tables for empty metric outputs. This rule
   applies to output-table names; column validation follows the declared schemas.

   Require `location_id` and `row_key` with the accepted Pandera declarations for
   every named output of a location-scoped metric at definition validation,
   before resource acquisition. Missing or incompatible declarations are invalid
   definitions. At the shared producer boundary, require every `location_id` to
   belong to the distinct represented locations in the selected primary input
   actually supplied to that invocation, using that input's dataset/revision
   scope. Check membership without another provider lookup. Pandera enforces the
   declared string types, non-nullness, and `(location_id, row_key)` uniqueness.
   Nonstructural columns, including domain-named IDs, receive ordinary declared
   schema validation.
   Apply these requirements in hybrid graphs as well; do not downgrade a definition
   to unpartitioned scope or use runtime row numbers as its semantic keys.

   Validate external-origin descriptor structure through the shared declaration
   boundary, including origins on node and resource-binding declarations and
   provider metadata derived from dataset registrations. Resolve each node's
   effective direct-origin collection under item 2 before cache lookup or
   resource acquisition.
   Authors own disclosure of direct external acquisition; validation does not
   inspect handlers to discover omitted origins.

   Unpartitioned metric outputs use this same producer boundary for ordinary
   named-output, schema, and supported-type validation, without mandatory
   location-ownership or stable-row-key checks. Validate output scope during
   plan preparation: reject `per_location` caching for an unpartitioned metric
   and partition-selection bindings on its outputs before resource acquisition.
   Validate provider caching as a boolean setting under item 9.

   **Accepted Pandera and SQLite schema contract.** Authors continue declaring
   outputs through Pandera `DataFrameSchema` or `DataFrameModel`, including the
   existing location-schema wrapper. Derive the storage description from that
   declaration; authors do not provide a second SQL schema. Compile it once per
   registered schema, rejecting unsupported types before execution. Registration
   builds descriptions without database access. Database table creation and data
   writes belong to runtime storage initialization or execution.

   **Accepted structural Pandera declarations.** Providers and metrics explicitly
   declare structural columns in the same `DataFrameSchema` or `DataFrameModel`
   as their ordinary attributes. The fixed names in item 3 identify their roles;
   no separate column-name selector establishes ID, owner, row-key, or active
   geometry semantics. Registration validates the following requirements:

   | Table kind | Required schema constraints |
   | --- | --- |
   | Provider records | `location_id`: string, non-null, unique; `geometry`: active geometry with the provider's expected CRS and existing location-geometry validity requirements. |
   | Location-scoped metric output | `location_id` and `row_key`: strings, non-null; composite uniqueness of `(location_id, row_key)` within the named output. |
   | Any spatial metric output | Active geometry column named `geometry`, with declared geometry type, nullability, validity checks, and CRS. |
   | Operation-derived input, generated by Lyra | `source_location_id` and `location_id`: strings, non-null; composite uniqueness of `(source_location_id, location_id)`; `geometry` and target attributes retain their declared types and target CRS. |

   These are mandatory structural constraints. Authors may add compatible
   constraints but cannot weaken the required types, nullability, uniqueness, or
   geometry contract. Missing columns, configurable aliases in place of required
   names, and reserved-name collisions are definition errors. At execution, a
   returned table must satisfy its declared schema and the shared invocation
   membership checks. Unpartitioned metrics require no identity columns; their
   domain-named IDs remain ordinary values, and spatial outputs use `geometry`.

   **Accepted derived forwarding schemas.** Operation authors continue returning
   the dictionary-of-tuples mapping and declare no additional DataFrame schema.
   Lyra derives the forwarded input schema from the target provider's registered
   schema and the operation's source/target scopes. Add `source_location_id`,
   retain `location_id` as the target ID, and require pair uniqueness while
   allowing repeated target IDs. Preserve target column types, nullability,
   descriptions, units, and geometry CRS, including for empty tables.

   Validate provider records and operation mappings once at their respective
   shared producer boundaries before joining them. Provider checks, including
   uniqueness of individual columns and table-wide cardinality checks, govern
   the authoritative record table. They are not copied or rerun unchanged as
   constraints on the expanded correspondence table, where a shared target's
   values repeat. Reuse validated target values and enforce correspondence and
   scope invariants during construction. This requires no general translation
   of custom Pandera checks and no repeated provider validation. The derived
   schema explicitly describes the forwarded table's structural constraints.

   The initial table-column contract is deliberately small:

   | Logical column type | SQLite representation | Reconstruction |
   | --- | --- | --- |
   | String | `TEXT` | pandas string |
   | Signed 64-bit integer | `INTEGER`, from `-2**63` through `2**63 - 1` | pandas nullable integer |
   | Finite 64-bit float | `REAL` | pandas nullable float |
   | Boolean | Integer `0` or `1`, handled by SQLAlchemy `Boolean` | pandas nullable boolean |
   | Geometry | Standard WKB in a `BLOB` column | GeoPandas geometry through standard WKB conversion |

   Nullability remains part of each column declaration, subject to mandatory
   structural non-nullness. Missing values map to SQL
   `NULL`; non-missing floats must be finite. Geometry CRS comes from its declared
   schema and is retained with the stored result. Existing geometry validity and
   location-identity rules still apply. Geometry bytes represent one cell, not an
   opaque serialized result table.

   Use one canonical integer and float type without preserving arbitrary input
   dtype widths, backing arrays, or categorical encodings. Authors represent
   temporal and categorical values using ordinary supported strings or numbers.
   Nested objects and lists are not table-column types in this implementation.
   The separate Pydantic parameter contract in item 7 continues to support JSON
   containers; operation handlers retain their dictionary-of-tuples interface.

   Pandera owns output validation, declared custom checks, and explicitly enabled
   coercion at the shared producer boundary. Use SQLAlchemy Core for SQL schema
   construction, bulk statements, and standard scalar conversions; use pandas
   with explicit declared dtypes for reconstruction and Shapely/GeoPandas for
   WKB conversion. Maintain a small mapping from supported logical types to
   library types, not a general-purpose custom serializer or Pandera-to-SQL
   check compiler. Do not infer restored types from observed rows: empty and
   all-null results use the same declared types as populated results.

   SQL constraints enforce storage structure and scoped internal identities;
   arbitrary Pandera checks continue to run in Pandera. Preserve the current
   result's logical schema, nullability, descriptions, units, and CRS as needed
   for ordinary retrieval and transport. This does not introduce legacy-schema
   migration or historical-result readers.

   Give the executor sole ownership of execution-plan validation before resource
   acquisition. Remove the worker's duplicate plan-validation call and translate
   executor failures at the worker boundary. Independent entry points, including
   cache reads and public interfaces accepting arbitrary data, still validate
   their inputs.

   **Accepted storage and report integrity boundaries.** Lyra constructs report
   entries from the resolved plan and actual execution/cache outcomes. Authors
   supply ordinary outputs. Keep report entries scoped to their execution and
   resolved node use, and preserve the ordinary scoped result identities needed
   by storage. Report actual execution only when a handler runs.

   Use ordinary construction invariants, scoped primary and foreign keys where
   applicable, and transactional writes of related records. Verify construction,
   storage, and retrieval with isolated behavioral tests. Reuse validated objects;
   do not add a separate report revalidation subsystem or repeat producer-table
   validation in the writer or report reader. Public lookups use shared parsing
   and error handling under item 13. These rules ensure structural consistency,
   not semantic correctness of authors' results or declarations.

   Verify that mutation and alternate call paths cannot bypass required checks.
   Apply the same boundary to every node kind and output scope. Report generation
   must not create parallel validation paths in adapters, execution, and storage.
   Internal representation and error propagation remain implementation details.

   [Producer validation](src/lyra/sdk/results.py),
   [storage preparation](src/lyra/sdk/storage_schema.py),
   [worker execution](src/lyra/server/worker.py).

7. **Use entry points for plugin discovery and Pydantic for parameters.**

   **Accepted.** Discover explicitly enabled installed plugins through standard
   Python entry points and load their definitions in API and worker processes.
   Live definitions carry node kinds, output scopes, external-origin metadata,
   Pandera schemas with standard structural columns, and primary-input ownership
   bindings in the shared catalog. Expose Lyra-derived forwarding schemas and
   source/target scopes through the same schema-description machinery.
   Let Pydantic validate parameters and apply defaults, generating JSON Schema for
   discovery. Remove mandatory generated
   plugin manifests, their synchronization machinery, parallel JSON Schema
   validation, and custom default handling. Any retained manifest export is an
   optional inspection tool.

   Relax restrictions imposed solely by parallel validation while preserving a
   usable JSON interface and discovery schema. Capture effective parameter values
   during normalization for queued requests and execution reports, without tracking
   whether they were explicitly supplied or defaulted. Persist descriptions needed
   to read reports within the supported current store.

   **Accepted parameter simplification.** Keep a small shared Pydantic parameter
   base with validated defaults and unknown fields rejected. Pydantic owns type
   validation, default application, schema generation, and serialization. The
   initial implementation supports strings, booleans, integers, finite floats,
   null, lists, string-keyed dictionaries, and nested Pydantic models containing
   supported fields. Use Pydantic's built-in `JsonValue` for recursive JSON
   structures rather than maintaining an equivalent custom type; retain the
   finite-number requirement through library validation and encoding options.

   Serialize all effective parameter fields, including defaults and null values.
   Parameter models must not use custom serializers or field exclusions; all
   computation inputs must remain represented. Reject unsupported values instead
   of falling back to arbitrary string representations. Use Pydantic's
   serialization rather than a custom recursive parameter walker, and remove
   checks needed solely to keep a parallel validation/default implementation in
   sync. This does not require accepting arbitrary Python objects or all Pydantic
   model features.

   **Accepted custom-validator contract.** Authors may declare ordinary Pydantic
   field and model validators on their parameter models. Validators must be
   deterministic, idempotent, and free of external I/O. The same input must
   produce the same effective values, and validating already normalized values
   again, including after JSON transport, must preserve those values. Validators
   must keep results within the supported parameter representation.

   Trust authors to satisfy and test these requirements. Retain ordinary
   Pydantic validation of declared types, constraints, and model rules. Lyra does
   not attempt to prove validator behavior, run validators extra times to test
   these properties, or introduce custom I/O enforcement machinery. Such checks
   cannot generally establish these properties for arbitrary Python code.
   Violations are plugin bugs and may cause inconsistent execution or incorrect
   cache reuse; that limitation is accepted. Normal validation at the relevant
   boundaries is separate from attempting to verify validator purity or
   idempotence.

   The normalized values determine cache identity under item 9. The accepted
   API-to-worker handoff in item 8 carries all effective values and reconstructs
   the model through ordinary Pydantic validation. Supplied/default presence is
   not retained. Parameter forwarding remains supported; item 5 and R8 retain
   effective values and graph bindings without a separate provenance-link model.

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
   build for all jobs. Jobs are inexpensive and short; rejecting pending work
   after an incompatible deployment is an acceptable cost. Remove
   per-job captured executable graph contracts and their comparison and
   reconstruction machinery.

   **Accepted parameter handoff.** The API validates parameters through Pydantic
   and applies defaults before enqueueing. Queue the normalized request, including
   every effective parameter value, build revision, and required dataset revisions.
   Reporting is automatic for every managed graph. Include defaulted values and
   null fields in the serialized parameter representation; do not carry
   supplied-field masks or explicit-versus-default origin flags.

   The worker reconstructs the parameter model through ordinary Pydantic
   validation of those queued effective values. Under the accepted author
   contract, validation after JSON transport preserves the effective values.
   Defaults already resolved by the API arrive as explicit values; the worker
   does not independently select defaults for those fields. Cache keys, handler
   execution, and reports use the same effective parameter values. Trust the
   validator contract without additional behavioral checks or a custom model
   restoration mechanism.

   For example, omitting a parameter whose default is `radius=100` queues
   `radius=100`, just as explicitly supplying that value would. Execution and
   reports retain the value without distinguishing those two cases. Named
   dependency and parameter bindings remain part of the selected graph description.

   Reject a build mismatch before resource acquisition or handler execution,
   using the existing terminal failure status with a clear `deployment_changed`
   reason. Callers can resubmit. Prepare compatible jobs against the worker's
   catalog. The executor validates the plan before execution for every output
   scope. The build revision covers executable code, routing policy, and
   declarations. The public discovery fingerprint serves a different purpose.
   Build identification and compatibility checks remain implementation details.
   The deployment build revision serves job compatibility and execution
   reporting; metric and operation computation-cache implementation identity
   uses the author-managed code revision specified below. Provider code
   revisions are reported but do not affect immutable record cache keys or
   downstream location-input identities.

   Persist the normalized request, durable execution ID, outcome, selected graph
   and route, relevant revisions, and output descriptions. The report includes
   node kinds, output scopes, graph bindings, effective parameters, declared
   external origins, and observed execution/cache-reuse/skipped outcomes under
   item 4. Include mixed execution/reuse outcomes without requiring per-row or
   per-partition event records. Declared bindings do not imply acquired inputs.
   Record actual handler execution only when it occurs.

   Store results and reports independently of queued jobs so retrieval survives
   RQ expiry. Publish success only after the required final values, result
   descriptions, and report are durable under R10. No successful result may
   silently fall back to an unavailable report after a persistence failure.

   Backward compatibility across incompatible plugin or storage changes is not
   required in closed alpha. Do not add executable contract snapshots, legacy
   readers, or deployment-synchronization machinery for report retrieval.

   [Acceptance models](src/lyra/contracts/accepted.py),
   [plan preparation](src/lyra/runtime/planning/preparation.py),
   [job observations](src/lyra/server/job_store.py).

9. **Select caching by node kind and reuse values by their declared identities.**

   **Accepted metric and operation caching.** Make caching available at
   intermediate and final metric and operation nodes through three
   developer-selected strategies:

   | Strategy | Behavior |
   | --- | --- |
   | `off` (default) | Execute the complete input batch on each invocation. |
   | `full` | Reuse results for the complete bound selection when its computation key matches. |
   | `per_location` | Reuse matching locations, compute the missing subset, and assemble the requested result. |

   **Accepted provider caching.** Providers have one boolean cache setting:

   | Setting | Behavior |
   | --- | --- |
   | Off / `False` (default) | Acquire the records currently needed without reusing cached provider records. |
   | On / `True` | Reuse records by `(dataset_id, revision, location_id)`, acquire only missing records, and assemble the needed selection. |

   Each location identity identifies one immutable record. Independence from
   other requested locations is part of this contract and requires no separate
   author promise. Providers may acquire a reduced selection with either
   setting. Batch the needed acquisitions without including batch membership in
   record identity. A downstream cache hit may bypass provider acquisition
   entirely; an empty needed selection invokes no provider handler and creates
   no record cache keys. Shared consumer inputs still require their complete
   bound selections.

   Provider code revision, node ID, resource configuration, acquisition batch,
   and the graph path selecting a record do not affect its cache key or its
   identity as a downstream location input. Record provider implementation and
   observed acquisition/reuse/skipped outcomes in the execution report. Changed
   geometry or attributes require a changed dataset ID, revision, or location
   ID. Disabling caching permits another acquisition of the same immutable
   value; it does not permit different contents under that identity.

   **Accepted unpartitioned caching.** An unpartitioned metric may use `off` or
   `full`, with `off` still the default. `per_location` is invalid for this scope.
   When invoked, its handler receives all initially bound input selections; cache
   hits downstream do not narrow those selections. A full cache entry contains all
   named output tables for that whole computation, including successful empty
   outputs. Reuse requires the complete computation key to match. A downstream
   hit may bypass the metric entirely under the ordinary node-local `off` rule.
   Determinism and declared-input and source-boundary obligations remain unchanged.

   **Accepted deterministic computation contract.** Metrics and location
   operations produce the same logical values and output membership for the
   same computation key, including successful empty outputs. This applies
   across executions, batching permitted by the partition contract, and all
   caching strategies and output scopes. Meaningful time periods, source
   releases, implementation changes, and other output-affecting differences
   must be represented in the declared input or implementation identities or
   effective parameters included in the key. Dataset providers retain item 20's
   narrow lookup interface and frozen location-data identities; each record is
   invariant across acquisitions, batches, and cache settings.

   Authors own this guarantee. Different logical values under the same key are a
   contract violation, not another valid stored version. Lyra does not prove
   determinism or add output-content hashing or comparisons to establish it.
   Physical row and column order remain insignificant under item 3.

   **Accepted node-local meaning of disabled caching.** Metric and operation
   strategy `off`, and provider setting `False`, disable the node's own cache
   reuse. The node executes whenever it is invoked, but a valid downstream
   cache hit may bypass that invocation entirely. Repeated execution with the
   same computation key or location identity must produce the same logical
   result. Changed observations require changed key inputs even when caching is
   off. Disabling caching does not invalidate dependent cached results or
   change their identities.

   For location-scoped metrics and operations, all caching strategies use the
   declared partition mapping. When executed, `off` and `full` handlers receive
   the complete bound inputs; a partition mapping alone does not establish
   independent computation. `per_location` uses the location keys and output
   ownership defined above. Each cache entry contains all results owned by one
   location across the node's named outputs, including an empty group when a
   location successfully produces no rows. Publish the cache entry only when
   all of its named outputs are complete. Entry presence distinguishes a
   reusable empty group from a cache miss; no per-row completion flag or
   separate completion object is required. Operation correspondence pairs are
   grouped by their input-location component and follow the same rule.
   Normalize omitted dictionary keys to completed empty groups for the inputs
   supplied to a successful invocation. Assemble operation and metric groups
   through explicit keys, without promising result order. Other inputs still
   contribute to computation identity. Consumer inputs follow the accepted
   partition-related or shared selection bindings in item 3.

   **Accepted order-independent cache identity.** Reordering the same location
   selection, operation target group, or keyed input rows does not by itself change
   computation identity. Keys account for the selected identities and membership,
   not incidental collection order. Explicit sequence values in data or parameters
   remain significant. Internal canonicalization may sort identities for a stable
   key representation; it does not promise any handler input or output order.

   **Accepted upstream execution scope.** Preserve each metric or operation
   producer's initial input binding for the current request before cache hits
   reduce downstream work. That binding describes a particular input selection,
   not the entire dataset. When an upstream metric or operation result is
   needed, only a producer declaring `per_location` may receive a reduced
   partition request based on the consumer's missing keys. `off` and `full` do
   not establish the required independence promise and retain their complete
   initially bound inputs.

   For example, a request covers A, B, and C, but the downstream result for A is
   a valid cache hit. The downstream handler needs partition-related findings for
   B and C from a producer initially bound to A, B, and C in this request:

   | Metric or operation producer strategy | Upstream behavior |
   | --- | --- |
   | `off` | Execute with A, B, and C. |
   | `full` | Look up results for the complete A/B/C selection; execute all three on a miss. |
   | `per_location` | Request B and C, check the producer's own cache, and compute only its missing keys. |

   In each case, supply only the findings associated with B and C to that
   partition-related consumer input. Shared inputs retain their complete bound
   selections, including when a `per_location` producer executes a reduced
   batch. Apply the same rule at each upstream metric or operation dependency;
   a downstream cache hit must not silently change the bound inputs of an `off`
   or `full` metric or operation producer.

   Providers acquire only the location records still needed after these rules
   determine consumer inputs. For a consumer needing only B/C, a provider with
   caching off acquires B/C; with caching on, it acquires only uncached B/C
   records. If an upstream metric or operation must still run on A/B/C, or a
   shared input requires A/B/C, those records remain needed. Provider narrowing
   never changes the selection a handler is required to receive.

   A `per_location` producer requested for A/B/D with matching cached groups for
   A and B invokes its handler only for D. Its primary location input contains
   only rows whose `location_id` is D, retaining any distinct incoming
   correspondences; dependency tables partitioned by the same location contain
   only D's groups, which may have zero or more rows. Other partition-related
   inputs follow their declared correspondences, and shared inputs remain complete.
   The runtime combines cached A/B with the newly computed D group so the consumer
   receives the complete requested A/B/D result. A `full` producer with only an
   A/B/C cache entry computes the complete A/B/D invocation on this miss; an `off`
   producer executes that complete invocation whenever needed.

   Metric and operation authors choose the strategy and own the independence
   promise required by per-location reuse, including dependencies. Authors
   validate this independence; Lyra does not prove it or automatically select a
   safer strategy. Lookup, partitioning, and assembly complexity is accepted
   because it enables reuse across overlapping requests. Use the shared SQLite
   result store in item 11; caching policy does not determine retention of
   already stored results.

   **Accepted shared operation storage and caching.** Metrics and operations use
   the same cache lookup, publication, retention, and reuse machinery. Store
   ordinary output rows or correspondence rows in the shared result layer when
   required by caching or result retrieval. Preserve mapping membership and empty
   groups. The execution report does not require another copy or persistence of
   uncached intermediate mappings.

   Cache matching and the declared strategy determine reuse. A stored result does
   not authorize an `off` node's own cache reuse: it still executes when invoked,
   although a valid downstream hit may bypass it. `full` reuses a matching complete
   bound selection's mapping; `per_location` reuses matching input-location groups
   and computes the missing groups. Reporting does not change these rules.

   Each executable node may declare an optional opaque code revision, including
   metrics, operations, and provider handlers. Its implementation identity is
   `(node_id, code_revision)`. Metrics and operations include this identity in
   computation keys alongside effective parameters, inputs, and declared
   external-origin identities. Providers retain it for execution reporting;
   their record keys use only `(dataset_id, revision, location_id)`. An omitted
   code revision is represented by `None`, a stable value. Plugin authors own
   changes to this token whenever implementation or behavior-affecting
   declarations change; assigning a different node ID also changes
   implementation identity. Lyra compares the declared tokens for equality
   without interpreting version numbers, hashing code, inspecting package
   versions, or detecting changes automatically. Authors may automate token
   updates in their own tooling.

   Upstream metric and operation computation identities contribute to dependent
   computation identities, propagating their implementation revisions. Provider
   implementation revisions do not invalidate record caches or dependent
   computations; changed authoritative record contents require changed location
   identities. Deployment build revisions are recorded for job compatibility
   and reporting, not used as additional computation-cache invalidation tokens.
   No automatic cache clearing on code changes is required; cache reuse follows
   the declared identities.

   **Accepted upstream result identities.** Identify metric outputs and
   operation correspondences through their producer's logical computation
   identity, the named output being consumed, and the selected partition groups
   where applicable. An unpartitioned output is referenced as a whole table
   under its complete computation identity, without a partition-group
   selection. Use the producer's caching strategy to determine the computation
   identity's scope:

   | Metric or operation producer strategy | Computation identity referenced by the consumer |
   | --- | --- |
   | `per_location` | The identity of each selected partition computation. |
   | `full` or `off` | The logical computation identity of the complete selection initially bound for the request. |

   A per-location computation's identity is independent of which other partitions
   happen to share its execution batch, provided its own effective inputs,
   parameters, and shared context are unchanged. For example, findings for A use
   `K_A` whether A was computed alongside B or C. A full or uncached findings
   computation over A/B/C uses `K_ABC`, even when a consumer selects only group A;
   an A/B/D computation has a different identity because no partition independence
   was promised. Shared inputs contribute their complete bound selection.

   These identities derive from implementation identities, effective parameters,
   input identities, and declared external-origin IDs and revisions. They are
   independent of job/run IDs, storage references, and output-content hashes.
   An `off` producer has a logical computation identity
   even though its own execution cannot be replaced by a cache lookup. The
   computation key also identifies its values whenever they are persisted.
   Execution reports have separate execution identities; stored values require
   no historical producing-invocation reference.

   Location records supplied by providers contribute only their
   dataset/revision scope and selected IDs. They do not reference a provider
   computation or its acquisition batch. A's record has the same input identity
   when acquired with B or C, from cache or with caching off, or after a
   provider code revision change. Operation correspondences used by a binding
   remain computed dependencies with their own computation identities;
   resolving their target records through a provider does not erase those
   dependencies or put them in the record keys.

   **Accepted identity-key contents.** Use shared descriptor encoding and key
   generation with an explicit identity-kind discriminator. A location-record
   descriptor contains only that discriminator and `(dataset_id, revision,
   location_id)`. A computation descriptor for a metric or operation includes
   its discriminator, implementation identity `(node_id, code_revision)`, the
   declared output scope, and computation scope: a complete bound input
   selection for `full` or `off`, or one qualified location partition for
   `per_location`. Add the following node-specific components:

   | Node kind | Additional descriptor contents |
   | --- | --- |
   | Metric | Effective validated parameters, including defaults; identities of every named input; effective direct-origin IDs and revisions under item 2; and the primary location input's name when location scoped. |
   | Location operation | The metric components, plus the declared target dataset ID and revision. |

   A dataset provider partitions by its own locations and has no primary
   location input argument. It accepts no arbitrary calculation parameters or
   upstream metric-table inputs. Each needed record has its own location-record
   key, with no key for the acquisition batch. Provider implementation, cache
   setting, resource configuration, and the upstream graph selecting its IDs
   are excluded from record keys under item 20. For metric and operation nodes,
   input names distinguish their roles and participate in identity.

   Include each metric's and operation's effective direct-origin IDs and revisions
   in its descriptor, combining node and resource-binding declarations under item 2.
   Declaration placement and repeated references to the same origin do not change
   computation identity. The provider's dataset ID and revision already supply
   its derived origin identity; do not add an independently managed token.
   Upstream metric and operation origin changes propagate through their
   computation keys; provider dataset changes propagate through
   location identities. Arrange origin identities consistently with other
   unordered identity collections. Descriptions are discovery and report
   metadata, excluded from keys.

   Location-record input identities contain their dataset/revision scope and
   selected location IDs, without provider implementation or acquisition-batch
   identity. Metric-output and operation-correspondence dependencies use the
   upstream logical computation references, named outputs, and selected groups
   specified above, including correspondences used to derive location
   selections. Use compact upstream computation keys as references rather than
   embedding entire ancestor descriptors. Shared inputs contribute their
   complete bound selection. A per-location descriptor covers its one primary
   partition and applicable inputs plus complete shared inputs; a full or
   uncached computation descriptor covers the complete selection initially
   bound for the current request.

   An operation's resulting target IDs are outputs, not descriptor
   prerequisites; its declared target dataset/revision is known before
   execution. Shared descriptor variants cover immutable location records and
   metric/operation computations across output scopes. An `off` metric or
   operation has this logical descriptor without an entry in its own
   computation cache.

   An unpartitioned metric's descriptor records that output scope and all complete
   bound inputs, without a primary location input or owning partition. Downstream
   shared bindings reference its computation key and named output as a whole table.
   Changing any effective input selection changes dependent computation identities
   even when the consumer itself uses `per_location` caching.

   Exclude incidental collection order, job/run IDs, storage references, and
   location/output content hashes. Explicit sequence information in scalar data
   fields and ordered parameter values retains its meaning.

   **Accepted descriptor normalization and encoding.** Use effective validated
   parameter values, including defaults, under item 7's supported serialization
   contract. An omitted parameter and an explicitly supplied equal default have
   the same computation identity, without tracking their different origins.
   Preserve scalar type distinctions remaining after validation; for example, the
   string `"1"` and integer `1` are different values. Do not coerce values solely
   to make cache keys match.

   Arrange unordered descriptor collections consistently by their explicit
   identities before encoding. Location selections A/B and B/A produce the same
   key, and dictionary insertion order is irrelevant. Preserve list order for
   sequence-valued parameters: parameter values `["X", "Y"]` and
   `["Y", "X"]` remain distinct. Internal sorting of unordered identity
   collections does not create a public ordering guarantee.

   Use Pydantic to produce the supported plain parameter representation, then
   encode the complete descriptor as deterministic JSON with sorted object
   keys, fixed compact encoding options, and non-finite numbers rejected. Hash
   the UTF-8 encoding with SHA-256 to obtain the cache key. Reuse Python's
   standard JSON and hashing libraries; no additional serialization framework
   is needed. Unsupported values fail rather than using a string/repr fallback.
   This hashes the identity descriptor, not location or output contents. Use
   the same encoding and hashing for location-record descriptors. The
   identity-kind discriminator separates record keys from computation keys
   without adding an author-managed version or acquisition identity.

   Metric and operation cache entries are logical units. A per-location entry
   contains a complete group across all named outputs, including successful
   empty groups, rather than a single result row. This partition result is a
   logical grouping, not a blob or a database row. Several partition results
   may be produced by one batched handler call and persisted together. A
   `per_location` key references one complete partition result; a `full` key
   references the complete bound selection's collection of partition results
   or, for an unpartitioned metric, its whole named output tables. Distinct
   `full` selections such as A/B/C and A/B/D have separate cache entries;
   computing one does not overwrite the other, and storing their rows by
   partition does not enable subset cache matching for `full`. Each provider
   record entry contains exactly one resolved location; an unresolved ID is an
   acquisition error, not a reusable empty record. Batched provider calls may
   publish several independent record entries together. Item 11 defines the
   accepted relational layout, resolving R4.

   **Accepted values-only cache.** Cache entries map computation or
   location-record keys to result values and the schema, named-output,
   output-scope, and applicable partition and row-identity information required
   to retrieve them. Keys account for the applicable identity contract:
   computation keys include effective parameters, defaults, metric/operation
   implementation revisions, input identities, and external-origin
   IDs/revisions; provider record keys include only immutable location identity
   and the record-kind discriminator. Neither matching nor reuse depends on a
   historical invocation, calculation time, or execution report. Cache entries
   and cached value records carry no required producing-invocation link or
   reporting flag.

   **Accepted SQLite storage.** Use the computation or location-record key
   directly as the stored value's identity, called `value_key` in the shared
   layout. Item 11 defines a result header, any location partitions, and typed
   tables for its named outputs. Publish the header, applicable partition
   records, and all data rows together in a transaction after successful
   computation or acquisition and validation. A published computation header
   identifies a complete stored result, even with no output rows or no
   locations. A location-record header requires its single resolved record.
   Absence of the header is a cache miss; reuse of an existing result also
   requires the node's cache policy to allow it. A logical cache entry is this
   reusable stored result, not a separate key-to-storage-ID mapping. Its header
   and declared output scope represent completion without a separate completion
   object or per-row completion flag.

   Persist one logical value per computation or location-record key. If another
   successful execution or acquisition of that key needs persistence, retain
   the existing immutable values rather than creating a new stored version.
   Record the actual execution separately from cache reuse in the execution
   report. Assembly selects the required keys and partition groups without
   creating another stored copy of their values. Use indexed grouped retrieval
   under item 11; values carry no required link to any producing invocation.

   **Accepted cache-hit reporting.** A valid matching cache entry is reusable
   without its historical execution report. The current report describes the
   selected graph, effective parameters, declared sources, and node reuse outcome.
   Record actual execution for handlers that run, including mixed execution and
   reuse for a node with only some partitions cached. Bypassed work remains
   skipped; do not run it to fill in a report or recover historical producers.
   This resolves R2.

   Location-scoped metric rows retain their qualified `location_id` and `row_key`
   during assembly under item 3. Repeated row keys in different partitions remain
   distinct. Operation rows retain their qualified
   `(source_location_id, location_id)` pairs. Forwarded tables preserve shared
   targets once per correspondence while record caches retain one authoritative
   value per location identity. Assembly and cache
   lookup retain all required computation and partition references without a
   separate finding-level trace.

   Location-scoped metrics and operations may use `per_location` caching in
   hybrid graphs. Ownership and stable row identities do not establish
   partition independence; authors own that separate promise. Every managed
   graph has an execution report.

   **Accepted.** Item 11 fixes the value-storage layout, scoped keys, and atomic
   value publication, applying R1's row identities. R10 requires final results
   and the execution report to be durable before caller-visible success.

   [Cache implementation](src/lyra/runtime/cache.py),
   [executor](src/lyra/runtime/execution.py).

10. **Keep each node kind's ordinary return contract.**

    **Accepted.** Metric handlers return ordinary named output tables; location
    operations return ordinary correspondence mappings. Every location-scoped
    metric output declares standard `location_id` and `row_key` columns in Pandera.
    Lyra derives operation forwarding schemas and builds pair rows joined to
    target `geometry` and attributes. Output validation,
    partition completion, and cache assembly follow item 4. Authors supply no
    row/group contribution declarations or execution-report wrapper.

    Lyra generates reports automatically for all managed graphs. Clients do not
    select a reporting mode, and handlers do not branch on a reporting flag. Run
    and result interfaces expose the durable execution ID alongside ordinary
    outputs. DataFrames need no additional reporting columns. Cache hits follow
    R2's values-only contract without requiring historical reports.

    Decorator names, result-class names, and field spelling are implementation
    details within the agreed contract.

11. **Store results and execution reports in SQLite and retain them.**

    **Accepted backend and deployment scope.** Use SQLite for Lyra-managed result
    data, cache lookup, structural result scopes, and execution reports.
    Local execution and
    API/worker execution use the same storage implementation. The initial target
    is one machine with a modest workload; workers on multiple machines and a
    high-workload deployment are not planned for the foreseeable future.

    **Accepted native row storage and relational layout.** Persist ordinary SQL
    rows for metric outputs, stored location records, and operation
    correspondences. Use two shared metadata tables and one typed data table
    per registered named output or provider dataset registration. A `value_key`
    identifies the stored value directly: a computation key for
    metric/operation results or a location-record key for provider records
    under item 9. This is the identity key itself, not another storage ID or
    mapping layer. The conceptual location-scoped metric layout is:

    | Table | Columns | Primary key |
    | --- | --- | --- |
    | `stored_results` | `value_key`, `node_id`, `output_scope`, `partition_dataset`, `partition_revision` | `value_key` |
    | `stored_partitions` | `value_key`, `location_id` | `(value_key, location_id)` |
    | Named output, such as `scores` | `value_key`, `location_id`, `row_key`, and ordinary value columns, including `geometry` when spatial | `(value_key, location_id, row_key)` |

    `stored_results` describes a complete persisted computation, including one
    covering zero locations, or one resolved provider record. For computations,
    `node_id` identifies the metric or operation and its registered
    named-output definitions. For provider records, `node_id` is absent and the
    dataset registration identifies the schema and data table; record values
    have no dependency on a particular provider implementation. `output_scope`
    explicitly distinguishes location-scoped and unpartitioned results. For
    location-scoped results, `partition_dataset` and `partition_revision` are
    the owning location dataset's existing ID and revision from item 20, copied
    from the resolved partitioning input, or the provider's own dataset. They
    are not additional author-managed versions. The revision remains nullable
    for the accepted frozen, unversioned identity.

    `stored_partitions.value_key` references `stored_results`. Each
    location-scoped metric output row references its owning `(value_key, location_id)`
    partition. The result header supplies that location's dataset/revision
    scope. A value key alone is not a metric-row primary key: one computation
    may cover multiple locations, and each location may produce multiple rows
    per named output. `location_id` and `row_key` are the required structural
    columns declared in the metric's Pandera schema; storage uses those same
    columns without duplicating them. Every location-scoped metric output uses
    these fixed names and ownership/row identities, regardless of caching strategy.

    Provider records have one header and partition per immutable location key,
    with exactly one data row. Batch membership and provider code revisions do
    not create separate stored copies. Provider data tables contain `location_id`,
    `geometry`, and declared attributes. Operation data tables contain
    `source_location_id` and target `location_id`, keyed by
    `(value_key, source_location_id, location_id)`. Their source field references
    the owning `(value_key, location_id)` in `stored_partitions`; source and target
    dataset/revision scopes come from the result and output descriptions.
    Store operation pairs separately from provider records. The joined
    handler-input table can be constructed from these values without another
    permanently stored copy or duplicate provider records. The existing node-kind identity contracts apply;
    string metric-row keys and structural integrity follow R7. Use the scoped
    primary-key indexes for value lookup and grouped partition reads. Physical
    naming, additional query indexes, and SQL construction remain
    implementation details.

    **Accepted unpartitioned storage.** An unpartitioned result has a header with
    explicit unpartitioned scope, no owning dataset or revision, and no
    `stored_partitions` records. Its named-output rows reference the computation
    directly. Use internal row identifiers as needed for storage and pagination,
    scoped to the stored computation and named output. They are not author-facing
    semantic row identities, imply no row order, and establish no correspondence
    across computations. Preserve all rows, including repeated equal values.
    Do not invent a synthetic owning location. Use the same typed output tables,
    validation boundary, and retrieval machinery as other results.

    Retain logical schemas once with the registered named-output definitions or
    dataset registrations, including fixed structural names, column types, nullability, descriptions,
    units, and CRS needed to reconstruct results in the supported current
    store. Do not repeat an `output_schemas` document on each result or data
    row. Dataset scope belongs on the result header; ordinary value rows need
    not duplicate it.

    Retain or derive operation forwarding schema descriptions through the shared
    schema machinery in item 6. Pair storage needs the two ID columns and their
    scopes; the forwarded schema additionally describes target geometry and
    attributes. Generated tables preserve that schema when empty. Internal
    storage fields such as `value_key` are not added to author-facing tables.

    **Accepted completion and empty results.** Publish a result header, all its
    applicable partition records, and all named-output rows in one transaction
    after successful validation. Header presence represents a complete stored
    result. Each listed metric or operation partition covers every declared
    named output; zero rows in one of those output tables represents a
    successful empty output for that partition. This structural scope supplies
    completion without a separate completion object or per-row flag. Failed or
    unprocessed work is not published as an empty result.

    A provider record is complete only with its resolved data row. Missing IDs
    remain acquisition errors. An empty needed location selection creates no
    provider headers, partitions, data rows, or handler invocation. Record its
    observed outcome in the execution report. Provider records acquired in one
    batch may be published in one transaction under their individual keys.

    For example, a `full` computation `K_ABD` has one result header and
    partition records for A, B, and D. If B produces no score rows, its
    partition record remains. A successful persisted location-scoped metric or
    operation `full` or `off` computation over zero locations has a result
    header and no partition or output rows, distinguishing it from an absent
    stored result. With `per_location`, A, B, and D have separate computation
    keys and headers even when computed in one batch. A request for zero
    locations has no per-location computations; the request and observed
    execution outcome remain in the report without inventing a partition or
    key.

    An unpartitioned header covers all declared named outputs. Zero rows in a
    named-output table means that whole output completed empty. Publish the header
    and all outputs atomically, including when every output is empty; header absence
    remains a cache miss. Empty bound inputs may produce nonempty unpartitioned
    outputs under item 4. Explicit scope distinguishes these results from a
    location-scoped computation covering zero locations.

    **Accepted reuse and storage identity.** Store one immutable logical value
    per computation or location-record key. Repeated successful persistence of
    the same key retains that result. No independent storage ID, stored-version
    ID, or key-to-value-ID mapping is needed. Matching published results are
    reusable only when the declared cache policy permits it; stored values do
    not bypass a metric/operation `off` invocation or an acquisition by a
    provider with caching disabled. The initial layout has no `cache_available`
    flag. Independent invalidation of individual entries is deferred; changes
    to declared input or metric/operation implementation identities produce
    different computation keys. Provider record keys change only with location
    identity. Disabling provider caching does not delete retained records, and
    later enabled caching may reuse them.

    Stored values and execution reports are independently owned within the
    shared store. Cache lookup needs only the applicable value identity and
    complete values. Assembly and result descriptions reference value keys and
    selected groups without copying values. Stored values require no historical
    report or inverse producing-invocation link. Reports record current
    execution, cache reuse, and skipped work at the node level under item 4.

    Store report metadata through ordinary shared SQLite persistence, using scoped
    keys and transactions for related records. The physical report schema remains
    an implementation detail; it need not contain row/group provenance tables or
    retained intermediate membership. Item 12 fixes retention scope, and R10 fixes
    local initialization and durable result/report publication.

    Arrow files and Arrow blobs are not the persistent table representation.
    WKB remains the standard representation of individual geometry cells. Arrow
    bulk downloads and GeoParquet exports are generated from selected rows through
    standard library conversions. Ordinary result storage does not require a
    separate filesystem artifact store or coordination between SQL references and
    external payload files.

    Use the Pandera-derived storage descriptions and supported types in item 6.
    Store the logical schema and CRS metadata needed to reconstruct current-store
    results, including empty outputs, without a custom per-value codec. Immutable
    value references can be shared by caches and assembled results. Operation
    mappings use the same value layer without reporting-specific copies.

    **Accepted performance approach.** Compile storage descriptions once per
    registered schema, validate each producer table once, insert batches in a
    transaction, retrieve requested partitions together with indexed queries,
    and reconstruct columns using explicit declared types. Use SQLAlchemy Core
    and existing pandas/Shapely/GeoPandas conversions. The anticipated metric
    results have roughly 1–5 columns and 100–10,000 rows; location datasets range
    from a few dozen polygons to thousands of individual houses. These are design
    workloads, not API limits. Geometry complexity and the number of rows per
    partition matter independently of row count.

    Exploratory single-process measurements support this choice at that scale.
    For 10,000 rows with four scalar value columns plus identities, native SQL
    writes took about 39–41 ms, full reads about 13 ms, and single-partition reads
    about 0.4–0.6 ms. Arrow blobs were faster for bulk operations with larger
    groups and slower for many tiny groups. Native storage of 10,000 points took
    about 49 ms to write and 16 ms to read; 50 polygons with 10,000 vertices each
    took about 26 ms and 9 ms. These local synthetic measurements include
    conversion and database commit or dataframe reconstruction, use warm reads,
    and exclude metric execution, Pandera validation, and integrated execution-report
    processing. They establish feasibility, not performance guarantees or test
    thresholds. Verify the integrated implementation on representative workloads.

    **Accepted retention.** Result data is append-only and immutable within the
    supported current store. Retain stored values, structural membership information,
    and execution reports indefinitely. Changed computation keys, caching policies,
    and expired job records do not delete underlying results or reports. Resolve
    execution and result identities independently of transient RQ records.
    Automatic cleanup, retention counts, and ownership-based deletion are
    deferred. Storage growth is accepted, and an explicit development-store reset
    is allowed. Indefinite retention applies to data actually written; item 12
    still determines which intermediate values are persisted. No backward
    compatibility, migrations, or legacy-result handling across incompatible
    schema or implementation changes are required in closed alpha.

    **Accepted scope.** This fixes the value-storage layout and completion
    contract, resolving R4. Report metadata uses its own records, independently of
    result values. R10 settles durable execution-ID access, automatic local store
    setup, and success publication after required result and report persistence.

12. **Retain execution reports without trace-only intermediate metadata.**

    **Accepted.** Retain the normalized request, selected graph and named bindings,
    effective parameters, declared sources and revisions, observed node outcomes,
    and final status described in item 4. Keep sufficient descriptive metadata to
    read the report in the supported current store after transient jobs expire.
    Reporting applies to location-scoped, unpartitioned, and hybrid graphs.

    Preserve output ownership, row identities, partition membership/completion,
    operation correspondences, and computation references wherever execution,
    caching, or result retrieval requires them. Their runtime responsibilities
    are unchanged. The report does not require durable links from findings to
    invocations, intermediate row-membership records, empty-group trace records,
    acquired-input member lists, or navigable parameter-origin chains.

    Intermediate metric values, location records, and operation mappings follow
    the ordinary caching policy. With caching off, use uncached intermediate
    results in memory without snapshots or identity-only membership tables
    solely for reporting. Persist operation mappings when caching or ordinary
    result retrieval requires them, without a special retention rule for
    uncached operations. Required final outputs and their retrieval
    descriptions remain persisted. Retention of a stored value does not enable
    a node's own cache reuse when its metric/operation strategy is `off` or its
    provider setting is `False`.

    **Deferred.** Finding-level provenance, intermediate membership inspection,
    exact row contributions, historical cache ancestry, and explicit diagnostic
    capture of every node output require a demonstrated use case. Reporting does
    not reconstruct missing values or archive external data or executable code.

13. **Retrieve execution reports through shared lookup logic.**

    **Accepted.** Expose report retrieval by durable execution ID through Python,
    HTTP, and MCP using shared contracts and lookup logic. Run/result interfaces
    provide the ID alongside ordinary outputs; no reporting column or mandatory
    per-row reference is added to pandas/GeoPandas tables. The report contains the
    execution-level information in item 4. Bounded inspection and complete JSON
    downloads use the same retained report and shared read-only lookup logic.
    A public graph-navigation or intermediate membership-listing interface is
    outside the initial implementation.

    **Accepted bounded inspection and complete downloads.** Return the complete
    report inline when the response fits the normal inspection size limit.
    Otherwise, return an explicitly partial preview that preserves the execution
    ID and final status, identifies omitted or shortened information, and includes
    a link to download the complete report as JSON. Never silently truncate a
    report or present a preview as complete. Exact preview fields, omission
    metadata, response envelopes, and size limits remain implementation choices.

    Generate the full JSON download from the retained report using shared lookup
    and ordinary authentication. The download may stream beyond the inspection
    response-size limit; that limit applies to inspection responses, not bulk
    report downloads. No additional permanently stored report file is required.
    Provide Python access to the complete report from the local store or through
    the remote JSON download. The execution ID continues to resolve the report
    and its download access after transient job records expire.

    MCP returns the same bounded inspection, including the full-report download
    link when the report is too large to return inline. Assume agents can download
    and process files efficiently through other capabilities. Complete inspection
    of an oversized report using only MCP is not required; report pagination or
    chunk-retrieval tools are outside the initial implementation.

    Response limits never remove information from the retained report. Successful
    execution still requires the complete report to be durable under R10,
    including large parameter values and graph descriptions. Exceeding an
    inspection size limit selects the preview-and-download response rather than
    failing execution or report retrieval solely because of that limit.

    A successful execution has a durably stored report. An empty result or request
    covering zero locations still has its execution ID, request, and observed node
    outcomes; it needs no synthetic row, partition, or invocation. Mixed cache
    reuse and computation are reported without requiring per-finding explanations.
    Reports work for both metric output scopes and hybrid graphs, without a
    complete-trace eligibility classification.

    Ordinary result retrieval remains separate and supports the table access,
    downloads, and pagination in item 14. Public row/group trace references,
    finding lookups, empty-group inspection, parameter-origin traversal, and
    interactive upstream graph navigation are deferred.

    **Accepted lookup failures.** Shared parsing rejects malformed execution or
    result references as validation errors. A well-formed reference with no target
    returns not found. A broken required relationship in stored report/result data
    is an integrity error. Use normal lookups and report errors encountered; do
    not rerun handlers, acquire external data, fabricate records, or perform a
    separate recursive integrity sweep. Intentionally unstored intermediate
    values are outside report retrieval and do not make the report incomplete.
    Exact reference syntax, error models, and method names remain implementation
    choices. R10 fixes publication and local access.

14. **Use Arrow bulk downloads and paginated JSON row access.**

    **Accepted.** Serve named output tables as Arrow bulk downloads and provide
    bounded, paginated JSON rows through HTTP and MCP. Keep status, schemas,
    execution reports, previews, and download links in JSON. Agents can read
    result values through MCP without code execution or an Arrow reader. Row access
    complements execution-report retrieval. Oversized execution reports use the
    explicit partial preview and complete JSON download in item 13; agents may
    process that file through capabilities outside MCP.

    Run/result interfaces expose a durable execution ID for report lookup,
    consistently through Python, HTTP, and MCP. Successful final results and their
    reports must be durable before success is published under R10. Response
    envelope fields and encoding remain implementation choices; table structural
    column names are fixed by item 3.

    Unpartitioned named tables use ordinary result retrieval, Arrow downloads,
    and paginated JSON access. Result descriptions expose their unpartitioned
    scope without mandatory location or author-row-key fields. Their executions
    have the same report access as other graphs.

    Read the selected rows from SQLite through shared retrieval logic. Generate
    Arrow bulk downloads through standard table conversion and return paginated
    JSON rows from the same stored results. Geometry uses standard WKB/spatial
    conversion with the declared CRS. Arrow is a transport format, not a required
    persistent table format.

    Assemble reused and newly computed partitions through explicit keys and
    stored result references. Downloads and previews need not create an
    additional permanently stored result copy. Paginated retrieval should select
    the requested rows in SQL rather than decode a complete partition or table
    blob before taking a page.

    Preserve `location_id` and `row_key` for location-scoped metric rows, and
    `source_location_id` and `location_id` for operation correspondence rows.
    Location tables use `location_id`; spatial tables use `geometry`. Retain
    dataset/revision qualifications and result/table and named-output scopes in
    their metadata. Use the same names and applicable Pandera-derived schemas in
    Python, Arrow, and JSON, including empty tables. Keep completion
    records for empty partitions and outputs under item 11 without introducing
    public trace references for their rows or groups.

    Pagination must traverse every row of a fixed result once, without omissions
    or repetitions, including distinct stored rows with equal values. Preserve
    declared row identities where present; internal row addressing supports
    unpartitioned outputs without creating public semantic
    keys. Its cursor and traversal arrangement are implementation details;
    the API does not promise any relative row order. Separate reads and transport
    formats need not present rows in the same physical arrangement.

    Bulk consumers need an Arrow reader. Downloads retain the supported logical
    column types, IDs, geometry, and native CRS under item 6's simplified type
    contract. Verify SQL-to-Arrow-to-client conversion as part of implementation.
    Pagination and JSON row encoding details remain implementation choices within
    this client contract.

    [Server conversion](src/lyra/server/results.py),
    [client reconstruction](src/lyra/api/client/_tables.py),
    [GeoPandas Arrow reader](https://geopandas.org/en/stable/docs/reference/api/geopandas.GeoDataFrame.from_arrow.html).

15. **Separate storage integrity, location equality, and cache identity.**

    **Accepted.** Persistent table storage follows the SQLite row and transaction
    model in item 11. Standard byte checksums apply to actual byte artifacts where
    needed, such as generated downloads or exports; they do not require retaining
    serialized result-table blobs or hashing the whole database for cache identity.
    Concrete detection and reporting of damaged stored results remains part of
    shared storage implementation under item 6 and R7.

    Compare location contents by aligning records through qualified IDs and
    columns through names, then using library table comparison, including Arrow's
    comparison where used in memory. Physical row and column positions do not
    determine semantic equality. Supported logical types, values, geometry,
    location scope, CRS, and explicit scalar ordering fields remain significant.
    Use library alignment and comparison operations instead of custom normalization
    rules for every scalar representation. Potentially slower repeated table
    comparisons are an accepted tradeoff.

    Remove custom Arrow-buffer normalization where it exists solely for
    integrity or equality, and remove location-content fingerprints from cache
    identity. Cache keys use the accepted identity descriptors in item 9. A
    byte checksum identifies stored bytes; different physical arrangements of
    logically equal records can have different checksums.

    Stored values are addressed by their value key, a hash of the applicable
    computation or immutable location-record identity rather than output
    contents. Durable execution IDs identify reports separately from that value
    identity. Equal values do not establish that two requests are the same
    execution.

    Location-data identity is fully captured by
    `(dataset_id, revision, location_id)`, including `revision=None`, for
    records supplied by registered providers in all managed executions. This
    includes externally prepared locations exposed through registered datasets.
    Trust this identity without content hashing, comparison, or additional
    identity verification. Location content comparisons and storage
    integrity checks serve their separate purposes; neither is required to
    establish location-data identity.

    **Accepted.** Value-key contents follow the descriptor variants in item 9.
    Metric and operation computations use implementation identities, effective
    parameters, and bound input and origin identities. Provider records use
    only immutable location identity. Both include an identity-kind
    discriminator and use item 9's normalization, deterministic JSON encoding,
    and SHA-256 hashing.

    [Fingerprint implementation](src/lyra/data/content_fingerprints.py),
    [location equality](src/lyra/data/locations.py),
    [IPC verification](src/lyra/data/ipc_codec.py).

16. **Keep synchronous and asynchronous clients with simpler transports.**

    **Accepted.** Retain synchronous and native asynchronous consumer and
    administrator clients, backed directly by HTTPX's `Client` and `AsyncClient`.
    Remove the background event-loop thread and synchronous-to-asynchronous bridge.
    Share endpoint definitions, request construction, response validation, error
    interpretation, and table conversion across both interfaces, including Arrow
    downloads, paginated rows, and execution-report retrieval.

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
    operations. Use the same approach for paginated row access and report retrieval.
    Report inspection follows item 13's complete-inline or explicit-preview
    contract, with a full JSON download link for oversized reports.
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

    The execution report records the selected graph and operations, their effective
    parameters and declared sources, and observed execution/cache-reuse/skipped
    outcomes. Reporting does not filter routes, change tie preferences, or resolve
    ambiguity. Location-scoped, unpartitioned, and hybrid graphs all have reports.
    Invalid definitions and plan errors retain normal failure behavior. This
    resolves R9.

    [Route compiler](src/lyra/runtime/planning/catalog.py),
    [routing behavior tests](tests/test_canonical_routes.py).

19. **Remove stale feature-payload validation and simplify compatibility checks.**

    **Accepted.** Replace production `LocationPayload` calls, which pass empty
    feature lists, with focused scope and CRS validation. Remove the unused
    feature-list validation path and update test helpers to construct locations
    through supported public APIs. Preserve required validation of actual location
    contents at the shared construction and persistence boundaries, with Arrow
    and JSON conversion for downloads, previews, and paginated row access.

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

    A dataset is a registered collection of location records. A location is one
    member, containing its ID, geometry, and attributes. A dataset-provider node
    is the executable code that retrieves requested members; it is not a separate
    node for every location. Different requested subsets belong to the same
    dataset.

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
    The requested dataset's ID, revision, and description also supply its provider's
    external-origin metadata automatically under item 2. Provider acquisition enters
    the execution graph through the ordinary provider node, without a separate
    origin registration. Keep implementation identity and the graph bindings
    that select locations distinct from this dataset identity.
    Replace the compound family/source/edition identity with the dataset ID;
    separate geographical editions, observation periods, releases, and
    field-definition mappings are omitted from the descriptor. Multiple datasets
    can share a schema while retaining distinct IDs. Provider acquisition remains
    responsible for returning the requested dataset revision and rows.

    **Accepted provider lookup contract.** Providers receive the dataset ID,
    revision, and requested location IDs. They retrieve the authoritative records
    without arbitrary calculation parameters or upstream metric-table inputs.
    Parameters such as a nearby-hospital search radius belong to metrics or
    operations; they do not change the hospital records returned for fixed IDs.

    Development executions use the same provider lookup contract. Authors can
    expose a fixed local dataset through a registered provider and select subsets
    by ID for each run. Tests use in-process fake providers and isolated data.
    These providers obey the same dataset-identity, schema, and deterministic
    result contracts as other providers. Local development retains private metric
    roots through the shared execution machinery specified in item 2.

    Database connections, credentials, and timeouts are resource configuration
    inside the source boundary, not calculation parameters or record-key
    inputs. Any configuration that changes authoritative record contents must
    respect the location-data identity guarantee. An upstream computation can
    determine which IDs are requested; describe that graph dependency in the
    report separately from the provider's cache identity, without retaining a
    row-level selection history. Each provider record key contains only the
    immutable `(dataset_id, revision, location_id)` tuple and the record-kind
    discriminator used by shared key encoding. Provider node ID, code revision,
    cache setting, and acquisition batch do not affect that key or downstream
    record identity.

    **Accepted provider cache and acquisition scope.** A boolean setting
    controls record-cache reuse, with caching off by default. With caching on,
    reuse each needed cached record and acquire only missing IDs. With caching
    off, acquire the needed IDs without record-cache reuse. Both settings
    permit reduced selections because each record is immutable independently of
    other requested IDs. Batch acquisition as appropriate; shared inputs and
    metric/operation execution rules still determine which records are needed.
    A downstream cache hit can bypass acquisition entirely, and an empty needed
    selection invokes no handler. Persist records under their individual
    identities when caching or ordinary retrieval requires them, using item
    11's shared value layer.

    Provider schemas explicitly declare the non-null unique string `location_id`
    and active `geometry` columns alongside ordinary attributes. Require
    `geometry` to declare its expected CRS and existing validity requirements.
    Authors rename external ID and geometry fields to these names before return
    and avoid reserved-name collisions. Derive advertised CRS and correctly
    tagged empty results from that schema;
    do not add a separate CRS field to dataset registration. Different CRSs need
    corresponding schema variants. Geometry constraints belong in the schema.

    `(dataset_id, revision, location_id)` fully captures location-data identity and
    identifies frozen contents, including when the revision is `None`. This
    contract applies to registered provider data in every managed execution,
    including local development and locations prepared externally. Newly
    constructed locations receive their identities outside Lyra's location
    operations and are exposed through
    registered datasets, following the initial implementation boundary in item 4.
    Producers own the identity guarantee.
    Lyra trusts the declared tuple without reading, hashing, comparing, or otherwise
    verifying the contents to establish identity. Existing schema and structural
    validation remains independent of this trusted identity contract.

    Unversioned locations have the same immutability guarantee: `None` is a stable
    identity value, not a reference to changing latest contents. A change to covered
    geometry, attributes, or relationship fields stored in the dataset record
    requires a different revision value, location ID, or dataset ID. Dataset
    producers own this guarantee; Lyra neither interprets version tokens nor detects
    unreported changes. A revision identifies data without creating a historical
    snapshot. Record dataset ID and revision in the execution report and ordinary
    stored-result scopes. Include descriptions in discovery and reports, but
    exclude their text from dataset identity, routing, and cache keys.

    Computed operation mappings are computation results with their own identity,
    separate from the identities of the participating location records. Changing
    an operation's implementation or parameters can change its mapping without
    changing those records. For example, different search radii can map the same
    building to different hospital selections while all location identities remain
    unchanged. Record the operation implementation, effective parameters, and
    inputs in its computation identity as specified in item 9.

    A downstream location-record input is identified by its dataset/revision
    scope and selected IDs. Fetching A with B or C, changing provider code
    revision, or changing cache settings leaves A's identity unchanged.
    Operation correspondences used to select records retain their own
    computation dependencies and identities. Metric and operation keys include
    their accepted descriptor fields and use the normalization, serialization,
    and hashing from item 9.

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
    originating Lyra table/result reference when available. Preserve the applicable
    standard `location_id`, `source_location_id`, and `row_key` columns, with active
    geometry named `geometry`. Retain both source and target scope metadata when
    exporting a correspondence table. Geometry, CRS, and column types use standard
    GeoParquet representation. Do not assign a computed table one input dataset's
    identity when it combines several sources.

    Execution reports remain in Lyra's retained store. An export's originating
    result reference can lead to its execution report through that store; the file
    does not bundle the report or its graph description.
    Third-party software may discard custom metadata when rewriting the file.
    These portability limits and the removal of Lyra's exact-preservation guarantee
    are accepted concessions. Arrow downloads and JSON report retrieval remain
    separate supported interfaces.

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

    Result/table references for retrieval and durable execution IDs for reports
    follow the same ownership principle: shared definitions and validation across
    transports. Per-row and output-group trace-reference types are deferred. Exact
    syntax remains an implementation decision. Removing unused public names is
    acceptable in closed alpha; active reference functionality remains supported.

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

    **Accepted initial explanation feature.** Automatically retain an execution
    report for every managed graph, describing the request, selected graph,
    effective parameters, declared sources and revisions, node outcomes, and final
    status. Reports are retrieved by durable execution ID. Ordinary output tables
    and result access remain unchanged. All output scopes, including hybrid graphs,
    use this reporting contract.

    **Deferred until demonstrated need.** Provenance for individual findings,
    public row/group trace references, empty-group inspection, intermediate member
    listing, navigable input-selection and parameter-origin history, interactive
    graph traversal, graph visualization, recursive expansion, downstream-impact
    search, and detailed partial traces for failed executions are outside the
    initial implementation. Do not retain additional intermediate identity or
    membership records solely to support those features later. Explicit diagnostic
    output capture, arbitrary file-artifact lineage, fine-grained cell or parameter
    influence tracking, and automatic stored-result cleanup remain deferred.

    Exact contributing rows, rejected-candidate explanations, proof of exhaustive
    selection, and reconstruction of historical cache producers are outside the
    report guarantee. Lyra does not infer semantic dependencies from handler code.
    Differentiability is a separate long-term goal and is not specified here.

    Operations map existing locations. Creating and managing new locations through
    operations, including merging and splitting, is outside the initial scope;
    external preparation and registered datasets supply newly constructed ones.
    Caller inputs remain parameters and registered location selections under
    item 2. Local development preserves private metric roots and shares the same
    provider acquisition and execution machinery. Full location-record injection,
    arbitrary table injection, and caller-supplied stored-result references as
    dependency replacements remain outside the managed-execution contract.

    **Preserved execution and storage features.** Standard `location_id` ownership,
    stable string `row_key` values, ordinary unpartitioned outputs, hybrid graphs,
    named dependencies, parameter forwarding, operation dictionaries and
    handler-visible correspondence tables, shared and
    partition-related input selection, and complete empty-result semantics remain
    accepted. Keep all information these features need to execute, validate, cache,
    and retrieve results. Secondary metric location associations remain deferred;
    domain-named output ID columns receive ordinary schema validation. Structural
    names and required Pandera constraints are explicit under items 3 and 6;
    forwarded operation schemas are derived by Lyra and use immediate-source
    `source_location_id`, target `location_id`, and target `geometry`/attributes.

    Retain `off`, `full`, and `per_location` caching for metrics and
    operations, with their eligibility, independence, upstream execution, and
    assembly rules. Providers use boolean caching and may acquire reduced
    selections with either setting. Preserve deterministic computation
    identities, immutable location-record keys independent of provider
    implementation and acquisition batches, author-managed code revisions,
    external-origin declarations, and descriptor
    normalization/encoding/hashing. SQLite native rows, shared result/partition
    tables, typed output tables, scoped keys, atomic value publication, Pandera
    validation, SQLAlchemy integration, Arrow downloads, and paginated result
    access remain accepted. Reporting does not change cache matching or require
    historical producer links.

    **Resolved review scope.** R1 retains row identity and assembly. R2 retains
    values-only caching and node-level reuse reporting. R3 preserves empty-result
    completion without a finding-level inspection contract. R4 preserves cache
    and storage semantics. R5 retains provider and external-origin declarations.
    R6 retains primary ownership and operation correspondences. R7 retains shared
    validation and storage/report integrity. R8 retains effective parameters and
    the API/worker handoff without parameter-origin traversal. R9 preserves normal
    routing for all reporting scopes. R10 settles durable execution-ID access,
    automatic persistent local SQLite setup, and required result/report persistence
    before success. No review issue remains open.

Implementation touches the existing [node definitions](src/lyra/sdk/plugin.py),
[output contracts](src/lyra/contracts/outputs.py),
[dependency bindings](src/lyra/contracts/dependencies.py),
[execution metadata](src/lyra/contracts/provenance.py), and
[executor](src/lyra/runtime/execution.py). Translate the accepted contracts into
authoring interfaces, runtime and storage changes, and relevant end-to-end
verification. This is a design ledger; deferred features remain outside that
implementation scope.

## Resolved review decisions

The `R1`–`R10` identifiers are stable and separate from the feature numbers above.
All review issues are resolved. Their accepted constraints below match the current
execution-report, runtime, and storage contracts, including the reduced inspection
scope.

### R1. Row identity across assembled cache fragments

**Resolved — accepted identity and assembly contract.** Affects items 3, 4, 9,
11, and 14.

Within every location-scoped named output, identify a metric row by its qualified
owning `location_id` and author-supplied string `row_key`. Keys are non-null
and unique within the partition and output. `(building A, "roof")` and
`(building B, "roof")` are distinct rows. Keys remain stable across input
permutations and permitted changes in execution batches. Both standard columns
and their structural constraints must be explicitly declared in the output's
Pandera schema regardless of caching strategy. Missing or incompatible declarations
are definition errors, not an implicit scope change.

Downstream inputs identify rows through the named input, qualified partition, and
author key. Assembly preserves these identities across cached and newly computed
groups without renumbering keys. Stored values require no historical invocation
link. The execution report describes node execution and reuse; it does not attach
public trace references to individual rows or groups.

Operation correspondence rows use `source_location_id` for the immediate input
and `location_id` for the target, qualified by their separate dataset/revision
scopes and result/table and named-output scopes. Handler-visible tables retain
every pair and join the target's `geometry` and attributes, repeating shared
target values as needed. Provider records remain unique by `location_id`.
Unpartitioned outputs require neither an owner nor an author row key; whole-table
forwarding and internal storage addressing follow items 3 and 11. Structural
names are fixed by item 3 and schema requirements by item 6; transport envelope
syntax remains an implementation choice. R10 provides execution-ID access to reports.

### R2. Values-only caching and execution reporting

**Resolved — accepted values-only cache and node-outcome report contract.** Affects
items 1, 3, 4, 5, 8, 9, 10, 11, 12, 13, and 14.

**Accepted cache responsibility.** Cache entries map computation or
location-record keys to values and the schema, named-output, output-scope,
partition, and row-identity information needed for retrieval. Metric and
operation keys account for implementation revisions, effective parameters
including defaults, declared inputs and origins, and computation scope.
Provider record keys use immutable location identity, independently of provider
implementation, acquisition batch, and cache setting. Cache matching and reuse
require neither a historical invocation nor its report. The applicable key
identifies one immutable stored value; no independent storage version or
key-to-value-ID mapping is needed. Row keys identify rows within a partition,
not the entire computation.

**Accepted empty-result representation.** Publish a result header, applicable
partition records, and all named outputs together after successful validation.
A matching metric or operation header with zero rows represents a complete
empty value; absence is a miss. The cache policy must permit reuse. Partition
records preserve empty outputs for supplied locations. A full-selection header
can represent a persisted location-scoped computation over zero locations.
Unpartitioned headers cover all whole named outputs without location
partitions. No separate completion object or per-row completion field is
required. Empty inputs may produce nonempty unpartitioned outputs under item 4.

A provider record header requires exactly one resolved location row. An empty
needed selection creates no provider record entries or invocation. Unresolved
IDs are acquisition errors, not cached empty records. Provider records share
item 11's value layer and atomic publication rules under their individual
location keys.

**Accepted current report.** Record effective parameters without supplied/default
origin flags, the selected graph and declared sources, and node execution,
cache-reuse, or skipped outcomes. A node may both compute and reuse partitions;
the report represents that mixture without a durable event record for every
partition or row. A cache hit does not claim current handler execution or fresh
provider acquisition. Do not execute bypassed nodes to fill in a report or recover
the historical producer. Keep ordinary runtime references required for correct
assembly and retrieval, without a separate finding-level provenance graph.

Reports apply to all managed graphs. R10 requires current results and the report
to be durable before success. Complete stored values may be reused independently
of the outcome or report of the execution that first wrote them.

### R3. Empty results and completion without finding-level inspection

**Resolved — accepted completion semantics and execution reports.** Affects items
1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, and 14.

Metrics return ordinary named tables, with explicit Pandera declarations for
`location_id` and `row_key` in location-scoped outputs. Operations return ordinary
`dict[str, tuple[str, ...]]` correspondence mappings. Lyra derives and constructs
their downstream tables with `source_location_id`, `location_id`, and target
`geometry`/attributes. Authors supply no lineage payload or reporting wrapper.
Lyra validates outputs, tracks completion, and generates the execution report.

Successful completion covers every supplied primary partition in every named
output. For buildings A and B, a defects table containing only `(B, "roof")`
means A completed empty. An operation returning `{"A": (), "B": ("H1",)}`
means A completed with zero matches and B maps to H1. Keep these results usable
by consumers, cache assembly, and ordinary retrieval. Failed or unprocessed work
is not a successful empty result; unresolved targets remain acquisition errors.

Zero-match sources contribute no forwarded rows. Their completion remains in
the mapping's structural partition scope; empty forwarded tables retain their
derived schema, source/target dataset scopes, and target CRS without null targets
or fabricated geometry.

When values are persisted, item 11's headers and partition records distinguish
empty groups, zero-location computations, and absent results. A per-location
request covering zero locations has no partition computations. It still has an
execution report describing its request and observed outcome without a fabricated
partition, row, or invocation. Unpartitioned outputs use whole-table completion,
including empty tables, and have ordinary execution reports.

Providers have no empty-record cache entries: every published location key has
its resolved record. No needed IDs means no provider invocation or record keys,
with the observed outcome still recorded in the execution report.

Retain membership and completion where execution, caching, or result retrieval
requires them. Do not persist additional intermediate member identities or empty
group trace records solely for reporting. There is no first-version public
inspection interface for an individual row, an empty group, or its upstream
members. Node-level reports replace that explanation scope while preserving
all execution and cache completion behavior.

### R4. Provider record caching and metric/operation cache eligibility

**Resolved — accepted deterministic computation and storage contract.** Affects
items 1, 2, 3, 4, 6, 7, 8, 9, 11, 14, 15, and 20.

The computation and location-record descriptor contents, normalization,
serialization, and hashing are accepted. The partition and binding rules below
establish unordered output membership, the rows a consumer receives, upstream
execution scope, and dependency identities. Items 9 and 11 accept immutable
values addressed directly by their computation or location-record key, the
shared result/partition tables and typed output tables, scoped primary and
foreign keys, and atomic value publication. They use the accepted SQLite native
row storage, supported column types, and Pandera and SQLAlchemy integration.
Additional query indexes and physical SQL details are implementation choices.
Effective parameters, graph bindings, and the API/worker parameter handoff
follow items 5 and 8 and R8. R10 settles durable result/report publication.

**Accepted constraint — deterministic results.** Metrics and operations obey
item 9's frozen-input and determinism contract across output scopes and caching
strategies. The same computation key identifies the same logical output values
and membership. Authors express output-affecting changes through effective
parameters or declared identities used in the key. Different logical results
under the same key are a contract violation. Trust this contract without
output-content hashing, comparisons, or behavioral proofs. For metrics and
operations, per-location independence remains a separate author promise.
Providers obey the stronger immutable-record contract below.

**Accepted constraint — trusted location-data identity.** The tuple
`(dataset_id, revision, location_id)` fully captures location-data identity and
identifies frozen contents, including when the revision is `None`. Apply this
contract to registered provider data in all managed executions, including local
development and externally prepared locations exposed through registered
datasets. Dataset producers must use a different tuple when covered contents
change. Use the tuple directly for cache identity without content hashing,
comparison, or additional identity verification. Producers own this guarantee.
Existing schema and structural validation remains independent of it.

**Accepted constraint — boolean provider caching.** Providers use a boolean
setting, off by default. With caching on, reuse records individually by
immutable location identity and acquire only missing needed IDs. With caching
off, acquire the needed records without provider cache reuse. Either setting
permits reduced selections and batched acquisition because a record's contents
are independent of other requested IDs. Shared consumer inputs retain their
complete selections, and metric/operation execution scope still determines
which records are needed. A downstream hit may bypass acquisition entirely. An
empty needed selection invokes no handler and creates no record cache keys.
Changing provider cache settings does not change record identity or delete
retained values.

Provider record cache keys and downstream location-record input identities
exclude provider node ID, code revision, acquisition batch, and resource
configuration. Provider implementation identity remains in the execution
report. Changing geometry or attributes requires a different location-data
tuple.

The frozen contents are the dataset record, including relationship fields stored
in that record. Computed operation correspondences have their own computation
identity; changing a correspondence through different parameters or implementation
does not require changing the participating location records' identities.

**Accepted constraint — author-managed implementation identity.** Use
`(node_id, code_revision)`, where the code revision is an optional opaque token
and `None` is a stable identity value. This applies to metrics, operations, and
executable provider handlers. Metrics and operations include implementation
identity in computation keys alongside effective parameters, inputs, and
external-origin identities. Provider implementation identity serves execution
reporting; record cache keys and downstream record identities use only the
location-data tuple. Plugin authors are responsible for changing the token or
node ID when implementation or behavior-affecting declarations change. Lyra
trusts that declaration without automatic change detection, code hashing, or
inferred versions. Authors may automate token updates in their own tooling.
Propagate upstream metric and operation implementation revisions through their
computation identities. Provider code revisions do not invalidate cached
records or downstream computations. Deployment build revisions retain their
job-compatibility and reporting purposes without acting as additional
cache-invalidation tokens.

**Accepted constraint — output scope and location partitions.** Providers,
operations, and location-scoped metrics have a location-based partition
mapping, independently of cache policy. Location-provider nodes are partitioned
by their own location identities, with one returned row per successfully
resolved location. Location-scoped metrics and operations have at least one
location input and partition by one named input. Lyra selects the sole input
automatically; otherwise the developer explicitly selects one. Each
location-scoped metric output row has one primary partition owner, with zero or
more rows per key in each named output. Each named output's Pandera schema
declares non-null string `location_id` and `row_key` columns with composite
uniqueness. `location_id` is bound to the selected primary input's represented
locations and dataset/revision scope. Its values must belong to the distinct
`location_id` values actually supplied through that input. Domain-named output
ID columns are ordinary data and establish no location-reference or forwarding
behavior. Structural names and active `geometry` follow item 3.

`row_key` is stable and unique within each owning partition. Missing or
incompatible structural declarations make the metric definition invalid.
Per-location independence remains a separate author promise. A forwarded
primary input may contain repeated target `location_id` values under different
`source_location_id` values; primary partitions still refer to the represented
targets. Source correspondence ownership does not implicitly change downstream
partition ownership.

A metric may explicitly select unpartitioned scope under item 3, bypassing primary
location-input inference. All its named outputs then belong to the whole
computation, with no mandatory location-owner column or stable row key. It uses
ordinary schema validation and supports `off` or `full`, never `per_location`.
Its inputs retain complete bound selections and its outputs are automatically
shared whole-table dependencies. Hybrid graphs are supported and receive ordinary
execution reports. Mixing output scopes within one metric is outside the initial
implementation.

**Accepted constraint — operation correspondence pairs.** Represent an operation's
correspondences as distinct `(source_location_id, location_id)` pairs with
declared dataset/revision scopes. Partition relationship rows by their first
component. Each input location has zero or more pairs, and different partitions
may reference the same target location without sharing the same relationship row.
This follows the same ownership and caching model as metric result rows. Keep
correspondences for execution, caching, and retrieval under item 12. Targets must
be existing locations resolvable through the declared target dataset's provider.
Creating locations through operations, including merging or splitting, is outside
the initial implementation; the accepted boundary is specified in item 4.

Forward one row per pair to downstream handlers, joining target `geometry` and
attributes from validated provider records. Shared targets remain repeated in
the forwarded table while provider records remain unique by `location_id`.
Lyra derives the pair schema and does not rerun provider uniqueness or
cardinality checks on expanded rows. Immediate source and target scopes remain
attached to each input; earlier-hop associations require explicit composition.

**Accepted constraint — empty groups and independence.** A successful empty
group is a completed result that can be cached and reused, distinct from a
cache miss. Each metric or operation `per_location` entry contains the
location's complete group across all named outputs. Authors are responsible for
validating the independence needed for `per_location`; Lyra does not infer or
prove it. Declaring a partition mapping alone does not make a metric or
operation independently computable per location. Provider records are
independent by their immutable identity contract and require one resolved
record per cache entry.

**Accepted constraint — operation dictionary interface.** Authors provide an
operation's correspondence payload as a dictionary from input location IDs to
tuples of target IDs. Keys must belong to the supplied partitioning input. A
successful return covers every supplied key; omitted keys and empty tuples both
normalize to completed empty groups. Reject repeated target IDs within a tuple;
allow the same target under different input keys. Target-tuple order and dictionary
insertion order have no semantic significance; assemble groups by explicit keys.
These rules apply to the distinct represented locations in the current
invocation's supplied primary input.
The dictionary is a Python interface using string location IDs with strict
Pydantic validation. JSON uses string keys and arrays of target strings. When
persistence is needed, SQLite stores `source_location_id`/`location_id` pairs and
partition scopes for empty results under item 4; tuple-valued table cells are
unnecessary. All operation handlers return the ordinary mapping without an
additional DataFrame schema. Lyra generates their forwarding tables and schemas
under item 6, using provider values and preserving every correspondence. Lyra generates
the node's execution-report entry without an author-supplied reporting wrapper
or additional per-pair or empty-group trace records.

**Accepted constraint — declared inputs and execution reports.** Authors declare
executable node inputs, parameter bindings, source boundaries, and partition-related
or shared selection behavior. These declarations remain necessary for execution
and cache identity. The report describes the selected graph, effective parameters,
sources, and node outcomes, without per-row input links or member lists.

Computation identities account for all declared inputs. Different partitions may
use a shared input while remaining independently computable. Authors own that
independence promise. Input selection and upstream execution follow the rules below.

**Accepted constraint — consumer input selection.** Each consumer's named input
has partition-related or shared selection semantics, as specified in item 3.
Unpartitioned metrics receive complete bound input selections. Their output tables
are automatically shared dependencies; partition-selection bindings on those
outputs are invalid. Neither downstream cache misses nor matching IDs authorize
filtering them by location.
Partition-related inputs supply the rows associated with requested primary keys;
shared inputs retain their complete bound selection across partitions. Infer
partition-following behavior when a dependency explicitly forwards the consumer's
primary location input into its own partitioning input, or an operation derives
targets from that primary input through its correspondence mapping. The primary
input itself follows the node's partition declaration.

For other inputs of a location-scoped consumer with no established
relationship, require an explicit shared binding or a declared correspondence.
An explicit shared declaration can override partition-following behavior.
Matching names, types, or IDs alone is insufficient. Put the optional selection
setting on existing input declarations; concrete API spelling remains an
implementation choice. These semantics apply independently of cache policy.
They determine consumer input selection; reducing metric or operation execution
also requires the independence promise described below. Providers may always
acquire the reduced selection of records still needed.

**Accepted constraint — upstream execution scope.** Cache-driven reduction of
downstream work may narrow an upstream metric or operation partition request
only when that producer declares `per_location`. The producer then checks its
own cache and computes only its missing requested keys. Metrics and operations
using `off` or `full` retain their initial input binding for the current
request, before cache-driven reduction. When the upstream result is needed,
`off` executes on that selection, while `full` reuses a matching complete
result or executes on a miss. Initially bound inputs describe the request's
selection, not the entire dataset.

For a metric or operation producer initially bound to A/B/C in this request
when only downstream B/C remain to be computed, `off` and `full` operate on
A/B/C; `per_location` receives B/C and may compute fewer keys after its own
cache lookup. The partition-related consumer receives only the B/C groups in
every case. Shared inputs remain at their complete bound selections, including
during reduced execution. Apply these rules at each upstream metric or
operation dependency.

Provider acquisitions may narrow with either boolean setting. If only B/C
records are needed, caching off acquires B/C, while caching on acquires only
their cache misses. If an upstream `off`/`full` computation or a shared input
still needs A/B/C, the provider must supply those records. Acquisition
narrowing preserves every consumer's required input selection.

**Accepted constraint — node-local disabled caching.** Disabling a producer's
own cache does not require its execution when a valid downstream cache hit
satisfies the request. Metric/operation `off` and provider `False` mean execute
whenever invoked, with the same deterministic result for the same computation
or location identity. Changed observations require changed key inputs. Disabled
caching does not disable downstream caching. Persisted values remain
addressable by their keys even when the node's own policy prevents their cache
reuse.

**Accepted constraint — unordered collections.** Location selections, operation
target groups, and table rows have no semantic ordering. Lyra makes no
developer-facing ordering guarantees for forwarding, execution, cache retrieval,
assembly, or transport. Resolve identified records and relationships by explicit
keys and address columns by name. Reordering a collection alone does not change its computation
identity. Authors must make meaningful sequence information explicit in data or
parameters and produce the same logical results for permutations of input rows,
preserving declared identities where present. Explicit scalar ordering fields and sequence-valued parameters remain
significant. Internal sorting for canonical key serialization or alignment does
not establish a public ordering contract. Pagination traverses a fixed result
without omitting or repeating rows, using declared identities where present and
internal storage addressing otherwise, as specified in item 14.

**Accepted constraint — shared mapping storage and reporting.** Operations use
the same cache machinery as metrics. Store mappings once in the shared value layer
when needed for caching or result retrieval, retaining membership and empty
completion. Cache lookup and current results may share those immutable values.
An `off` operation still executes when invoked even if its values were previously
stored; a downstream hit may bypass it. The report records observed node outcomes
without forcing uncached intermediate mappings or selection histories into durable
storage. Sharing target location records does not merge the correspondence pairs
needed to execute operation chains.

**Accepted constraint — upstream computation identity scope.** A metric-output
or operation-correspondence input reference identifies its producer's logical
computation, named output, and selected partition groups where applicable. An
unpartitioned input references the complete named table under its producer's
whole computation identity, without partition selection. For a `per_location`
producer, use the computation identity of each selected partition, independent
of its physical execution batch when its effective inputs, parameters, and
shared context match. For `full` or `off`, use the logical identity of the
producer's complete initially bound selection, even when the consumer selects
only some output groups. Shared inputs contribute their complete bound
selection. Derive these logical identities from implementations, parameters,
input identities, and declared external-origin IDs and revisions, without
job/run IDs, storage references, or output-content hashes. An `off` producer
still has a logical identity. Its computation key also identifies its values
whenever they are stored. Current execution reports describe node uses and
outcomes separately; stored values require no historical producing invocation.

Provider-supplied records contribute only their dataset/revision scope and
selected IDs. Their identities do not include provider implementation or
acquisition batch. Fetching A with B or C does not change A's identity.
Operation correspondences used by a binding retain their computation
dependencies and identities independently of the provider lookup of target
records.

For example, with A/B cached and A/B/D requested from a `per_location` producer,
only rows representing `location_id` D are supplied through its primary location
input to the executing handler, retaining distinct incoming correspondences.
Dependencies partitioned by the same location supply only D's groups, while shared
inputs remain complete. The consumer receives assembled A/B/D groups. With only
an A/B/C entry, a `full` producer computes A/B/D on the miss; `off` computes the
complete A/B/D invocation when needed. Logical per-location entries hold complete
groups across all named outputs and may reference partition values stored in a
shared batch; they do not require one handler call per key. Different
full selections have separate cache entries and remain available under
the retention policy. Physical partitioning of SQL rows does not enable subset
cache matching for `full`.

**Accepted constraint — identity-key contents.** Use shared descriptor variants
with an explicit identity-kind discriminator. A location-record descriptor
contains only that discriminator and `(dataset_id, revision, location_id)`. A
metric or operation computation descriptor includes its discriminator,
`(node_id, code_revision)`, the declared output scope, and either a complete
bound selection or qualified partition scope. Metrics add effective validated
parameters including defaults, all named input identities, effective direct-origin
IDs and revisions from node and resource-binding declarations under item 2, and a
primary location input's name when location scoped. Unpartitioned metrics have no
primary location input or owning partition in their descriptor and cover all
complete bound inputs. Operations
add the metric components and their declared target dataset ID/revision.
Location-record inputs identify their dataset scope and selected IDs;
metric-output and operation-correspondence dependencies use the accepted
upstream logical identities, named outputs, and selected groups, including
correspondences used to derive location selections. Shared inputs retain their
complete bound selection. Reference upstream computation keys without embedding
entire ancestor descriptors. Provider node ID, code revision, cache setting,
resource configuration, acquisition batch, and the graph path selecting records
stay outside record keys. The provider's dataset ID and revision already
represent its derived origin; no independently managed origin token is needed.
Origin descriptions are excluded from keys. Upstream metric and operation
origin changes propagate through their computation keys; provider dataset
changes propagate through location identities. Shared encoding and key
generation serve both descriptor variants across node kinds and output scopes,
independently of physical storage.

Per-location descriptors cover one primary partition and its applicable inputs,
including complete shared inputs. Full and uncached descriptors cover their
complete initially bound input selections. Exclude incidental ordering, job/run IDs,
storage references, and location/output content hashes; preserve meaningful
explicit sequences in data and parameters. Hash a deterministic descriptor
encoding into the cache key using the accepted method below.

**Accepted constraint — descriptor normalization and encoding.** Use Pydantic's
validated effective parameter values and serialization under item 7's supported
JSON-value contract, including all defaults and null fields. Omitted and
explicit equal defaults have the same key, without supplied/default origin
tracking. Preserve scalar distinctions remaining after validation. Arrange
unordered identity collections consistently by explicit keys, ignore dictionary
insertion order, and preserve meaningful parameter sequence order. Serialize
the descriptor as deterministic JSON with sorted object keys, fixed compact
options, and non-finite numbers rejected, then hash its UTF-8 encoding with
SHA-256. Use the standard JSON and hashing libraries. Reject unsupported values
without arbitrary string fallbacks; do not hash location or output contents.
Use the same encoding and hashing for location-record descriptors; their
identity-kind discriminator keeps record and computation key spaces distinct.

**Accepted constraint — SQLite row storage.** Use item 11's shared
`stored_results` and `stored_partitions` tables and one typed data table per
named output or provider dataset registration. `stored_results` is keyed
directly by `value_key`, the computation or location-record key itself, and
retains `node_id` for metric/operation computations, explicit output scope,
and, for location-scoped results, `partition_dataset` and `partition_revision`.
The latter two fields are the existing owning dataset's ID and revision, not
additional author controls. `stored_partitions` is keyed by `(value_key,
location_id)` and references the result header. All location-scoped metric rows
are keyed within their named-output table by `(value_key, location_id,
row_key)` and reference their partition. Provider records have one header,
partition, and resolved data row per immutable location key. Their header has
no `node_id`; the dataset registration identifies the schema and data table.
Metric `location_id`/`row_key` fields and provider `location_id`/`geometry` fields
are the actual standard columns in their Pandera schemas. Operation rows use
`source_location_id` and target `location_id`, with primary key
`(value_key, source_location_id, location_id)`; their source field references the
owning partition. Preserve both dataset/revision scopes. Joined forwarding
tables reuse these pairs and provider records without requiring another stored
copy. Retain logical
schemas once with registered output definitions or dataset registrations.
Additional physical row addressing for unpartitioned outputs remains an
implementation detail.

Unpartitioned results have no owning dataset or location partitions. Their rows
reference the computation directly, with internal row identifiers as needed for
storage and pagination. These identifiers imply neither semantic row identity nor
ordering. The explicit scope distinguishes these results from location-scoped
computations over zero locations; no synthetic location is introduced.

A metric or operation `per_location` key identifies one complete partition
across all named outputs; a `full` key identifies the complete bound selection.
Publish the header, applicable partition records, and all outputs in one
transaction. A known partition with zero data rows has completed empty outputs.
A successful persisted location-scoped full-selection computation over zero
locations has a header with no partition or value rows. For an unpartitioned
result, the header covers all whole named outputs, including empty tables;
empty inputs can produce nonempty outputs under item 4. An empty per-location
request has no partition computations and remains recorded in its execution
report. These records preserve completion without a separate completion object
or a fabricated row or invocation.

An empty needed provider selection creates no record entries or invocation.
Unresolved IDs are acquisition errors, not cached empty records. Several
resolved records may be published atomically in one batch under their
individual keys.

Cache reuse follows the node-kind cache policy and matching published value.
The initial layout has no independent storage/version identifier,
cache-to-value mapping table, or `cache_available` flag. Independent per-entry
invalidation is deferred. Repeated persistence under the same key retains the
immutable value; assembly and result retrieval reference existing keys and
groups. Keep execution reports separate from those values. Persist operation
correspondences once when needed for caching or result retrieval. R2 defines
the independent value and report responsibilities.

Item 6 specifies supported scalar columns, WKB geometry cells, and the
Pandera-derived storage description. Use SQLAlchemy Core and standard dataframe
and geometry conversions, batch inserts, and indexed grouped reads. Arrow is a
download format generated from selected SQL rows. The workload assumptions and
exploratory performance evidence are recorded in item 11; the integrated
implementation still requires representative performance verification. No legacy
result readers or migrations across incompatible code/schema changes are required.

Structural Pandera schemas use the fixed names and constraints in items 3 and 6.
Providers and metrics declare their own columns; Lyra derives operation input
schemas and preserves source/target scope metadata. Ordinary attributes cannot
collide with structural names. Empty results retain the same schema as populated
ones, and validation remains at the shared boundaries before storage or forwarding.

**Resolution.** The rules above settle per-node caching eligibility and
behavior, computation and location-record identities, the value-storage layout,
scoped relationships, and atomic publication. Preserve the accepted
independence and unordered-collection contracts. String metric-row keys and
structural validation follow R7. Durable execution-report access and
publication follow the accepted R10 contract.

Use these rules to define which metric and operation output declarations may
select `per_location`, and validate providers' boolean cache settings. Apply
the assembled row-identity contract accepted under R1.

### R5. Provider acquisitions and declared external origins

**Resolved — accepted declaration and acquisition-reporting contract.** Affects
items 1, 2, 3, 6, 7, 8, 9, 12, 13, and 20.

**Accepted provider boundaries.** Dataset registration establishes the
provider's external boundary. Derive origin ID, revision, and description from
the requested dataset, without a second origin registry. Keep provider node ID
and code revision as implementation identity in execution reports,
independently of record keys. The report identifies provider execution, cache
reuse, or skipped work; only actual execution represents a new provider
acquisition.

**Accepted direct external origins.** Use the immutable, reusable `Origin` value
type in item 2, with a stable `id`, optional opaque `revision`, and optional
description. An origin need not be a registered dataset. Each node that directly
acquires data from an origin declares it, even when an upstream dependency also
exposes that origin. Nodes consuming the source only through upstream outputs need
no direct declaration for it. Authors may reuse one origin object across nodes;
identity depends on `(id, revision)`, not the Python instance.

**Accepted I/O resource integration.** Allow origins on the existing `Resource`
bindings used by metrics and operations, as illustrated by
`Resource("census_db", origins=(census_origin,))`. Referencing that binding in a
handler's resource annotation contributes its origins to the node. Explicit
node-level origins remain supported for acquisition outside managed resources.
The effective direct-origin collection combines both declaration locations and
deduplicates `(id, revision)` within each node. An empty effective collection
promises no extra external acquisition. Declaration placement does not change
cache identity.

Collect and validate this metadata before resource acquisition and cache lookup,
without initializing resources, reading credentials, or contacting services.
Discovery, reporting, and computation keys use the same effective collection.
Bindings may name different origins while sharing a resource instance; resource
names retain their configuration and lifecycle roles. Origin metadata requires no
additional resource instances, separate registry, query interception, or
method-level source tracking. Resource operation logs remain diagnostics. Dataset providers
retain the automatic origin derived from the requested dataset registration.

Authors own complete dependencies and origins for every output scope and caching
strategy. Undeclared acquisition violates the contract. Lyra does not inspect code
or police I/O; internal queries, file reads, and processing are outside the report
guarantee. Connection details, query text, file hashes, and processing descriptions
are not required metadata. Reports distinguish declared sources from actual node
execution without proving which source an executing handler internally accessed.

**Accepted identity and validation.** Origin IDs/revisions participate in cache
keys; descriptions are informational. Provider dataset identity already
supplies its origin. Metric and operation origin changes propagate through
computation keys; provider dataset changes propagate through immutable location
identities. `revision=None` means fixed unversioned data, including in
development. Validate node declarations, the standard structural Pandera schemas,
and origin descriptors at shared boundaries before acquisition. Missing or
incompatible `location_id`/`row_key` declarations on a location-scoped metric are
invalid definitions. Every valid
graph, including unpartitioned and hybrid graphs, has the same execution-report
contract.

**Accepted input scope.** Managed executions accept parameters and registered
location selections. Providers supply records; Lyra resolves table dependencies
through declared upstream nodes. `run_development` retains public and private
concrete roots while sharing request preparation, acquisition, validation, and
execution machinery. Full location-record injection, arbitrary table injection,
and caller-supplied result references as dependency replacements remain outside
the initial implementation.

Providers use the narrow lookup interface and trusted
`(dataset_id, revision, location_id)` identity in item 20. Caller-selected IDs are
request context, distinct from acquisition or reuse of their records. An upstream
computation may select requested IDs; its graph dependency does not add arbitrary
parameters or upstream-table inputs to the provider interface. Reporting does not
require durable row-level selection history.

Provider caching is boolean and off by default. Both settings permit
acquisition of only the records still needed; enabled caching reuses records
individually and acquires only misses. Provider implementation and
acquisition-batch identity do not affect record cache keys or downstream
location-record inputs. Metric outputs and operation correspondences retain
their computation identities. Shared input requirements and the
metric/operation execution-scope rules still determine the needed records.
Empty selections and downstream cache hits may bypass acquisition.

**Resolution.** These declarations, interfaces, identity rules, and ordinary node
outcomes settle R5. Primary ownership and correspondences follow R6, structural
validation follows R7, and durable report access/publication follows R10.

### R6. Metric ownership and location correspondences

**Resolved — accepted primary ownership and operation-correspondence contract.**
Affects items 3, 4, 6, 12, 13, and 20.

**Accepted metric ownership.** Every named output of a location-scoped metric
explicitly declares non-null string `location_id` and `row_key` columns in its
Pandera schema, with uniqueness of their combination. `location_id` is the owner
associated with the selected primary input and must belong to the distinct
represented locations actually supplied through that input. Dataset and revision
scope come from that named input. Reject owners that exist elsewhere in the
dataset or request but were not supplied to the invocation.

Multiple rows may share an owner; their `row_key` values distinguish them within
the partition and named output. A supplied owner may produce zero rows. Validate
non-nullness and membership at the existing shared producer boundary, without
another provider lookup. Missing or incompatible structural schema declarations
remain invalid definitions. The column names are fixed; authors still select
the primary named input under item 3.

**Accepted metric-column scope.** Nonstructural columns, including domain-named
IDs of other locations, are ordinary data subject to their declared schema
checks. Spatial outputs use the standard active `geometry` column. Structural
names are reserved for their accepted roles. Lyra stores,
returns, and forwards their values without interpreting them as location references
or acquiring records. Secondary metric association declarations and row-to-location
links beyond the owner are deferred from the initial implementation. Ownership
and row identity retain their execution, validation, and storage responsibilities
without a finding-level trace guarantee.

All named input dependencies, selection rules, and their computation-key identities
remain supported. Operation correspondences continue to derive and forward location
selections. Unpartitioned metrics retain their whole-table contract without
mandatory ownership or row keys. Ordinary ID columns establish no additional
dependency edges. All graph scopes receive execution reports.

**Accepted operation identities.** An operation correspondence row contains
`source_location_id` for the immediate input and `location_id` for the target,
qualified by their separate declared dataset/revision scopes. Several rows may
refer to the same target while belonging to different source partitions. The
pair is unique within the operation result/output scope.

Operation correspondences use the dictionary-of-tuples Python interface specified
in item 4. Omitted input keys and empty tuples both represent zero matches; the
structural key-membership and target-uniqueness checks are specified there.

**Accepted constraint — forwarding and resolution.** Operation authors return
the dictionary directly. Lyra validates it, resolves the distinct target records,
and forwards one table row per source/target pair. Each row contains standard
`source_location_id`, target `location_id`, and the target's `geometry` and
attributes. A shared target appears under each source that selected it. The
authoritative provider table and record cache retain one record per location.
This gives batched handlers the associations explicitly without row-order assumptions.

Lyra derives the forwarding schema under item 6; operation authors declare no
additional DataFrame schema. Reuse already validated provider values and mapping
identities; do not rerun provider uniqueness or cardinality checks on repeated
target rows. Zero matches yield no rows, with completed empty groups tracked
separately and an empty table retaining its schema and scopes.

Retain immediate mappings along chains: A → B → C yields B/C pairs in the final
hop, with C's geometry and attributes. Consumers needing A/C associations declare
the relevant inputs and explicitly compose the qualified mappings. Keep each
column's meaning and source/target metadata unchanged. Unresolved targets produce
an error during normal acquisition and are not silently converted into zero
matches; no separate preflight existence lookup is required.

**Accepted constraint — mapping representation.** Individual IDs are strings.
Python mappings use `dict[str, tuple[str, ...]]`; JSON uses objects with arrays of
target strings. When persistence is required, SQLite stores
`source_location_id`/`location_id` pair rows and
structural partition/output scope for empty groups. A published cache entry with
zero rows is a valid empty result under R2. Preserve membership and use
the standard source and target fields for relationship rows, with their
surrounding scopes, as specified in item 4. Reporting does not add provenance
links to returned pairs or completed empty groups, or force uncached intermediate
mappings into storage.

**Accepted constraint — existing target locations.** Every operation target must
resolve through the declared target dataset's provider. One-to-many and
many-to-one correspondences between existing locations remain supported.
Operations may perform spatial calculations to determine correspondences, but
creating locations, assigning their identities, and registering their geometries
and attributes are outside the initial implementation's operation contract. Newly
constructed locations, including merged or split locations, are prepared externally
and exposed as a registered dataset. Their external preparation is outside the
execution-report guarantee.

**Resolution.** Primary metric ownership, ordinary handling of other output
columns, and the accepted operation rules above settle R6. Operation-row identities
follow R1's identity and current-result scopes. A target location retains its own
identity, distinct from correspondence rows and computation identity. String
metric-row keys and storage/report integrity follow R7.

### R7. Identity validation and storage/report integrity

**Resolved — accepted string identities and shared integrity boundaries.** Affects
items 3, 4, 5, 6, 11, 12, 13, and 22.

**Accepted string keys and ownership.** Metric `row_key` values are strings, non-null and
unique within one owning location partition of one named output, with stability
under the permutations and batching permitted by item 3. Authors represent numeric
or composite identifiers as stable, unambiguous strings. Use the ordinary declared
string-column validation at the shared producer boundary and standard JSON, SQLite
text, and Arrow string representations. Public reference models use shared strict
string types without an additional string/repr fallback or custom identity codec.

Every location-scoped named output explicitly declares `location_id` and
`row_key` in Pandera with non-null string types and composite uniqueness. Missing
or weakened structural declarations are invalid definitions. R6 binds the owner
`location_id` to the distinct represented locations in the supplied primary
input, qualified by that input's dataset and revision. Check membership without
another provider lookup. Provider schemas explicitly declare unique non-null
string `location_id` and active `geometry` with its expected CRS. Location records
and operation mapping IDs retain their strict string contract through Pydantic.
Unpartitioned outputs require ordinary schema validation without mandatory
ownership or row keys, and use `geometry` when spatial. Secondary metric
associations remain outside the initial scope.

Reserve structural names for their roles under item 3. Authors rename external
fields before return; unknown aliases and collisions cannot replace required
columns. Pandera owns structural column type, nullability, and uniqueness checks
alongside ordinary attribute validation, with shared invocation checks for scope
and membership. Authors may add compatible checks without weakening requirements.

Operation-derived inputs have Lyra-generated schemas containing non-null string
`source_location_id` and target `location_id`, unique as a pair, plus target
`geometry` and attributes. Provider `location_id` uniqueness is checked before
expansion; repeated targets across source rows are valid. Derive column metadata
and enforce mapping/scope construction invariants without revalidating provider
values or translating provider checks to relationship-table semantics. Operations
continue returning mappings and require no additional author-declared table schema.

**Accepted names and completion.** Reject references to unknown input or output
names in definitions and resolved bindings during definition or plan validation,
before resource acquisition. At the shared producer boundary, a metric must return
every declared named output and no undeclared output names. Empty outputs require
explicit schema-valid empty tables. Apply this rule to both metric output scopes;
column validation uses the declared Pandera schemas and mandatory structural
contract. Empty forwarded operation tables retain their Lyra-derived schema,
source/target dataset metadata, and target CRS.

Operation mappings retain their accepted completion semantics: reject input keys
not supplied to the invocation and repeated target IDs within a tuple; normalize
omitted supplied keys to empty target groups after a successful return. Omitting
an operation's location key is distinct from omitting a declared metric table.

**Accepted automatic record construction.** Lyra builds the execution report from
the normalized request, resolved graph, effective parameters and declared sources,
and observed execution/cache outcomes. Authors return ordinary tables or mappings.
Report entries belong to their execution and resolved node use. Actual execution
is reported only when handlers run; reuse and skipped work do not invent an
invocation. Reports require no per-row/group provenance references or intermediate
member records.

**Accepted enforcement and ownership.** Preserve shared validation, scoped result
keys and relationships, ordinary construction invariants, database constraints
where applicable, and transactional writes. Verify construction, persistence, and
retrieval with isolated behavioral tests. No separate report revalidation subsystem,
recursive integrity sweeps, or repeated producer validation is required. Cache
reads and public input boundaries retain their ordinary validation.

Authors remain responsible for correctness, intended ownership, key stability,
determinism, partition independence, and complete dependency/origin declarations.
A structurally valid row can still have an unintended supplied owner; Lyra cannot
infer that semantic error. Broken internal storage/report relationships indicate
an implementation or store-integrity failure.

**Accepted lookup outcomes.** Shared parsing rejects malformed execution/result
references as validation errors. An otherwise valid reference without a target
returns not found. Broken required relationships in retained report/result data
are integrity errors. Use ordinary lookups without rerunning handlers, acquiring
external data, or fabricating records. Intentionally unstored intermediate values
are outside report retrieval. Empty results, zero-location requests, and reused
or skipped work are valid outcomes under the ordinary execution/report contracts.
Exact reference syntax and transport error models remain implementation choices.

**Resolution.** String identities, ownership, named-output completeness, shared
validation, and ordinary storage/report integrity settle R7. R10 settles durable
report access and required persistence before success.

### R8. Parameter normalization across the API and worker

**Resolved — accepted contract.** Affects items 5, 7, and 8.

The API-to-worker handoff and omission of supplied/default origin tracking are
accepted. Parameter forwarding remains part of the graph; reports retain its
binding descriptions and effective values without navigable parameter-origin
records. Treat each named parameter as one complete value, including nested
objects and lists. These constraints settle normalization, transport, and reporting.

**Accepted constraint — shared parameter representation.** Item 7 fixes the
initial supported values as ordinary JSON scalars and containers, with finite
numbers and nested Pydantic models. Pydantic owns validation, defaults, schema
generation, and serialization. Include all effective fields, defaults, and null
values; parameter models cannot use custom serializers or field exclusions.
Remove parallel JSON Schema validation, custom default handling, and the custom
recursive parameter serializer. Item 9 defines deterministic JSON and SHA-256
computation keys over these effective values. Explicit-versus-default origin
is not tracked and does not affect the key.

**Accepted constraint — trusted custom validators.** Support ordinary Pydantic
field and model validators declared on parameter models. Authors must make them
deterministic, idempotent, and free of external I/O: the same input produces the
same effective values, and validating normalized values after JSON transport
preserves them. Authors own compliance and testing. Lyra retains ordinary
Pydantic validation without custom behavioral proofs, extra validation runs to
test these properties, or I/O enforcement machinery. Violations are plugin bugs;
the possibility of inconsistent execution or incorrect cache reuse is accepted.
This contract supports the accepted worker revalidation flow below.

**Accepted constraint — effective-value handoff.** The API validates parameters
through Pydantic, applies defaults, and queues all effective values using the
accepted serialization contract. The worker reconstructs the model through
ordinary Pydantic validation. Authors guarantee that normalized values remain
unchanged after JSON transport and revalidation. Defaulted fields are already
present in the queued representation, so the worker does not independently choose
their defaults. Cache keys, execution, and reports use the same effective values.
No custom restoration machinery or additional validator-behavior checks are
required.

**Accepted constraint — effective parameter reporting.** Record complete effective
values for each resolved node use without supplied-field masks, default-origin
metadata, or separate origins for nested fields and list elements. Preserve named
dependency and parameter bindings in execution and in the report's selected graph
description. For A's `search_radius` forwarded to B's `radius`, then to C's
`distance`, retain the graph wiring and each node's effective values without
additional public source-parameter references or forwarding-chain traversal.

### R9. Execution reporting and automatic route selection

**Resolved — normal routing and reports for every managed graph.** Affects items
1, 8, 10, 14, and 18.

Select routes through existing shortest-route rules, explicit tie preferences,
and ambiguity handling. Reporting does not filter candidates, favor a longer
route, alter preferences, or resolve ambiguity. The report describes the selected
graph and its observed execution/cache outcomes.

Location-scoped, unpartitioned, and hybrid graphs all have execution reports.
No complete-trace eligibility assessment is needed. Missing or incompatible
`location_id`/`row_key` Pandera declarations on location-scoped metrics remain
invalid definitions; ambiguity
and other plan errors retain normal failure behavior. Clients submit one request
type with no reporting mode or retention switch. R10 fixes durable report access
and success publication; R2 preserves values-only cache reuse.

### R10. Durable execution-report access and result publication

**Resolved — accepted execution reports, persistent local storage, and durable
success.** Affects items 1, 4, 5, 6, 8, 9, 10, 11, 12, 13, 14, 16, 17, 18,
21, 22, and 24, and the explanation scope of R1–R9.

**Accepted report and access.** The first explanation feature is an execution
report describing the normalized request, selected graph and named bindings,
effective parameters, declared sources and revisions, observed node execution,
cache-reuse/skipped outcomes, and final status. Include mixed execution/reuse
outcomes without a durable per-finding or per-partition event history. The report
distinguishes declared sources from actual execution and does not claim fresh
acquisition for cached or skipped work.

Run/result interfaces expose a durable execution ID alongside ordinary outputs.
Shared read-only lookup logic retrieves the report by that ID for Python, HTTP,
and MCP access. Item 13 defines bounded inspection: return the complete report
inline when it fits, or an explicitly partial preview preserving the execution ID
and status, identifying omissions, and linking to the complete JSON download.
Bulk report downloads may stream beyond the inspection response-size limit.
Python callers can obtain the full report locally or through the remote download;
agents using MCP may download and process oversized reports through other
capabilities. MCP-only report pagination or chunk retrieval is not required.
The ID, report, and download access remain usable after transient RQ/job records
expire within the supported current store. Result tables remain ordinary
pandas/GeoPandas tables without mandatory reporting columns. Exact return shapes,
reference encoding, method names, and physical report schema remain implementation
choices within shared contracts.

Reports apply to all managed graphs, including unpartitioned and hybrid graphs.
Empty results and zero-location requests still have an execution ID and report,
without fabricated rows, partitions, or handler invocations. Public finding/group
trace references, empty-group inspection, intermediate member listing, and
navigable parameter-origin or input-selection history are deferred. Do not retain
additional intermediate identities, membership, or mappings solely for those
features. Preserve everything needed for execution, caching, and ordinary retrieval.

**Accepted local storage.** Managed local runs, including development runs, use
the same SQLite storage and report machinery as API/worker execution. Provide a
persistent default store with a path configurable through normal runtime
configuration. Lyra creates and initializes it as needed; authors do not create
its tables manually. Local result access identifies the associated store so a
caller can reopen it later and resolve the execution ID. Default paths and exact
configuration spelling remain implementation details. The existing current-store
scope applies: incompatible schema/code changes need no migration or historical
reader, and an explicit development-store reset remains permitted.

**Accepted durable success and failure behavior.** Before returning local success
or marking a remote execution successful, persist all required final result values,
their retrieval descriptions, and the complete execution report. Publish the final
result description and report together transactionally in SQLite after required
values are durable. Only then expose successful completion through the run/job
interface. Do not expose a partially written report as a successful execution's
final report. Bounded previews under item 13 never truncate the retained report
or relax this publication requirement. No separate whole-graph revalidation pass
is required.

Required result or report persistence failure fails the execution through normal
error handling, even if the numerical calculation completed. Do not return a
successful result with an unavailable report as a fallback. This rule applies to
all output scopes. It guarantees reports for successful executions; it does not
require a complete report for every failed or interrupted attempt, particularly
when storage itself is unavailable. Failure status remains distinct from a
successfully completed empty result.

Execution need not hold a database transaction open while handlers run. Complete
cache values may be committed earlier under the existing atomic value-publication
rules and remain reusable if later report persistence fails. Such values do not
constitute publication of a successful run. Stored values and current execution
reports remain independently owned; no historical producer link or cross-service
transaction with the queue is required.

**Resolution.** These rules settle R10 and the initial explanation scope. The
execution-report contract replaces finding-level inspection commitments while
preserving partitioning, stable identities, dependency resolution, operation
forwarding, metric/operation cache strategies, boolean provider caching, output
validation, empty-result completion, and ordinary result storage and retrieval.
More detailed provenance requires a demonstrated workflow before
implementation.
