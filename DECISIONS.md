# Automatic traceability implementation ledger

Lyra should support automatically recorded provenance for the current request's
declared computation and execution. Traces identify logical nodes, effective
parameters, named input bindings, output rows and groups, location ownership,
and returned operation relationships. They distinguish executed handlers, cache
hits, and skipped work. Cached values carry no historical producing-invocation
requirement. Exact contributing rows, explanations of rejected candidates, and
proof of exhaustive selection are outside the initial guarantee. Lyra automatically determines trace eligibility
from the selected execution graph and retains provenance for eligible executions.
Graphs containing unpartitioned metrics return calculation results without a
complete trace. Clients submit one request type and receive explicit
trace-availability information. This ledger records the first implementation's
accepted direction, working contract, and remaining decisions; it does not describe
an implemented capability.

Keep the implementation small and place responsibility for accurate declarations
on plugin authors. Closed-alpha status permits breaking changes without migration
or compatibility machinery. There are no existing users or deployments to
preserve. Legacy-result readers and historical-result compatibility across
incompatible code or schema changes are outside scope; a development-store reset
is acceptable. Retention and trace inspection within the supported current store
remain part of the contract.

All executable nodes and data sources are assumed deterministic over frozen,
immutable inputs. The same computation key identifies the same logical output
values and membership, including empty results. Authors represent every
output-affecting change through the declared identities or effective parameters
used in that key. This guarantee applies to every node kind, output scope, and
caching strategy; physical row order remains insignificant.

1. **Use one runtime and derive trace eligibility from declarations.**

   **Accepted.** Node kinds define the provider, metric, and location-operation
   interfaces. Metrics declare one output scope: location scoped or unpartitioned.
   Every location-scoped metric declares location ownership and stable row identity
   for every named output under item 3. Unpartitioned metrics return ordinary
   named tables without mandatory ownership or row keys. Providers and operations
   retain their location-record and correspondence identities. All use the same
   dependency resolution, executor, resource lifecycle, validation, storage, and
   transports, with ordinary return contracts for their node kinds.

   Lyra records current-execution provenance from validated outputs, declarations,
   and execution metadata under item 4. All authors declare graph dependencies and
   external origins under item 2, including for unpartitioned metrics and caching
   off. Authors own the accuracy of those declarations; Lyra does not inspect
   handler code to prove compliance. Output scope, identities, and external-origin
   metadata determine the supported capabilities and graph eligibility.

   **Accepted automatic trace eligibility.** Clients submit one request type.
   Lyra selects the execution graph using normal routing and checks its complete
   dependency graph, including location operations, before resource acquisition or
   handler execution. Validate required ownership, identity, and external-origin
   declarations at the shared boundaries. A valid selected graph is structurally
   eligible for a complete trace when every reachable metric is location scoped.
   Providers and operations have their required identities by their node-kind
   contracts. External-origin declarations preserve traversal of upstream graph
   dependencies. Eligibility includes nodes later skipped by cache hits and relies
   on the author's declaration contract, not behavioral proof.

   An unpartitioned metric makes the selected graph ineligible for a complete trace
   but does not prevent calculation. Execute otherwise valid graphs and return
   their results with trace unavailable and a reason identifying that metric and
   its path. Missing required ownership or row-key declarations on a location-scoped
   metric are invalid definitions, not a reduced tracing capability. Execution-plan
   errors and invalid declarations retain their normal failure behavior; do not
   silently change a metric's output scope.

   **Accepted automatic retention and availability.** Eligible executions retain
   current-execution provenance automatically, even when the caller only needs result
   values. The metadata storage cost is accepted. Result interfaces report whether
   a complete trace is available and give a reason when it is unavailable. Report
   a trace as available only when the required current-execution metadata is durably
   retained and resolvable. Structural eligibility alone is not an availability
   guarantee. Cache hits are fully represented by the reuse records accepted in
   R2; their historical producing executions are outside the trace guarantee.
   Publication and persistence-failure behavior remain under R10.

   The initial request contract has no client-selected tracing mode, retention
   switch, or require-a-trace-or-fail option. Trace completeness means resolvable
   current-request context within the accepted source boundaries; it does not require
   exact row-contribution information.

2. **Declare external origins on ordinary node definitions.**

   **Accepted external-origin metadata.** Metrics and operations declare a collection
   of descriptors for external data they acquire directly, beyond their declared
   graph inputs. Each descriptor has a required stable origin `id`, an optional
   opaque `revision`, and an optional plain-text `description`. An origin need not
   be a registered location dataset. Authors do not repeat origins already exposed
   through declared upstream dependencies. An empty collection is the author's
   promise that the handler acquires no additional external data.

   These declarations are required whenever direct external acquisition occurs,
   including in unpartitioned metrics and with caching off. Authors own complete
   and accurate dependency and origin declarations. Undeclared external acquisition
   is a plugin-contract violation; Lyra does not inspect code or enforce I/O behavior
   to discover it. Internal queries, file reads, and processing remain outside the
   trace guarantee. Connection details, query text, file hashes, and explanations
   of internal processing are not required origin metadata.

   **Accepted provider origins.** Dataset registration automatically establishes
   the provider's external boundary. Derive its origin ID, revision, and description
   from the requested dataset registration, without another declaration or origin
   registry. A provider serving several datasets uses the requested dataset's
   metadata. Keep the provider's node ID and code revision as its implementation
   identity, separately from dataset and location-data identity. Provider
   declarations participate in graph validation and eligibility before resources
   are acquired.

   Origin IDs and revisions participate in computation identity under item 9;
   descriptions are retained for discovery and eligible trace inspection but do not
   affect cache keys. Provider origin identity is already represented by its dataset
   ID and revision. `revision=None` identifies fixed, unversioned input, not changing
   latest data. Changed external contents require a changed declared identity or
   effective key input under the deterministic contract. Tracing neither archives
   external data nor guarantees its future availability. Exact declaration spelling
   remains an implementation choice.

   **Accepted acquisition and graph context.** For eligible executions, Lyra records
   actual node invocations with their declared external origins and ordinary inputs
   and outputs. Record provider acquisition when its handler executes. A cache hit
   records current reuse and declared origin metadata without claiming acquisition;
   an upstream node bypassed by a hit remains skipped with its declared context.
   Do not invent invocations, acquired tables, or acquisition events for skipped
   work. Unpartitioned metrics still declare origins but do not become eligible
   for a complete trace by doing so.

   External boundaries preserve dependencies inside Lyra. A loader receiving a
   computed location selection retains that selection's current-request context
   separately from the retrieved records. Include all upstream graph dependencies
   when determining eligibility; an origin declaration cannot conceal an
   unpartitioned upstream metric. Named bindings and actual execution or reuse
   describe current-request context, without asserting exact contributing rows or
   tracing internal acquisition and processing.

   Dataset providers follow the narrower lookup contract in item 20. An upstream
   computation may select the IDs to retrieve; retain that selection's provenance
   separately from the identities of the requested records. A hospital record's
   contents do not depend on the building or radius that caused it to be selected.
   The selection link does not introduce arbitrary calculation parameters or
   upstream-table arguments into the provider interface. Metrics and operations
   acquiring external data retain their declared calculation inputs.

   **Accepted caller-input scope for the initial implementation.** All managed
   local and remote executions, including `run_development`, accept parameters
   and location selections identifying registered datasets and requested IDs.
   Location geometry and attributes enter through registered dataset providers,
   following the accepted execution, cache-reuse, and skipped-acquisition rules.
   Handlers receive validated `Locations` internally. Table dependencies are
   supplied by Lyra through execution or cache reuse of declared upstream nodes.
   External tabular data enters through declared nodes with external-origin
   metadata for their direct acquisitions.

   **Accepted development roots and shared execution.** `run()` selects public
   metrics. `run_development()` selects concrete metrics, including private
   metrics, while accepting the same location-selection form. Development root
   selection permits private roots before shared request preparation. Both paths
   share selection normalization, parameter and plan validation, provider
   acquisition, and the executor's dependency resolution, result validation,
   caching, and provenance machinery. Public metric discovery and remote root
   submission retain their public-metric restrictions. Declared dependencies
   retain their ordinary execution behavior.

   Direct injection of full location records, arbitrary DataFrames, and
   caller-supplied stored-result references as table-dependency replacements is
   outside the initial managed-execution contract. Direct Python calls to handler
   functions remain outside managed graph execution, caching, and automatic
   provenance. R5 records the accepted provider-acquisition and external-origin
   contract within this scope; caller-selected IDs remain part of the current
   request's input context.

   Authors decide where boundaries belong. Inspection displays source boundaries
   clearly and follows the current request's node contexts, named input bindings,
   and available result references through the declared graph. It distinguishes
   actual execution from reuse or skipped work without claiming to identify every
   contribution or the historical execution that populated a cache entry.

3. **Give outputs explicit scope, identity, and location ownership.**

   **Accepted partition-scoped metric row identity.** Every named output of every
   location-scoped metric declares location ownership and a stable string row-key
   column; the handler supplies its values. The key is non-null and unique within one owning
   location partition of one named output. Missing ownership or row-key declarations
   make the definition invalid, regardless of caching strategy or graph eligibility.
   The row's identity within that output combines the qualified partition
   `(dataset_id, revision, location_id)` with the author-declared key. Different
   partitions and different named outputs may reuse the same author key.

   For example, a findings output may contain `(building A, "roof")`,
   `(building A, "entrance")`, and `(building B, "roof")`. Two rows with
   `(building A, "roof")` in that output are invalid. Authors keep keys stable
   for the same logical rows when input order changes or independently computable
   partitions execute in different batches. DataFrame indexes, incidental row
   positions, and execution-batch membership do not establish row identity.

   Downstream handlers address a location-scoped metric input row by its qualified
   partition and author key within the named input. Assembly preserves those
   identities within the current result/table and named-output scope. A durable
   trace reference associates the row with the current execution's node context
   and records whether its group was computed or reused. Cached data requires no
   link to its historical producing invocation. A reuse record does not claim
   that the handler executed in the current request.
   Operation rows use the qualified input/target pairs in item 4. These identity
   and assembly rules resolve R1. Metric-row keys use strings under R7; exact
   declaration spelling and reference syntax are implementation choices
   under the shared-contract ownership rule in item 22.

   **Accepted unordered collections and explicit identity.** Location selections,
   operation target groups, partition groups, and table rows have no semantic
   ordering. Lyra makes no developer-facing guarantee about input or result row
   order, target-tuple order, dictionary iteration order, or column positions.
   Associate records through qualified location IDs, operation input/target pairs,
   and declared metric-row identities; address inputs, outputs, and columns by
   name. Cache lookup, forwarding, assembly, and provenance must not match records by
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
   partition key, independently of caching strategy. A partition
   is identified by `(dataset_id, revision, location_id)`. Select a particular named location input
   as the partitioning input, not merely a location type; two inputs can have the
   same type or dataset while serving different roles.

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
   storage and retrieval under items 11 and 14, and no complete row/group trace
   under item 1. Location-independent row and group tracing is outside the initial
   implementation. This scope changes no declared-input, source-boundary, or
   deterministic-result obligations. Exact declaration spelling remains an
   implementation choice.

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
   metric declares exactly one scalar owner column associated with the metric's
   selected primary location input, such as `building_id` with `buildings`. Every
   returned row has a non-null string owner ID belonging to the locations actually
   supplied to that invocation. The named input supplies the dataset and revision
   scope. Returning an owner from elsewhere in the dataset or request is invalid
   when it was not supplied to that invocation.

   Multiple rows may share an owner; their declared row keys distinguish them.
   A supplied owner may produce zero rows in any named output. Validate owner
   membership and non-nullness at the shared producer boundary, without an
   additional provider lookup. Multiple inputs and multiple named outputs remain
   supported. Exact declaration spelling is an implementation choice.

   Columns other than the owner and row-key columns are ordinary data, including
   columns containing IDs of other locations. Apply their declared schema checks without interpreting them
   as location references, acquiring records, or inferring dependencies. They are
   stored, returned, and forwarded as ordinary values. Named input dependencies,
   their selection rules, and operation correspondences retain their execution
   and cache-identity responsibilities. Metric trace inspection covers ownership,
   row identity, current node context, and named inputs. These rules resolve R6.

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

   For other inputs of a location-scoped consumer whose relationship to the primary
   partition is unclear, require an explicit shared binding or a declared correspondence. Matching
   argument names, location types, or IDs alone does not establish that
   correspondence. Authors may explicitly choose shared selection to override
   partition-following behavior when broader context is needed.

   Extend the existing input declarations with an optional selection setting;
   `selection="partition"` and `selection="shared"` are illustrative spellings,
   not a finalized API. This behavior applies across `off`, `full`, and
   `per_location`. Selecting the rows a consumer receives does not establish that
   an upstream producer can compute only those rows. Apply the accepted upstream
   execution rules in item 9 and R4 when cache hits reduce downstream work.

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
   Lyra records these together with their current execution or reuse context.
   Neither ownership, an operation correspondence, nor a node input reference
   asserts the exact rows that contributed to a value.

4. **Record current-execution provenance automatically from ordinary results.**

   **Accepted metric return contract.** Metrics return their ordinary named output
   tables. Location-scoped metric outputs declare row keys and location ownership
   in their schemas under item 3. Lyra records each identified row's qualified
   partition and author key, named output, and current execution context. It also
   records successful completion for every supplied primary partition of a
   location-scoped metric in every named output, including groups with no rows.
   Authors provide no separate
   dependency list, row/group lineage payload, or provenance wrapper.

   A metric handler must return every declared named output and no undeclared
   output names, for both location-scoped and unpartitioned metrics. Return a
   schema-valid empty table when an output has no rows. Missing a named output is
   invalid, not successful empty completion. Column acceptance remains governed
   by each Pandera schema. Operation mappings retain their separate rule that
   omitted supplied location keys normalize to empty target groups.

   For example, a defect metric receives buildings A and B with severity threshold
   3. Its ordinary `defects` output contains only
   `{"building_id": "B", "finding_id": "roof", "severity": 4}`. Lyra records
   B's identified row and A's successful empty group. Both refer to the current
   node context, which records the threshold once. A schema-valid empty table
   records successful empty groups for every supplied partition; it requires no
   additional author declarations. These completion rules apply only after a
   successful return and validation, not to failed or unprocessed work.

   **Accepted unpartitioned completion.** A successful unpartitioned metric return
   completes every declared named output for the whole computation. Schema-valid
   empty tables are completed outputs, not unprocessed work. Empty bound inputs do
   not imply empty outputs: a summary may return a row containing a count of zero.
   When its computation is needed, execute the handler on those inputs unless a
   permitted full-result cache hit supplies the result. Do not fabricate location
   groups or row identities. These completion and retrieval rules do not make an
   unpartitioned metric eligible for a complete trace.

   **Accepted current execution context.** Record logical implementation and
   computation identities, all effective parameters including resolved defaults,
   parameter bindings, and named input bindings for the current request. Store
   this context once for each node use, with partition scope where needed. Each
   retained trace describes its own request, including when inspected later. Record
   which output groups were computed and which were reused, and mark bypassed
   upstream work as skipped. Actual handler invocations and supplied inputs are
   recorded only when they occur. Declared bindings and dataset identities can
   remain inspectable when their nodes were skipped, without fabricated result
   tables, acquisition events, or handler invocations.

   Current-result references associate rows and groups with that execution or
   reuse context; cache payloads contain no historical invocation or provenance
   requirement. Cache hits record the current node, effective parameters, input
   identities and bindings, and retrieved output scope. They do not restore the
   execution that originally populated the cache. Current build metadata describes
   the current deployment, not the unknown build of a historical cache producer.

   A batched handler invocation retains the inputs actually supplied to it.
   Shared upstream work that executes for another consumer retains its actual
   execution status; only work genuinely bypassed is marked skipped. Sharing a
   physical execution batch does not make location partitions depend on
   one another or change their logical computation identity. Assembly preserves
   row identities and each group's computed-or-reused outcome in this request.
   Keep current operation-selection relationships separate from location-data
   identity. Input bindings are execution context, not exact row contributions.

   **Accepted membership and empty-result scope.** Retain output membership and
   outcome information with current node contexts, effective parameters, and
   named input bindings. Consumers and inspection can follow a supplied input
   to its current-result groups even when those groups contain no rows. The trace
   shows which logical computation supplied the observed membership and whether
   those values were computed or reused;
   it does not explain every excluded record or prove exhaustive selection.
   This contract applies to metrics and operations and resolves R3.

   **Accepted operation return contract.** The Python authoring representation of
   an operation's correspondence output is
   `dict[LocationID, tuple[LocationID, ...]]`: each key is an input location ID and
   its value is a tuple containing the corresponding target location IDs.
   `LocationID` is `str`, so the concrete mapping type is
   `dict[str, tuple[str, ...]]`. The tuple is a Python container for an unordered
   target group; each individual target ID is a string. Operation handlers return
   this mapping directly regardless of graph trace eligibility. Authors supply no
   separate row-lineage payload, group-dependency declarations, or provenance
   wrapper for operations.

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

   **Accepted automatic operation provenance.** For each returned correspondence
   and completed empty group, Lyra records its qualified input location, named
   output, current operation context and computation identity, effective parameters,
   and returned target identities. Record whether the mapping was computed or
   reused. Preserve declared external-origin metadata and actual provider acquisition
   records when acquisition occurs in this request. Retain current parameter
   bindings and selection relationships. The mapping and current execution
   metadata supply this information;
   authors do not duplicate it as dependency records.

   For `{"A": (), "B": ("H1",)}`, inspection records that A completed with no
   targets and B returned H1, together with the current operation's effective
   parameters and whether each group was computed or reused. It does not explain which candidates were rejected or
   establish that the search was exhaustive. Operation tracing requires neither
   dependency edges to unselected candidates nor recording those candidates
   solely to explain selection. It also does not conservatively attach every
   supplied row as a dependency of every operation group.

   Declared execution inputs, resolved binding descriptions, graph eligibility,
   and computation-key input identities retain their existing contracts. Cache
   identity accounts for all declared inputs even though the operation trace has
   the narrower relationship and current-execution scope. Records required by
   caching or other retained results remain governed by those rules.

   Require unique target IDs within each tuple; duplicates are a structural error.
   The same target ID may appear under several different input keys. Target-tuple
   permutations and dictionary insertion order have no semantic significance.
   Assemble operation groups by their explicit input and target IDs.

   Target IDs refer to the operation's declared target dataset and revision.
   Every target must be an existing location resolvable through that dataset's
   provider. The operation returns IDs, and Lyra obtains geometry and attributes
   from the provider when needed. Retain the correspondence separately from target
   records, with automatic operation provenance and provider source provenance.

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
   Reuse records the mapping's use with the current request's correspondences and
   selection relationships, without following a historical cache producer.

   **Accepted target resolution failures.** An unresolved target produces an error
   during normal acquisition. Do not silently drop it or treat it as a successful
   zero-match result. No separate preflight existence lookup is required.

   **Accepted mapping encoding.** The JSON payload is an object with string keys
   and arrays of target strings. Pydantic's declared mapping type restores the
   tuples. Persist correspondences as ordinary SQLite rows with string input-ID
   and target-ID columns, qualified by their dataset/revision and result scopes.
   Preserve the partition/output scope even when it has no correspondence rows.
   A published cache entry with zero pairs is a reusable empty result; absence of
   an entry is a cache miss. A separate completion object is not required. The tuple/JSON interface
   does not require list-valued SQL cells or serialized table blobs. Target
   collection order has no semantic significance. Use standard library and
   database representations without a custom location-ID codec. Operation row
   references carry separate input and target IDs, qualified by their
   dataset/revision and current result/table and output scopes. Trace references
   associate them with the current execution or reuse context. Assembly preserves
   these fields and both ends of the pair. Exact reference syntax follows the
   shared contracts in item 22. The cache representation needs no invocation field.

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

   These correspondences and their current execution or reuse context are the supported operation
   trace. Authors supply the correspondence mapping; Lyra does not infer the
   mapping from equal IDs or geometry. Every named output participates, including
   its completed empty groups.

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

5. **Record effective parameters and trust author contracts.**

   **Accepted.** Record all effective parameter values once per current node
   context, together with named input bindings and actual parameter-forwarding
   relationships. Every computed or reused output group can reach that context.
   Effective parameters, including defaults, also participate in computation keys.
   The initial implementation does not distinguish
   explicitly supplied values from defaults or record supplied/default origin
   flags. Parameters are recorded as execution context without attributing exact
   influence on individual output values.

   **Accepted parameter-binding provenance.** For a forwarded named parameter,
   retain a reference to the immediate source node context in the current execution
   and its parameter name, associated with the receiving node context and parameter
   name. Follow these
   references during inspection to recover a longer forwarding chain; do not
   duplicate an ultimate-source reference on each parameter. References identify
   specific current-request node uses rather than node names alone. These contexts
   exist for executed and cache-hit nodes; an actual handler invocation is recorded
   only when the handler runs.

   For example, if A's `search_radius` is forwarded to B's `radius`, and B's
   `radius` is forwarded to C's `distance`, B records its immediate binding from
   A and C records its immediate binding from B. Lyra captures these references
   from the bindings it resolves, without additional author declarations.

   Treat each named parameter as one value, including a nested object or list.
   Retain its complete effective value without separate provenance for nested
   fields or list elements. These references describe actual forwarding; they do
   not introduce explicit-versus-default origin tracking or fine-grained
   influence tracking within validators.

   Authors own the correctness and determinism of their results, stable identity
   declarations, partition-independence promises, and declared source boundaries.
   Lyra records current-execution provenance automatically. Schema checks enforce declared structure,
   and graph checks determine trace eligibility. Lyra does not inspect handler code, prove
   independence, or identify every actual contributor. Fine-grained row, column,
   and parameter influence tracking is outside the initial implementation.

6. **Give each validation step one owner.**

   **Accepted.** Validate each producer result once at a shared boundary. Include
   output tables or operation mappings and, where applicable, declared row keys
   and location ownership. Validate and normalize operation mappings at this
   same boundary. Construct automatic current-execution provenance from validated outputs,
   schema declarations, and resolved execution metadata. Pass owned validated data
   with its matching storage schema to the writer. Publication consumes this
   result without repeating producer validation. Authors remain responsible for
   result correctness and their identity, partitioning, and source-boundary promises.

   Reject references to unknown input or output names in definitions and resolved
   bindings during definition or plan validation, before resource acquisition.
   At the shared producer boundary, require a metric's returned named outputs to
   match its declarations exactly. Reject missing or unexpected output names;
   require explicit schema-valid empty tables for empty metric outputs. This rule
   applies to output-table names; column validation follows the declared schemas.

   Require ownership and row-key declarations for every named output of a
   location-scoped metric at definition validation, before resource acquisition.
   Missing declarations are invalid definitions. At the shared producer boundary,
   require a non-null owner ID from the selected primary location input actually
   supplied to that invocation, using that input's dataset and revision scope.
   Check membership without another provider lookup. Validate non-null string row keys
   unique within each owning partition and named output. Other output columns,
   including location-looking IDs, receive ordinary declared schema validation.
   Apply these requirements even when another node makes the selected graph
   ineligible for tracing; do not downgrade a definition
   to unpartitioned scope or use runtime row numbers as its semantic keys.

   Validate external-origin descriptor structure through the shared declaration
   boundary, including provider metadata derived from dataset registrations.
   Authors own disclosure of direct external acquisition; validation does not
   inspect handlers to discover omitted origins.

   Unpartitioned metric outputs use this same producer boundary for ordinary
   named-output, schema, and supported-type validation, without mandatory
   location-ownership or stable-row-key checks. Validate output scope during plan
   preparation: reject `per_location` caching for an unpartitioned metric and
   partition-selection bindings on its outputs before resource acquisition.

   **Accepted Pandera and SQLite schema contract.** Authors continue declaring
   outputs through Pandera `DataFrameSchema` or `DataFrameModel`, including the
   existing location-schema wrapper. Derive the storage description from that
   declaration; authors do not provide a second SQL schema. Compile it once per
   registered schema, rejecting unsupported types before execution. Registration
   builds descriptions without database access. Database table creation and data
   writes belong to runtime storage initialization or execution.

   The initial table-column contract is deliberately small:

   | Logical column type | SQLite representation | Reconstruction |
   | --- | --- | --- |
   | String | `TEXT` | pandas string |
   | Signed 64-bit integer | `INTEGER`, from `-2**63` through `2**63 - 1` | pandas nullable integer |
   | Finite 64-bit float | `REAL` | pandas nullable float |
   | Boolean | Integer `0` or `1`, handled by SQLAlchemy `Boolean` | pandas nullable boolean |
   | Geometry | Standard WKB in a `BLOB` column | GeoPandas geometry through standard WKB conversion |

   Nullability remains part of each column declaration. Missing values map to SQL
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

   Give the executor sole ownership of execution-plan validation and automatic
   trace-eligibility classification before resource acquisition. Classification
   records ineligibility and its reason without rejecting an otherwise valid
   calculation. Remove the worker's duplicate plan-validation call and translate
   executor failures at the worker boundary. Independent entry points, including
   cache reads and public interfaces accepting arbitrary data, still validate
   their inputs.

   **Accepted provenance integrity boundaries.** Lyra constructs execution records
   and references directly from the resolved plan, actual execution/cache outcomes,
   and validated outputs. Authors supply ordinary outputs, not provenance references.
   Keep each current-execution reference within its intended request, node use,
   named output or parameter, owning partition, and row/group scope, as applicable.
   An invocation reference identifies work actually executed. Shared stored-value
   references may identify reused computations without a historical producer link.

   Enforce these relationships through ordinary construction invariants, scoped
   primary and foreign keys where applicable, and transactional writes of related
   records. Verify construction, storage, and retrieval behavior with ordinary
   isolated tests. Use the same validated objects throughout; do not add a separate
   provenance revalidation subsystem or repeat producer-table validation in the
   metadata writer or inspector. Public inspection inputs retain their shared
   parsing and lookup checks under item 13. These guarantees establish structural
   consistency; authors still own result correctness, key stability, partition
   independence, and complete dependency/origin declarations.

   Verify that mutation and alternate call paths cannot bypass required checks.
   This boundary applies to every node kind and output scope as appropriate;
   recording provenance must not create parallel validation paths in adapters, execution,
   and storage. Internal representation and error propagation are implementation
   details within this scope.

   [Producer validation](src/lyra/sdk/results.py),
   [storage preparation](src/lyra/sdk/storage_schema.py),
   [worker execution](src/lyra/server/worker.py).

7. **Use entry points for plugin discovery and Pydantic for parameters.**

   **Accepted.** Discover explicitly enabled installed plugins through standard
   Python entry points and load their definitions in API and worker processes.
   Live definitions carry node kinds, output scopes, external-origin metadata,
   required row-key declarations, and location ownership in the shared catalog.
   Let Pydantic validate parameters and apply defaults, generating JSON Schema for
   discovery. Remove mandatory generated
   plugin manifests, their synchronization machinery, parallel JSON Schema
   validation, and custom default handling. Any retained manifest export is an
   optional inspection tool.

   Relax restrictions imposed solely by parallel validation while preserving a
   usable JSON interface and discovery schema. Capture effective parameter values
   during normalization for queued requests and trace records, without tracking
   whether they were explicitly supplied or defaulted. Persist descriptions
   needed for inspection within the supported current store.

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
   not retained as provenance. Actual parameter-binding relationships remain
   supported through the immediate references specified in item 5 and R8.

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
   Trace eligibility is derived from the selected graph, not a queued client
   tracing preference. Include defaulted values and null fields in the
   serialized parameter representation; do not carry supplied-field masks or
   explicit-versus-default origin flags.

   The worker reconstructs the parameter model through ordinary Pydantic
   validation of those queued effective values. Under the accepted author
   contract, validation after JSON transport preserves the effective values.
   Defaults already resolved by the API arrive as explicit values; the worker
   does not independently select defaults for those fields. Cache keys, handler
   execution, and traces use the same effective parameter values. Trust the
   validator contract without additional behavioral checks or a custom model
   restoration mechanism.

   For example, omitting a parameter whose default is `radius=100` queues
   `radius=100`, just as explicitly supplying that value would. Execution and
   inspection retain the value without distinguishing those two cases. Actual
   named input references and parameter-binding relationships remain part of
   provenance.

   Reject a build mismatch before resource acquisition or handler execution,
   using the existing terminal failure status with a clear `deployment_changed`
   reason. Callers can resubmit. Prepare compatible jobs against the worker's
   catalog. The executor validates the plan and determines trace eligibility
   before execution; an unpartitioned metric is recorded as a reason for unavailable
   tracing, not a job-rejection reason.
   The build revision covers executable code, routing policy, and declarations
   affecting traceability. The public discovery fingerprint serves a different
   purpose. Build identification and compatibility checks remain implementation
   details. The deployment build revision serves job compatibility and provenance;
   computation-cache implementation identity uses the author-managed code revision
   specified below.

   Persist the normalized request, execution identity, outcome, and output
   descriptions, including the selected node, route, relevant revisions, and
   trace availability with a reason when unavailable. Eligible executions
   automatically retain current node contexts and computation identities,
   current build and declared code revisions, node kinds and output scopes, input
   and parameter bindings,
   effective parameter values, output row identities and location ownership,
   automatic metric and operation execution/reuse records, and declared
   external-origin metadata. This includes empty groups under item 4. Store current input bindings
   and any acquired result references once per node context, without per-output-row
   contribution assertions or author-supplied dependency relations.
   Retain parameter-binding relationships as immediate current-node-context and
   parameter references when resolved, following item 5 and R8. Record actual
   invocations only for handlers that execute, and distinguish skipped inputs from
   acquired inputs. Cache-hit records require no historical producer metadata.
   No separate explicit-versus-default classification is required.

   Store these descriptions independently of queued jobs so current-store trace
   inspection survives RQ expiry. Persist descriptive metadata needed for ordinary
   retrieval and trace inspection. Backward compatibility and historical-result
   handling across incompatible plugin or storage changes are not required in
   closed alpha. Do not add executable contract snapshots, legacy readers, or
   deployment-synchronization machinery for that purpose.

   [Acceptance models](src/lyra/contracts/accepted.py),
   [plan preparation](src/lyra/runtime/planning/preparation.py),
   [job observations](src/lyra/server/job_store.py).

9. **Let authors select caching per node and reuse values by computation identity.**

   **Accepted.** Make caching available at intermediate and final nodes through
   three developer-selected strategies:

   | Strategy | Behavior |
   | --- | --- |
   | `off` (default) | Execute the complete input batch on each invocation. |
   | `full` | Reuse results for the complete bound selection when its computation key matches. |
   | `per_location` | Reuse matching locations, compute the missing subset, and assemble the requested result. |

   **Accepted unpartitioned caching.** An unpartitioned metric may use `off` or
   `full`, with `off` still the default. `per_location` is invalid for this scope.
   When invoked, its handler receives all initially bound input selections; cache
   hits downstream do not narrow those selections. A full cache entry contains all
   named output tables for that whole computation, including successful empty
   outputs. Reuse requires the complete computation key to match. A downstream
   hit may bypass the metric entirely under the ordinary node-local `off` rule.
   Determinism and declared-input and source-boundary obligations remain unchanged.

   **Accepted deterministic computation contract.** Metrics, location operations,
   and dataset providers produce the same logical values and output membership
   for the same computation key, including successful empty
   outputs. This applies across executions, batching permitted by the partition
   contract, and all caching strategies and output scopes. Meaningful time
   periods, source releases, implementation changes, and other output-affecting
   differences must be represented in the declared input or implementation
   identities or effective parameters included in the key. Dataset providers
   retain item 20's narrow lookup interface and frozen location-data identities.

   Authors own this guarantee. Different logical values under the same key are a
   contract violation, not another valid stored version. Lyra does not prove
   determinism or add output-content hashing or comparisons to establish it.
   Physical row and column order remain insignificant under item 3.

   **Accepted node-local meaning of `off`.** This strategy disables the node's
   own computation-cache reuse. The node executes whenever it is invoked, but a
   valid downstream cache hit may bypass that invocation entirely. Repeated
   execution with the same computation key must produce the same logical result.
   Changed observations require changed key inputs even when caching is off.
   `off` does not invalidate dependent cached results or change their identities.

   For location-scoped nodes, all caching strategies use the declared partition
   mapping. When executed, `off` and `full` handlers receive the complete bound
   inputs; a partition mapping alone does not establish independent computation.
   `per_location` uses the location keys
   and output ownership defined above. Each cache entry contains all results
   owned by one location across the node's named outputs, including an empty
   group when a location successfully produces no rows. Publish the cache entry
   only when all of its named outputs are complete. Entry presence distinguishes
   a reusable empty group from a cache miss; no per-row completion flag or separate
   completion object is required.
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

   **Accepted upstream execution scope.** Preserve each producer's initial input
   binding for the current request before cache hits reduce downstream work. That
   binding describes a particular input selection, not the entire dataset. When
   an upstream result is needed, only a producer declaring `per_location` may
   receive a reduced partition request based on the consumer's missing keys.
   `off` and `full` do not establish the required independence promise and retain
   their complete initially bound inputs.

   For example, a request covers A, B, and C, but the downstream result for A is
   a valid cache hit. The downstream handler needs partition-related findings for
   B and C from a producer initially bound to A, B, and C in this request:

   | Producer strategy | Upstream behavior |
   | --- | --- |
   | `off` | Execute with A, B, and C. |
   | `full` | Look up results for the complete A/B/C selection; execute all three on a miss. |
   | `per_location` | Request B and C, check the producer's own cache, and compute only its missing keys. |

   In each case, supply only the findings associated with B and C to that
   partition-related consumer input. Shared inputs retain their complete bound
   selections, including when a `per_location` producer executes a reduced batch.
   Apply the same rule at each upstream dependency; a downstream cache hit must
   not silently change the bound inputs of an `off` or `full` producer.

   A `per_location` producer requested for A/B/D with matching cached groups for
   A and B invokes its handler only for D. Its primary location table contains
   D's resolved record; dependency tables partitioned by the same location contain
   only D's groups, which may have zero or more rows. Other partition-related
   inputs follow their declared correspondences, and shared inputs remain complete.
   The runtime combines cached A/B with the newly computed D group so the consumer
   receives the complete requested A/B/D result. A `full` producer with only an
   A/B/C cache entry computes the complete A/B/D invocation on this miss; an `off`
   producer executes that complete invocation whenever needed.

   Authors choose the strategy and own the independence promise required by
   per-location reuse, including dependencies. Authors validate this independence;
   Lyra does not prove it or automatically select a safer strategy. Lookup,
   partitioning, and assembly complexity is accepted because it enables reuse
   across overlapping requests.
   Use the shared SQLite result store in item 11; caching policy does not
   determine retention of already stored results.

   **Accepted shared operation storage and caching.** Metrics and operations use
   the same cache lookup, publication, retention, and reuse machinery, with
   ordinary output rows or correspondence rows. An operation mapping is a stored
   result owned by the shared result layer. Cache lookup associates a computation
   key with a stored value reference; the current trace may reference that same
   mapping and record its use. Preserve mapping membership and empty groups, and avoid separate
   copies solely for cache and provenance use. Cache matching and eligibility
   still determine reuse; retaining a previous mapping is not sufficient by
   itself.

   Mapping storage supports caching independently of graph trace eligibility or
   trace retention. A mapping retained for inspection under `off` does not
   authorize that operation's own cache reuse: it still executes when invoked.
   A valid downstream cache hit may avoid invoking it. `full` reuses a matching
   complete bound selection's mapping, and `per_location` reuses matching input-location
   groups and computes missing groups.

   Each executable node may declare an optional opaque code revision, including
   metrics, operations, and provider handlers. Its implementation identity
   for caching is `(node_id, code_revision)`. This identifies the implementation;
   `(dataset_id, revision, location_id)` identifies a location record. A complete
   computation identity also includes the effective parameters, inputs, and
   declared external-origin identities.
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

   **Accepted upstream result identities.** Identify computed inputs through their
   producer's logical computation identity, the named output being consumed, and
   the selected partition groups where applicable. An unpartitioned output is
   referenced as a whole table under its complete computation identity, without
   a partition-group selection. Use the producer's caching strategy to determine
   the computation identity's scope:

   | Producer strategy | Computation identity referenced by the consumer |
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
   Current execution records have separate request and node-context identities;
   stored values require no historical producing-invocation reference.

   **Accepted computation-key contents.** Use one descriptor model and shared
   key-generation mechanism across executable node kinds, with fields populated
   according to their contracts. Every descriptor includes implementation identity
   `(node_id, code_revision)`, the declared output scope, and computation scope:
   a complete bound input selection for `full` or `off`, or one qualified location
   partition for `per_location`.
   Add the following node-specific components:

   | Node kind | Additional descriptor contents |
   | --- | --- |
   | Dataset provider | Dataset ID, dataset revision, and the unordered set of requested location IDs. |
   | Metric | Effective validated parameters, including defaults; identities of every named input; directly declared external-origin IDs and revisions; and the primary location input's name when location scoped. |
   | Location operation | The metric components, plus the declared target dataset ID and revision. |

   A dataset provider partitions by its own locations and has no primary location
   input argument. It accepts no arbitrary calculation parameters or upstream
   metric-table inputs. Its resource configuration and the provenance of selecting
   its requested IDs are separate from its computation key, as specified in
   item 20. For metric and operation nodes, input names distinguish their roles
   and participate in identity.

   Include each metric's and operation's directly declared external-origin IDs and
   revisions in its descriptor. The provider's dataset ID and revision already
   supply its derived origin identity; do not add an independently managed token.
   Upstream origin changes propagate through the producer computation keys used by
   dependents. Arrange origin identities consistently with other unordered identity
   collections. Descriptions are discovery and trace metadata, excluded from keys.

   Location-input identities include their dataset/revision scope and selected
   location IDs. Computed-input identities use the upstream logical computation
   references, named outputs, and selected groups specified above. Use compact
   upstream computation keys as references rather than embedding entire ancestor
   descriptors. Shared inputs contribute their complete bound selection. A
   per-location descriptor covers its one primary partition and applicable inputs
   plus complete shared inputs; a full or uncached computation descriptor covers
   the complete selection initially bound for the current request.

   An operation's resulting target IDs are outputs, not descriptor prerequisites;
   its declared target dataset/revision is known before execution. One descriptor
   model serves all node kinds and output scopes; trace eligibility remains a
   separate check. An `off` node has this logical descriptor without an entry in
   its own computation cache.

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
   encode the complete descriptor as deterministic JSON with sorted object keys,
   fixed compact encoding options, and non-finite numbers rejected. Hash the
   UTF-8 encoding with SHA-256 to obtain the cache key. Reuse Python's standard
   JSON and hashing libraries; no additional serialization framework is needed.
   Unsupported values fail rather than using a string/repr fallback. This hashes
   the identity descriptor, not location or output contents.

   Cache entries are logical units. A per-location entry contains a complete group
   across all named outputs, including successful empty groups, rather than a
   single result row. This partition result is a logical grouping, not a blob or
   a database row. Several partition results may be produced by one batched
   handler call and persisted together. A `per_location` key references one
   complete partition result; a `full` key references the complete bound selection's
   collection of partition results or, for an unpartitioned metric, its whole named
   output tables. Distinct `full` selections such as A/B/C and
   A/B/D have separate cache entries; computing one does not overwrite the other,
   and storing their rows by partition does not enable subset cache matching for
   `full`. Item 11 defines the accepted relational layout, resolving R4.

   **Accepted values-only cache.** Cache entries map logical computation keys to
   result values and the schema, named-output, output-scope, and applicable partition
   and row-identity information required to retrieve them. Keys account for the
   accepted effective parameters, defaults, implementation revisions, input
   identities, and external-origin IDs and revisions. Neither
   matching nor reuse depends on a historical invocation, calculation time, or
   provenance record. Cache entries and cached value records carry no required
   producing-invocation link, historical trace, or trace-completeness flag.

   **Accepted SQLite storage.** Use the computation key directly as the stored
   result's identity. Item 11 defines a result header, any location partitions,
   and typed tables for its named outputs. Publish the header, applicable partition
   records, and all named-output rows together in a transaction after successful computation
   and validation. A published header identifies a complete stored result, even
   with no output rows or no locations. Absence of the header is a cache miss;
   reuse of an existing result also requires the node's caching strategy to allow
   it. A logical cache entry is this reusable stored result, not a separate
   key-to-storage-ID mapping. Its header and declared output scope represent
   completion without a separate completion object or per-row completion flag.

   Persist one logical value per computation key. If another successful execution
   of that key needs persistence, retain the existing immutable values rather than
   creating a new stored version. Record the actual execution separately from
   cache reuse in current-request provenance. Assembly selects the required keys
   and partition groups without creating another stored copy of their values.
   Use indexed grouped retrieval under item 11; values carry no required link to
   any producing invocation.

   **Accepted cache-hit provenance.** An otherwise valid matching cache entry is
   reusable without historical provenance. The current execution records the node,
   computation identity, effective parameters, input bindings, selected output
   groups, and their cache-hit outcome. Record actual execution only for missing
   groups whose handlers run. Upstream nodes bypassed by a hit remain skipped;
   do not execute them solely to fill in a trace. This resolves R2.

   Trace availability follows the selected graph's eligibility and durable records
   for the current request. A cache hit does not by itself make that trace
   incomplete. Historical cache-producer information is outside the guarantee,
   and tracing neither changes cache matching nor forces recomputation to recover
   that history. Current logical input bindings remain inspectable without
   pretending that skipped inputs were acquired or used by a handler in this run.

   Location-scoped metric rows retain their qualified partition and author key during
   assembly under item 3. Repeated author keys in different partitions remain
   distinct. Current result/trace references show which groups were computed and
   which were reused without renumbering row keys or assigning a fictitious
   invocation to a cache hit. Operation rows retain their qualified input/target
   pair identities in the same way. Current selection relationships remain
   separate when location records are shared.

   Trace eligibility and caching policy remain independent. Location-scoped nodes
   may use per-location caching in a graph made ineligible for tracing by an
   unpartitioned dependency. Ownership and stable row identities do not establish
   partition independence; authors still own that separate promise.

   **Accepted.** Item 11 fixes the value-storage layout, scoped primary and foreign
   keys, and atomic publication, applying R1's accepted row identities. Result
   publication relative to durable current-request trace metadata remains under
   R10.

   [Cache implementation](src/lyra/runtime/cache.py),
   [executor](src/lyra/runtime/execution.py).

10. **Keep each node kind's ordinary return contract.**

    **Accepted.** Metric handlers return ordinary named output tables, and
    location-operation handlers return ordinary correspondence mappings regardless
    of the selected graph's trace eligibility. Every location-scoped metric output
    schema declares row identity and location ownership. Lyra constructs current-execution
    provenance, including completed empty groups, from validated outputs and
    execution metadata as specified in item 4. Handlers provide no row/group
    dependency declarations or provenance wrapper. Graph eligibility follows the
    validated node declarations and output scopes under item 1.

    Lyra determines eligibility automatically and retains current-request
    provenance for eligible executions under item 1. Clients do not select a
    tracing mode, and handlers do not branch on a request-level tracing flag.
    Report trace availability from the retained current-execution records. Cache
    hits follow R2's values-only contract and do not require historical provenance.

    Decorator names, result-class names, and field spelling are implementation
    details within the agreed contract.

11. **Store results and provenance in SQLite and retain them.**

    **Accepted backend and deployment scope.** Use SQLite for Lyra-managed result
    data, cache lookup, structural result scopes, and current-execution provenance.
    Local execution and
    API/worker execution use the same storage implementation. The initial target
    is one machine with a modest workload; workers on multiple machines and a
    high-workload deployment are not planned for the foreseeable future.

    **Accepted native row storage and relational layout.** Persist ordinary SQL
    rows for metric outputs, stored location records, and operation
    correspondences. Use two shared metadata tables and one typed data table per
    registered named output. The computation key identifies the stored result
    directly under item 9's deterministic computation contract. The conceptual
    location-scoped metric layout is:

    | Table | Columns | Primary key |
    | --- | --- | --- |
    | `stored_results` | `computation_key`, `node_id`, `output_scope`, `partition_dataset`, `partition_revision` | `computation_key` |
    | `stored_partitions` | `computation_key`, `location_id` | `(computation_key, location_id)` |
    | Named output, such as `scores` | `computation_key`, the declared location-owner column, the declared row-key column, and ordinary value columns | For location-scoped metrics, `(computation_key, location_id, row_key)` using the corresponding declared columns |

    `stored_results` describes a complete persisted computation, including one
    covering zero locations. `node_id` identifies its producer and registered
    named-output definitions. `output_scope` explicitly distinguishes location-scoped
    and unpartitioned results. For location-scoped results, `partition_dataset` and
    `partition_revision` are the owning location dataset's existing ID and revision
    from item 20, copied
    from the resolved partitioning input, or the provider's own dataset. They are
    not additional author-managed versions. The revision remains nullable for
    the accepted frozen, unversioned identity.

    `stored_partitions.computation_key` references `stored_results`. Each location-scoped
    output row references its owning `(computation_key, location_id)` partition. The
    result header supplies that location's dataset/revision scope. A computation
    key alone is not a metric-row primary key: one computation may cover multiple
    locations, and each location may produce multiple rows per named output.
    `location_id` and `row_key` above illustrate author-declared columns, not
    additional duplicate columns required in every output schema. Every
    location-scoped metric output uses these declared ownership and row identities,
    regardless of caching strategy or graph trace eligibility.

    Provider rows retain one record per resolved location. Operation data tables
    retain their input/target pair identities under the same computation and
    owning-partition scopes. The existing node-kind identity contracts apply;
    string metric-row keys and structural integrity follow R7. Use the
    scoped primary-key indexes for computation lookup and grouped partition
    reads. Physical naming, additional query indexes, and SQL construction remain
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

    Retain logical schemas once with the registered named-output definitions,
    including column types, nullability, descriptions, units, and CRS needed to
    reconstruct results in the supported current store. Do not repeat an
    `output_schemas` document on each result or data row. Dataset scope belongs
    on the result header; ordinary value rows need not duplicate it.

    **Accepted completion and empty results.** Publish a result header, all its
    applicable partition records, and all named-output rows in one transaction after
    successful validation. Header presence represents a complete stored result.
    Each listed partition covers every declared named output; zero rows in one
    of those output tables represents a successful empty output for that
    partition. This structural scope supplies completion without a separate
    completion object or per-row flag. Failed or unprocessed work is not
    published as an empty result.

    For example, a `full` computation `K_ABD` has one result header and partition
    records for A, B, and D. If B produces no score rows, its partition record
    remains. A successful persisted location-scoped `full` or `off` computation over
    zero locations has a result header and no partition or output rows, distinguishing
    it from an absent stored result. With `per_location`, A, B, and D have separate
    computation keys and headers even when computed in one batch. A request for
    zero locations has no per-location computations; its empty selection remains
    in the current-request node context without inventing a partition or key.

    An unpartitioned header covers all declared named outputs. Zero rows in a
    named-output table means that whole output completed empty. Publish the header
    and all outputs atomically, including when every output is empty; header absence
    remains a cache miss. Empty bound inputs may produce nonempty unpartitioned
    outputs under item 4. Explicit scope distinguishes these results from a
    location-scoped computation covering zero locations.

    **Accepted reuse and storage identity.** Store one immutable logical result
    per computation key. Repeated successful persistence of the same key retains
    that result. No independent storage ID, stored-version ID, or key-to-value-ID
    mapping is needed. Matching published results are reusable only when the
    declared caching strategy permits it; stored values do not bypass an `off`
    invocation. The initial layout has no `cache_available` flag. Independent
    invalidation of individual entries is deferred; changes to declared input or
    implementation identities produce different computation keys.

    Cache value records and current-request provenance are independently owned
    within the shared store. Cache lookup needs only a computation key and its
    values. A trace can associate the current result with reused value references
    without storing an inverse producing-invocation link in cached data. Cache
    entries are the matching published results eligible for reuse under item 9,
    including empty ones. Assembly and separate current-request uses reference
    their computation keys and selected groups without copying the stored values.
    Actual invocations, cache hits, and skipped work remain distinct in provenance.

    Retain execution metadata as ordinary relational records in this shared SQLite
    store. Use scoped keys and foreign keys where applicable and write related
    records transactionally. A trace row or group reference resolves against its
    retained identity and membership records or stored result rows, as appropriate;
    it does not require a value-table row for an intentionally unstored intermediate.
    Do not persist intermediate-value snapshots solely to satisfy a reference.
    The complete physical provenance schema remains an implementation detail.
    Coordination with caller-visible result publication and failure handling remain
    under R10.

    Arrow files and Arrow blobs are not the persistent table representation.
    WKB remains the standard representation of individual geometry cells. Arrow
    bulk downloads and GeoParquet exports are generated from selected rows through
    standard library conversions. Ordinary result storage does not require a
    separate filesystem artifact store or coordination between SQL references and
    external payload files.

    Use the Pandera-derived storage descriptions and supported types in item 6.
    Store the logical schema and CRS metadata needed to reconstruct current-store
    results, including empty outputs, without a custom per-value codec. Immutable
    value references can be shared by caches, assembled results, and current traces;
    do not duplicate operation mappings solely for caching and tracing.

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
    and exclude metric execution, Pandera validation, and integrated provenance
    processing. They establish feasibility, not performance guarantees or test
    thresholds. Verify the integrated implementation on representative workloads.

    **Accepted retention.** Result data is append-only and immutable within the
    supported current store. Retain stored values, structural membership information,
    and current-execution trace records indefinitely. Changed computation keys,
    caching policies, and expired job records do not delete underlying results.
    Trace traversal resolves stored identities independently of transient RQ records.
    Automatic cleanup, retention counts, and ownership-based deletion are
    deferred. Storage growth is accepted, and an explicit development-store reset
    is allowed. Indefinite retention applies to data actually written; item 12
    still determines which intermediate values are persisted. No backward
    compatibility, migrations, or legacy-result handling across incompatible
    schema or implementation changes are required in closed alpha.

    **Accepted scope.** This fixes the value-storage layout and its completion
    contract, resolving R4. Retained schema metadata and current-request
    provenance have their own records; the three-table metric example is not the
    complete provenance database. Publication of caller-visible results relative
    to durable trace metadata, and provenance-storage failure behavior, remain
    under R10.

12. **Retain current-execution provenance without intermediate-value snapshots.**

    **Accepted.** Executions with eligible graphs automatically retain current
    node contexts, logical computation identities, effective parameters and
    bindings, declared external-origin identities, and actual execution, cache-hit,
    and skipped outcomes. Retain output row identities, membership including empty groups,
    and owning locations for obtained results. Input bindings
    distinguish declared context from values actually acquired or supplied in
    this request. Store shared context once per node use without enumerating
    per-row contribution relations or tracing historical cache producers.

    Lyra produces these records automatically as it resolves node uses, supplies
    inputs, executes handlers or reuses cached values, and validates returned
    outputs. Completion and row/group membership derive from validated results;
    skipped work retains declared context without fabricated acquisitions or
    invocations. This is runtime execution metadata, not an author-supplied lineage
    payload. Eligible traces retain it independently of transient job records,
    even when intermediate values are not retained. Inspection reads the retained
    records through the shared store.

    Intermediate metric values and location geometry/attribute tables follow the
    caching policy. With caching off, these intermediate values are used in memory
    without requiring snapshots solely for tracing. Final job outputs remain
    persisted for retrieval. A node skipped because of a cache hit requires no
    intermediate-value acquisition or fabricated invocation record.

    Retaining operation correspondence identities and membership can preserve a
    complete mapping, including empty groups. Store that mapping once in the
    shared result layer when required for the current trace, even with operation
    caching off. A current trace and an eligible cache entry may reference the
    same immutable values; those values need no link to a producing invocation.
    Trace retention does not enable cache reuse, and cache storage does not depend
    on trace retention. Geometry snapshots, rejected-candidate records, and
    historical cache ancestry are outside this retention requirement.

    Cached values can supply intermediate values for inspection when retrieved in
    this request. Inspect their current reuse context rather than following their
    historical producers. Missing values for executed uncached intermediates and
    values never acquired for skipped nodes are distinguishable states. Neither
    requires running a handler solely to complete inspection.

    **Deferred.** An explicit per-run diagnostic option could capture all node
    outputs for later value inspection. The initial implementation does not
    guarantee recovery of every intermediate value or snapshot external source data
    solely for tracing.
    Capturing node outputs does not preserve executable code or guarantee future
    availability of external sources.

13. **Inspect current execution and cache reuse through one shared operation.**

    **Accepted.** Expose one bounded, read-only operation through Python, HTTP, and
    MCP. Given a row reference, return its current logical node and computation
    identity, effective parameters, owning location, operation targets where
    applicable, and whether its group was computed or reused. Show actual handler invocation
    details only for work performed in this request. Expose current named input
    bindings and any acquired result references, declared external-origin IDs and
    revisions, and value availability. Distinguish executed provider acquisition
    and handlers declaring direct origins from cache reuse and skipped work.
    Input bindings are current-request context and do not assert exact row contributions.

    Trace availability is explicit result metadata. When a complete trace is
    unavailable, expose its reason rather than presenting an incomplete history
    as complete. Graph eligibility alone does not establish that inspection can
    resolve all required current-execution records; apply the durability condition
    in item 1. Detailed availability and storage-failure handling remain under R10.

    The same operation accepts a reference to a completed metric or operation
    output group, including an empty group. Expose its qualified owning location,
    named output, current node context, computed-or-reused outcome, effective
    parameters, and member row identities or operation target identities. Obtained
    input selections retain references to their current-result groups, including
    empty groups. Inspection can therefore explain that an empty value was computed
    or reused without inventing a row or historical producer. Inspection reports observed results and execution context without
    explaining every excluded record or proving exhaustive selection.

    Clients follow current node-context, input-binding, and available result
    references to walk the declared graph. Mark nodes bypassed by cache hits as
    skipped. Provide bounded, paginated access to acquired selections and group
    members; declared-only inputs have no fabricated member tables. Missing
    intermediate values or historical cache-producer metadata do not make the
    supported current-execution trace incomplete. Inspection never reruns handlers
    to recover them. Agents can inspect traces through MCP without code execution.

    **Accepted inspection failures.** Reject a malformed caller reference as a
    validation error. A well-formed caller reference with no matching target returns
    not found. A broken internal reference in an existing retained trace is an
    integrity error, rather than ordinary value unavailability. References must
    resolve within their recorded scopes; the existence of a similarly keyed row
    in another output, partition, or node context is insufficient.

    Intentionally unstored intermediate values remain unavailable while retained
    identity and membership metadata remain resolvable. Completed empty groups are
    valid without rows; skipped and reused work has no new handler invocation.
    A zero-location request can retain a node context without partition or row
    records. Historical cache-producer records are outside scope. Inspection uses
    ordinary shared reference parsing and store lookups, not a separate recursive
    revalidation pass, and never fabricates records or acquires external data to
    repair a trace. Report inconsistencies encountered during inspection; exact
    error models and transport mappings remain implementation choices.

    Exploring a large graph requires multiple calls. Automatic recursive expansion,
    graph visualization, and dedicated downstream-impact search remain deferred.
    Reference encoding, method names, and pagination details are implementation
    choices within this contract.

14. **Use Arrow bulk downloads and paginated JSON row access.**

    **Accepted.** Serve named output tables as Arrow bulk downloads and provide
    bounded, paginated JSON rows through HTTP and MCP. Keep status, schemas,
    provenance, previews, and download links in JSON. Agents can read
    result values through MCP without code execution or an Arrow reader. Row access
    complements the accepted trace-inspection operation.

    Result and status interfaces expose trace availability and a reason when a
    complete trace is unavailable, consistently through Python, HTTP, and MCP.
    Calculate this from graph eligibility and retained current-execution records under
    item 1. Field names and response encoding are implementation choices.

    Unpartitioned named tables use the same ordinary result retrieval, Arrow
    downloads, and paginated JSON access. Their result descriptions expose the
    unpartitioned scope without mandatory location or author-row-key fields. Graphs
    containing these metrics report complete tracing as unavailable under item 1;
    retrieval does not require location ownership or row/group trace references.

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

    Preserve the qualified partition and author key for location-scoped metric rows,
    and the input/target pair for operation rows. Assembly retains current
    result/table and output scopes, with trace references to the current node
    contexts and computed-or-reused group outcomes. Empty metric and operation
    groups remain addressable without a row or historical invocation. How callers obtain the
    corresponding durable row and output-group references remains under R10.

    Pagination must traverse every row of a fixed result once, without omissions
    or repetitions, including distinct stored rows with equal values. Preserve
    declared row identities where present; internal row addressing supports
    unpartitioned outputs without creating public semantic
    keys. Its cursor and traversal arrangement are implementation details;
    the API does not promise any relative row order. Apply the same coverage rule
    to paginated provenance group members. Separate reads and transport formats need not
    present rows in the same physical arrangement.

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
    storage implementation and R10.

    Compare location contents by aligning records through qualified IDs and
    columns through names, then using library table comparison, including Arrow's
    comparison where used in memory. Physical row and column positions do not
    determine semantic equality. Supported logical types, values, geometry,
    location scope, CRS, and explicit scalar ordering fields remain significant.
    Use library alignment and comparison operations instead of custom normalization
    rules for every scalar representation. Potentially slower repeated table
    comparisons are an accepted tradeoff.

    Remove custom Arrow-buffer normalization where it exists solely for integrity
    or equality, and remove location-content fingerprints from cache identity.
    Cache keys use the accepted identity descriptor in item 9. A byte checksum
    identifies stored bytes; different physical arrangements of logically equal
    records can have different checksums.

    Stored values are addressed by their computation key, a hash of declared
    computation identity rather than output contents. Current execution and trace
    references identify specific request contexts and remain distinct from that
    value identity. Equal values do not establish the same execution provenance.

    Location-data identity is fully captured by
    `(dataset_id, revision, location_id)`, including `revision=None`, for
    records supplied by registered providers in all managed executions. This
    includes externally prepared locations exposed through registered datasets.
    Trust this identity without content hashing, comparison, or additional
    identity verification. Location content comparisons and storage
    integrity checks serve their separate purposes; neither is required to
    establish location-data identity.

    **Accepted.** Computation-key contents follow the descriptor in item 9, using
    implementation and location-data identities, effective parameters where
    supported, and the identities of bound inputs. Apply item 9's accepted
    normalization, deterministic JSON encoding, and SHA-256 hashing.

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

    Traceability records the selected operations, their returned mappings, and
    their execution or reuse in the current request under item 4. It does not require replacing automatic
    route discovery.

    Determine trace eligibility after normal route selection. Traceability does
    not filter candidate routes, change shortest-route selection or tie preferences,
    or resolve ambiguity. If the selected graph contains an unpartitioned metric,
    execute the otherwise valid calculation and report complete tracing unavailable
    with the responsible node and path. Do not choose a longer route solely to
    obtain a trace. Invalid definitions and plan errors retain their normal failure
    behavior. This resolves R9.

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
    origin registration. Keep implementation identity and selection provenance
    distinct from this dataset identity.
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
    inside the source boundary, not calculation parameters or descriptor inputs.
    Any configuration that changes authoritative record contents must respect the
    location-data identity guarantee. An upstream computation can determine which
    IDs are requested; retain that selection history in provenance separately from
    the provider's cache identity. The provider key contains its implementation
    identity, computation scope, dataset ID/revision, and requested location IDs.

    Require the provider schema's geometry column to declare its expected CRS.
    Derive advertised CRS and correctly tagged empty results from that schema;
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
    snapshot. Record dataset ID and revision alongside current node and row provenance.
    Include descriptions in discovery and retained provenance, but exclude their
    text from dataset identity, routing, and cache keys.

    Computed operation mappings are computation results with their own identity,
    separate from the identities of the participating location records. Changing
    an operation's implementation or parameters can change its mapping without
    changing those records. For example, different search radii can map the same
    building to different hospital selections while all location identities remain
    unchanged. Record the operation implementation, effective parameters, and
    inputs in its computation identity as specified in item 9.

    Dataset ID and revision alone do not constitute a provider computation key;
    the implementation, computation scope, and selected location IDs also matter.
    Metric and operation keys include their additional accepted descriptor fields
    and use the accepted normalization, serialization, and hashing from item 9.

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

    Current-execution provenance remains in Lyra's retained store. Full trace inspection therefore
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
    author-supplied row/group contribution declarations, fine-grained cell or
    parameter influence tracking, and automatic stored-result cleanup can follow
    real usage. Inferring semantic dependencies from handler code and checking
    semantic completeness are outside scope. Lyra automatically records current
    execution and cache reuse for metrics and operations from outputs, schemas,
    and execution metadata. Identifying exact contributing rows, explaining
    rejected candidates, and proving exhaustive selection are outside the initial
    tracing guarantee. Reconstructing the historical executions that populated
    cached values is outside the initial specification.
    Differentiability is a separate long-term goal and is not specified here.

    Creating and managing new locations through operations, including location
    merging and splitting, is outside the initial implementation. Operations map
    existing locations; external preparation and dataset registration supply newly
    constructed ones.

    Caller inputs follow item 2's accepted scope: parameters and location
    selections for every managed execution. Local development preserves private
    metric roots while sharing provider acquisition and execution machinery.
    Full location-record injection and arbitrary table injection, including
    stored-result references in place of declared table dependencies, are outside
    the initial implementation.

    Unpartitioned metrics are supported under item 3 with one scope for all of a
    metric's outputs, whole-table forwarding, ordinary output validation, and
    `off` or `full` caching. They may participate in hybrid graphs and use ordinary
    result storage and retrieval. Mixing output scopes within one metric and
    location-independent row/group tracing are outside the initial implementation;
    a graph containing an unpartitioned metric reports complete tracing unavailable.

    Secondary metric location-association declarations and row-to-location links
    beyond the owner are deferred. Other output ID columns remain ordinary data
    under their declared schemas. The initial metric trace covers the owning
    location, row identity, node context, and named inputs; it does not interpret
    those other columns as location references. Primary ownership validation and
    the existing operation-correspondence contract resolve R6.

    Every location-scoped metric output requires ownership and stable row identity;
    missing declarations are invalid definitions. Trace eligibility follows the
    validated selected graph's output scopes. All nodes declare direct external
    acquisitions under item 2, with provider origin metadata derived from dataset
    registrations. Origin IDs and revisions participate in computation keys;
    descriptions remain informational. These contracts resolve R5.

    Metric-row keys are strings. Shared definition and plan checks reject unknown
    binding names, and producer validation requires every declared metric output,
    including explicit empty tables, with no unexpected output names. Lyra derives
    provenance directly from validated outputs and resolved execution records.
    Construction invariants, scoped database constraints, transactional writes,
    and ordinary inspection parsing/lookups provide structural integrity without
    a separate provenance revalidation subsystem. These contracts resolve R7.

    Per-node caching modes, trusted location-data identities, author-managed
    implementation identities, computation-key descriptor contents, and descriptor
    normalization, serialization, and hashing are accepted. SQLite native row
    storage, the supported column types, and Pandera/SQLAlchemy integration are
    accepted in items 6 and 11. Deterministic results identified directly by their
    computation keys, the shared result/partition tables and typed output tables,
    scoped keys, and atomic value publication are accepted in items 9 and 11,
    resolving R4. Independent per-entry cache invalidation is deferred.
    Parameter-binding provenance and the API/worker parameter handoff are accepted
    in items 5 and 8, resolving R8. Automatic current-execution provenance and
    inspection of output groups, including empty metric and operation groups, are
    accepted in items 4, 10, 12, and 13, resolving R3.

    Automatic graph-based trace eligibility and retention, one client request
    type, and explicit result trace availability are accepted in items 1, 8, 10,
    and 14. Normal route selection precedes eligibility assessment, resolving R9.
    R2 is resolved: caches store values by logical computation identity, and the
    inspected request records its own cache hits without historical producer
    links. Durable publication and handling of provenance-storage failures remain
    open under R10.

Implementation touches the existing [node definitions](src/lyra/sdk/plugin.py),
[output contracts](src/lyra/contracts/outputs.py),
[dependency bindings](src/lyra/contracts/dependencies.py),
[provenance records](src/lyra/contracts/provenance.py), and
[executor](src/lyra/runtime/execution.py). The accepted direction still requires
resolution of R10's durable trace access and publication contract. Incorporate
that decision into the working contract before translating it into
concrete authoring interfaces, runtime and storage changes, and end-to-end
verification. Deferred features remain outside that implementation scope.

## Open review issues

These issues track potential conflicts, remaining behavioral decisions, and
accepted constraints incorporated into the feature contracts above.
The `R1`–`R10` identifiers are stable and separate from the feature numbers above.
Resolve each issue individually by recording its decision, updating the affected
feature contracts, and marking the issue resolved.

### R1. Row identity across assembled cache fragments

**Resolved — accepted identity contract.** Affects items 3, 4, 9, and 14.

**Accepted metric identity.** Within every location-scoped metric's named output,
identify a metric row by its qualified owning location partition and an
author-declared row key. The
author declares the key column; the handler supplies non-null values unique
within that partition and output. Different partitions may reuse an author key:
`(building A, "roof")` and `(building B, "roof")` are distinct rows. Keys remain
stable for the same logical rows across input permutations and changes in the
execution batch of independently computable partitions.

Ownership and row-key declarations are mandatory for every location-scoped named
output, regardless of caching strategy or graph eligibility. Missing declarations
are definition errors, not a tracing limitation or an implicit scope change.

Unpartitioned metric outputs require neither a location owner nor an author row
key. They are forwarded as whole tables and use internal storage addressing under
item 11, without semantic row identities or complete row/group traces.

**Accepted assembly and provenance.** Downstream inputs identify a location-scoped metric row
through the named input, qualified partition, and author key. Assembly preserves
that identity within the current result/table and named-output scope. Current
trace references associate groups with their node contexts and identify whether
they were computed or reused. Cached values require no historical invocation
link. Reuse does not renumber keys or claim a handler invocation occurred. This
distinguishes rows from different partitions while representing both cache hits
and newly computed groups in the inspected request.

**Accepted operation identity.** Operation references retain separate input and
target location IDs, their dataset/revision scopes, and their current result/table
and named-output scopes. Sharing a target ID across different input
partitions does not duplicate relationship-row identity. Assembly preserves both
ends of each pair, with current trace references to its execution or reuse context.

Metric-row keys use strings under R7. Exact authoring declaration spelling
and transport syntax are implementation details owned by the shared contracts
under item 22. Exposure or lookup of durable row references through result
interfaces remains under R10.

### R2. Values-only caching and current-execution provenance

**Resolved — accepted values-only cache contract.** Affects items 1, 3, 4, 5, 8,
9, 10, 11, 12, 13, and 14.

**Accepted cache responsibility.** Cache entries map logical computation keys to
result values and the structural schema, output scope, named outputs, and applicable
partition and row-identity information needed to retrieve them. Cache matching and
payloads require no
historical producing invocation, calculation time, provenance record, or
trace-completeness flag. Under the deterministic contract in item 9, the
computation key directly identifies one immutable stored result. A logical cache
entry is a published result eligible for reuse under the node's caching strategy;
it requires no independent storage ID or key-to-value-ID mapping. Current results
may share those values without embedding an inverse invocation link in them.

The computation key accounts for node/code revision, validated effective parameters
including resolved defaults, declared input identities, external-origin IDs and
revisions including provider dataset identities, and the selection or partition
scope required by the caching strategy.
For example, an omitted `radius` whose default is `500` and an explicit
`radius=500` have the same effective value and cache identity. The current trace
records that value without supplied/default origin flags. Row keys distinguish
rows inside a cached partition; they do not replace the rest of the computation key.

**Accepted empty-result representation.** Publish the result header, applicable
partition records, and all named-output rows together after successful computation and
validation, following item 11. A matching header with zero output rows represents
a complete empty value; absence of the header is a miss. Reuse also requires a
permitting caching strategy. Partition records preserve empty outputs for known
locations; a header alone preserves a successful persisted location-scoped
computation over zero locations when it has a full-selection key. No separate
completion object or per-row completion field is required. Unpartitioned results have no
location partitions; their explicit scope and published header cover all named
outputs, including empty tables. Empty inputs may produce nonempty outputs under
item 4.

**Accepted current-request trace.** A valid matching entry can be reused
independently of historical provenance. The inspected request records its current
logical node, computation identity, effective parameters, input bindings, output
scope, and cache-hit outcome. Actual handler invocations are recorded only for
work executed in that request. Upstream work bypassed by a cache hit is marked
skipped; declared bindings do not imply acquisition or execution. Do not run
skipped nodes solely to populate provenance or recover an earlier cache producer.

Trace availability depends on the selected graph's eligibility and durable
current-request records. A cache hit is a complete outcome under this scope, not
a missing historical trace. It neither changes eligibility rules nor requires
recomputation for tracing. Earlier executions that populated cached values are
outside the guarantee. Parameters and their resolved defaults remain part of both
computation identity and the inspected request's recorded context.

### R3. Current-execution provenance for empty results and table membership

**Resolved — accepted execution-and-reuse contract.** Affects items 1, 2, 3, 4, 5,
6, 8, 9, 10, 12, 13, and 14.

**Accepted ordinary returns.** Metrics return ordinary named output tables;
all location-scoped outputs declare row keys and location ownership in
their schemas. Operations return ordinary
`dict[str, tuple[str, ...]]` correspondence mappings. Both use these return
contracts regardless of graph trace eligibility. Lyra automatically records
current-execution provenance; authors provide no row/group contribution declarations, dependency
list, or provenance wrapper.

**Accepted membership and completion.** Record each supplied primary partition's
successful completion in each named output, including groups with zero rows.
Retain member identities, qualified owning locations, operation correspondences, and
current node contexts. These contexts record logical implementation/computation
identity, effective parameters, parameter bindings, declared external-origin metadata, and
whether groups were computed or reused. Actual invocation and input-acquisition
records exist only for work performed in the inspected request. Named bindings
remain declarative for upstream work skipped because of cache hits. Acquired
input references identify current-result selections and groups, including empty
ones. They do not assert exact input-row contributions or require historical cache
producer references.

For a defect metric supplied with buildings A and B, an output containing only
`(B, "roof")` records that row and A's successful empty group. An empty,
schema-valid table records completed empty groups for both buildings. For an
operation returning `{"A": (), "B": ("H1",)}`, retain A's successful empty group
and B's relationship to H1. Failed or unprocessed work is not recorded as a
successful empty outcome, and unresolved operation targets remain acquisition
errors. A cache entry with the same logical result can supply these groups as
cache hits, including empty groups, without another handler invocation.

Item 11 distinguishes completed empty groups for supplied locations from a
successful result covering zero locations. Retain a result header for the latter
when it has a full-selection computation key and its values are persisted.
An empty per-location selection has no partition computation keys. Its current
node context still retains the selection and actual execution status. Stored
value existence and request-specific provenance remain separate responsibilities.

Unpartitioned completion applies to each whole named output under item 4, including
empty tables and computations over empty inputs. No location groups or semantic
row identities are fabricated. These results use ordinary retrieval; graphs
containing unpartitioned metrics are ineligible for complete tracing under item 1.

**Accepted inspection scope.** The shared bounded inspection operation follows
row or output-group references to their current node contexts and named bindings.
A downstream zero count can still reach the upstream empty group's computed-or-
reused outcome and effective parameters without a synthetic row or historical
producer. The trace describes observed membership and current execution or reuse; it
does not explain every excluded record, identify all contributing rows, or prove
exhaustive selection. Traversal requires retained current-execution metadata, not
snapshots of every intermediate value. Cache identity, source boundaries, and
graph eligibility retain their existing requirements.

### R4. Per-location caching behavior and eligibility

**Resolved — accepted deterministic computation and storage contract.** Affects
items 1, 2, 3, 4, 6, 7, 8, 9, 11, 14, 15, and 20.

The computation-key descriptor contents, normalization, serialization, and hashing
are accepted. The partition and binding rules below establish unordered output
membership, the rows a consumer receives, upstream execution scope, and dependency
identities. Items 9 and 11 accept deterministic results addressed directly by
computation key, the shared result/partition tables and typed output tables,
scoped primary and foreign keys, and atomic value publication. They use the
accepted SQLite native row storage, supported column types, and Pandera and
SQLAlchemy integration. Additional query indexes and physical SQL details are
implementation choices. Parameter-binding provenance and the API/worker parameter
handoff are accepted in items 5 and 8 and R8. Durable current-request trace
publication remains under R10.

**Accepted constraint — deterministic results.** All executable node kinds and
output scopes obey item 9's frozen-input and determinism contract. The
same computation key identifies the same logical output values and membership
across executions and caching strategies. Authors express output-affecting changes
through effective parameters or declared identities used in the key. Different
logical results under the same key are a contract violation. Trust this contract
without output-content hashing, comparisons, or behavioral proofs. Determinism
does not establish per-location independence; that remains a separate author
promise.

**Accepted constraint — trusted location-data identity.** The tuple
`(dataset_id, revision, location_id)` fully captures location-data identity and
identifies frozen contents, including when the revision is `None`. Apply this
contract to registered provider data in all managed executions, including local
development and externally prepared locations exposed through registered
datasets. Dataset producers must use a different tuple when covered contents
change. Use the tuple directly for cache identity without content hashing,
comparison, or additional identity verification. Producers own this guarantee.
Existing schema and structural validation remains independent of it.

The frozen contents are the dataset record, including relationship fields stored
in that record. Computed operation correspondences have their own computation
identity; changing a correspondence through different parameters or implementation
does not require changing the participating location records' identities.

**Accepted constraint — author-managed implementation identity.** Use
`(node_id, code_revision)`, where the code revision is an optional opaque token
and `None` is a stable identity value. This applies to metrics, operations, and
executable provider handlers. It identifies an implementation, while the
location-data tuple identifies a record; the complete computation identity also
accounts for effective parameters, inputs, and external-origin identities. Plugin
authors are responsible for
changing the token or node ID when implementation or behavior-affecting declarations
change.
Lyra trusts that declaration without automatic change detection, code hashing,
or inferred versions. Authors may automate token updates in their own tooling.
Include relevant upstream implementation identities in dependent computation
identities. Deployment build revisions retain their job-compatibility and
provenance purposes without acting as additional cache-invalidation tokens.

**Accepted constraint — output scope and location partitions.** Providers,
operations, and location-scoped metrics have a location-based partition mapping,
independently of caching strategy. Location-provider nodes
are partitioned by their own location identities, with one returned row per
successfully resolved location. Location-scoped metrics and operations have at
least one location input and partition by one named input. Lyra selects the sole
input automatically; otherwise the developer explicitly selects one. Each
location-scoped metric output row has one primary partition owner, with zero or
more rows per key in each named output. Each named output declares one scalar
owner column bound to the selected primary location input. Its non-null string
IDs must belong to the locations actually supplied to the invocation, qualified
by that input's dataset and revision. Other output ID columns are ordinary data;
they establish no location-reference or forwarding behavior.

Every location-scoped named output also declares a stable row key, non-null and
unique within each owning partition. Missing ownership or row-key declarations
make the metric definition invalid. Per-location independence remains a separate
author promise, not a consequence of these identity declarations.

A metric may explicitly select unpartitioned scope under item 3, bypassing primary
location-input inference. All its named outputs then belong to the whole
computation, with no mandatory location-owner column or stable row key. It uses
ordinary schema validation and supports `off` or `full`, never `per_location`.
Its inputs retain complete bound selections and its outputs are automatically
shared whole-table dependencies. Hybrid graphs are supported; a graph containing
an unpartitioned metric cannot provide a complete trace. Mixing output scopes
within one metric is outside the initial implementation.

**Accepted constraint — operation correspondence pairs.** Represent an operation's
correspondences as distinct `(input_location_id, output_location_id)` pairs with
declared dataset/revision scopes. Partition relationship rows by their first
component. Each input location has zero or more pairs, and different partitions
may reference the same target location without sharing the same relationship row.
This follows the same ownership and caching model as metric result rows. Lyra
automatically records these correspondences and their current execution or reuse as the
supported operation trace. Targets must be existing locations resolvable through
the declared target dataset's provider. Creating locations
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
Pydantic validation. JSON uses string keys and arrays of target strings. SQLite
stores separate input/target correspondence rows and completed-empty metadata,
as specified in item 4; tuple-valued table cells are unnecessary. All operation
handlers return the ordinary mapping. Lyra derives operation provenance, including
empty-group execution or reuse context, without author-supplied lineage or a result wrapper.

**Accepted constraint — declared inputs and automatic provenance.** Authors
declare executable node inputs, parameter bindings, source boundaries, and
partition-related or shared selection behavior. Lyra records named input bindings
and any acquired result references once per current node context, together with
output row identities, computed-or-reused outcomes, owning locations, and operation
correspondences.
No author-supplied row/group dependency declarations are required, and input bindings
do not assert that every supplied row contributed to each output. Computation
identities still account for all declared inputs. Different location partitions
may use a shared input while remaining independently computable. Authors remain
responsible for the independence promise. Input selection and upstream execution
scope follow the accepted rules below.

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

For other inputs of a location-scoped consumer with no established relationship,
require an explicit shared binding or a declared correspondence. An explicit shared declaration can override
partition-following behavior. Matching names, types, or IDs alone is insufficient.
Put the optional selection setting on existing input declarations; concrete API
spelling remains an implementation choice. These semantics apply independently of
cache mode. They determine consumer input selection; reducing producer execution
also requires the independence promise described below.

**Accepted constraint — upstream execution scope.** Cache-driven reduction of
downstream work may narrow an upstream partition request only when that producer
declares `per_location`. The producer then checks its own cache and computes only
its missing requested keys. Producers using `off` or `full` retain their initial
input binding for the current request, before cache-driven reduction. When the
upstream result is needed, `off` executes on that selection, while `full` reuses a
matching complete result or executes on a miss. Initially bound inputs describe the
request's selection, not the entire dataset.

For a producer initially bound to A/B/C in this request when only downstream B/C remain to be
computed, `off` and `full` operate on A/B/C; `per_location` receives B/C and may
compute fewer keys after its own cache lookup. The partition-related consumer
receives only the B/C groups in every case. Shared inputs remain at their complete
bound selections, including during reduced execution. Apply these rules at each
upstream dependency.

**Accepted constraint — node-local `off`.** Disabling a producer's own cache does
not require its execution when a valid downstream cache hit satisfies the request.
`off` means execute whenever invoked, with the same deterministic result for the
same computation key. Changed observations require changed key inputs. This mode
does not disable downstream caching. Persisted results remain addressable by
computation key even when the node's own strategy prevents their cache reuse.

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

**Accepted constraint — shared mapping storage and current reuse context.** Operations
use the same cache machinery as metrics. Store an operation mapping once in the
shared value layer, with cache lookup and current-result references able to share
those values. The mapping preserves membership and empty groups; an atomically
published cache entry represents a complete value without historical producer
metadata. The inspected request separately records execution or reuse. Storage supports
caching without trace retention, and retaining a mapping for tracing under `off`
does not enable that node's own cache reuse. A valid downstream cache hit may
bypass its invocation. Current traces retain this request's correspondences and
selection relationships and mark bypassed work as skipped. Sharing
location records does not merge selection paths.

**Accepted constraint — upstream computation identity scope.** A computed input
reference identifies its producer's logical computation, named output, and selected
partition groups where applicable. An unpartitioned input references the complete
named table under its producer's whole computation identity, without partition
selection. For a `per_location` producer, use the computation identity of
each selected partition, independent of its physical execution batch when its
effective inputs, parameters, and shared context match. For `full` or `off`, use
the logical identity of the producer's complete initially bound selection, even when the
consumer selects only some output groups. Shared inputs contribute their complete
bound selection. Derive these logical identities from implementations, parameters,
input identities, and declared external-origin IDs and revisions, without job/run
IDs, storage references, or output-content hashes. An `off` producer still has a
logical identity. Its computation key also
identifies its values whenever they are stored. Current-request execution
references identify separate node uses and outcomes; stored values require no
historical producing invocation.

For example, with A/B cached and A/B/D requested from a `per_location` producer,
only D is supplied through its primary location input to the executing handler.
Dependencies partitioned by the same location supply only D's groups, while shared
inputs remain complete. The consumer receives assembled A/B/D groups. With only
an A/B/C entry, a `full` producer computes A/B/D on the miss; `off` computes the
complete A/B/D invocation when needed. Logical per-location entries hold complete
groups across all named outputs and may reference partition values stored in a
shared batch; they do not require one handler call per key. Different
full selections have separate cache entries and remain available under
the retention policy. Physical partitioning of SQL rows does not enable subset
cache matching for `full`.

**Accepted constraint — computation-key contents.** Every node descriptor includes
`(node_id, code_revision)`, the declared output scope, and either a complete bound
selection or qualified partition scope.
Dataset providers add dataset ID, revision, and the unordered requested location
IDs, with no arbitrary calculation parameters or upstream-table inputs. Metrics
add effective validated parameters including defaults, all named input identities,
directly declared external-origin IDs and revisions, and a primary location input's
name when location scoped. Unpartitioned metrics have no primary location input
or owning partition in their descriptor and cover
all complete bound inputs. Operations add the metric components and their
declared target dataset ID/revision. Location inputs identify their dataset scope
and selected IDs; computed inputs use the accepted upstream logical identities,
named outputs, and selected groups. Shared inputs retain their complete bound
selection. Reference upstream computation keys without embedding entire ancestor
descriptors. Provider resource configuration and selection provenance stay outside
its cache key. The provider's dataset ID and revision already represent its derived
origin; no independently managed origin token is needed. Origin descriptions are
excluded from keys. Upstream origin changes propagate through producer computation
keys. One descriptor model and key-generation mechanism serve all node kinds and
output scopes, independently of physical storage.

Per-location descriptors cover one primary partition and its applicable inputs,
including complete shared inputs. Full and uncached descriptors cover their
complete initially bound input selections. Exclude incidental ordering, job/run IDs,
storage references, and location/output content hashes; preserve meaningful
explicit sequences in data and parameters. Hash a deterministic descriptor
encoding into the cache key using the accepted method below.

**Accepted constraint — descriptor normalization and encoding.** Use Pydantic's
validated effective parameter values and serialization under item 7's supported
JSON-value contract, including all defaults and null fields. Omitted and explicit
equal defaults have the same key, without supplied/default origin tracking.
Preserve scalar distinctions remaining after validation. Arrange unordered
identity collections consistently by explicit keys, ignore dictionary insertion
order, and preserve meaningful parameter sequence order. Serialize the
descriptor as deterministic JSON with sorted object keys, fixed compact options,
and non-finite numbers rejected, then hash its UTF-8 encoding with SHA-256.
Use the standard JSON and hashing libraries. Reject unsupported values without
arbitrary string fallbacks; do not hash location or output contents.

**Accepted constraint — SQLite row storage.** Use item 11's shared
`stored_results` and `stored_partitions` tables and one typed data table per named
output. `stored_results` is keyed directly by `computation_key` and retains
`node_id`, explicit output scope, and, for location-scoped results,
`partition_dataset` and `partition_revision`. The latter two fields are the
existing owning dataset's ID and revision, not additional author controls.
`stored_partitions` is keyed by `(computation_key, location_id)` and references
the result header. All location-scoped metric rows are keyed within their
named-output table by `(computation_key, location_id, row_key)` and reference their partition.
Provider rows and operation pairs retain their accepted node-kind identities.
Retain logical schemas once with registered output definitions. Additional
physical row addressing for unpartitioned outputs remains an implementation detail.

Unpartitioned results have no owning dataset or location partitions. Their rows
reference the computation directly, with internal row identifiers as needed for
storage and pagination. These identifiers imply neither semantic row identity nor
ordering. The explicit scope distinguishes these results from location-scoped
computations over zero locations; no synthetic location is introduced.

A `per_location` key identifies one complete partition across all named outputs;
a `full` key identifies the complete bound selection. Publish the header,
applicable partition records, and all outputs in one transaction. A known partition
with zero data rows has completed empty outputs. A successful persisted
location-scoped full-selection computation over zero locations has a header with
no partition or value rows. For an unpartitioned result, the header covers all
whole named outputs, including empty tables; empty inputs can produce nonempty
outputs under item 4.
An empty per-location request has no partition computations and remains recorded
in its current node context. These records preserve completion without a
separate completion object or a fabricated row or invocation.

Cache reuse follows the declared strategy and matching published result. The
initial layout has no independent storage/version identifier, cache-to-value
mapping table, or `cache_available` flag. Independent per-entry invalidation is
deferred. Repeated persistence under the same key retains the immutable value;
assembly and current traces reference existing keys and groups. Keep
current-request contexts, bindings, and actual execution/reuse/skipped outcomes
separate from those values. Retain operation correspondences once for both cache
and current-result access. R2 defines their independent provenance responsibilities.

Item 6 specifies supported scalar columns, WKB geometry cells, and the
Pandera-derived storage description. Use SQLAlchemy Core and standard dataframe
and geometry conversions, batch inserts, and indexed grouped reads. Arrow is a
download format generated from selected SQL rows. The workload assumptions and
exploratory performance evidence are recorded in item 11; the integrated
implementation still requires representative performance verification. No legacy
result readers or migrations across incompatible code/schema changes are required.

**Resolution.** The rules above settle per-node caching eligibility and behavior,
computation identity, the value-storage layout, scoped relationships, and atomic
publication. Preserve the accepted independence and unordered-collection
contracts. String metric-row keys and structural validation follow R7. Durable
current-execution metadata and result trace-availability publication remain under
R10.

Use these rules to define which nodes and output declarations may select
`per_location`. Apply the assembled row-identity contract accepted under R1.

### R5. Provider acquisitions and external origins in the trace graph

**Resolved — accepted declaration and automatic-acquisition contract.** Affects
items 1, 2, 3, 6, 7, 8, 9, 12, 13, and 20.

**Accepted provider boundaries.** Dataset registration automatically establishes
the provider's external boundary. Use the requested dataset's ID, revision, and
description as its origin metadata. A provider serving several datasets uses the
metadata for the dataset being requested, without another origin registration.
Keep provider node ID and code revision as implementation identity, separately
from dataset and location-data identities.

For eligible executions, record acquisition when the provider handler executes.
A cache hit records the current reuse and declared dataset origin without an
acquisition event. A provider bypassed by a downstream hit remains skipped, with
its declared dataset context but no invented invocation or acquired rows. Cache
hits follow R2 and require no historical provider invocation.

**Accepted direct external origins.** Metrics and operations declare a collection
of origin descriptors for external data they acquire directly. Each descriptor
has a required stable `id`, optional opaque `revision`, and optional plain-text
`description`. An origin need not be a registered location dataset. Do not repeat
origins already represented by declared upstream dependencies. An empty collection
promises no additional external acquisition.

Complete dependency and origin declarations are an author obligation for all
output scopes and caching strategies, including unpartitioned metrics and `off`.
Undeclared acquisition violates that contract; Lyra does not inspect handler code
or enforce I/O behavior to discover it. Internal queries, file reads, and processing
remain outside tracing. Connection details, query text, file hashes, and internal
processing descriptions are not required metadata. Actual metric and operation
invocations retain their declared origins in eligible traces through the ordinary
node machinery.

**Accepted identity and eligibility.** Origin IDs and revisions participate in
computation keys; descriptions do not. Provider dataset IDs and revisions already
represent their derived origins. Dependent computations incorporate upstream
origin changes through producer computation keys. `revision=None` retains the
fixed, unversioned-input contract, including in development.

Validate node definitions, required ownership and row identities, and origin
descriptor structure before resource acquisition through the shared boundaries.
A valid selected graph is structurally trace eligible when every reachable metric
is location scoped. An unpartitioned metric makes complete tracing unavailable
without preventing calculation. Missing required ownership or row-key declarations
on a location-scoped metric are invalid definitions. Node kinds, output scopes,
identities, and origin metadata determine these capabilities. Origin declarations
preserve traversal of upstream dependencies and cannot conceal an unpartitioned
metric or turn its graph into an eligible one.
Actual trace availability still requires durable current-execution metadata under
R10.

**Accepted input scope.** All managed executions accept parameters and
location selections, including local development. Registered providers supply
location records; Lyra obtains table dependencies from declared upstream nodes.
`run_development` retains public and private concrete metric roots while sharing
request preparation, acquisition, validation, and execution machinery with the
normal path. Full location-record injection, arbitrary table injection, and
caller-supplied result references as table-dependency replacements are outside
the initial implementation and this review issue.

**Accepted constraint.** Provider-loaded locations use the trusted
`(dataset_id, revision, location_id)` identity specified in item 20 and R4,
including fixed development datasets. Caller-selected IDs are request input
context, distinct from the provider acquisition or cache reuse that supplies
their records.

Dataset providers use the narrow lookup interface in item 20. Retain the graph
history of a computed ID selection as selection provenance, separately from the
provider's data and computation identities. This does not add arbitrary
calculation parameters or upstream-table dependencies to the provider interface.

**Resolution.** Provider acquisitions use ordinary provider nodes and automatically
derived dataset origins. Metrics and operations declare their direct origins as
metadata while retaining their ordinary interfaces. The accepted scope, identity,
validation, and cache-key rules above settle R5. Declaration spelling remains an
implementation choice. Primary ownership and operation correspondences follow R6;
structural validation follows R7. Durable trace access and publication remain under
R10.

### R6. Metric ownership and location correspondences

**Resolved — accepted primary ownership and operation-correspondence contract.**
Affects items 3, 4, 6, 12, 13, and 20.

**Accepted metric ownership.** Every named output of a location-scoped metric
declares exactly one scalar owner column associated with the metric's selected
primary location input. Every returned row has a non-null string owner ID belonging
to the locations actually supplied to that invocation. Dataset and revision scope
come from that named input. Reject owners that exist elsewhere in the dataset or
request but were not supplied to the invocation.

Multiple rows may share an owner; their declared row keys distinguish them within
the partition and named output. A supplied owner may produce zero rows. Validate
non-nullness and membership at the existing shared producer boundary, without
another provider lookup. Missing ownership or row-key declarations remain invalid
definitions. Exact declaration syntax is an implementation choice.

**Accepted metric-column scope.** Columns other than the owner and row-key columns,
including IDs of other locations, are ordinary data subject to their declared
schema checks. Lyra stores,
returns, and forwards their values without interpreting them as location references
or acquiring records. Secondary metric association declarations and row-to-location
links beyond the owner are deferred from the initial implementation. They are
outside the complete-trace guarantee, which retains the owning location, row
identity, current node context, and named input bindings.

All named input dependencies, selection rules, and their computation-key identities
remain supported. Operation correspondences continue to derive and forward location
selections. Unpartitioned metrics retain their whole-table contract without
mandatory ownership or row keys. Ordinary ID columns do not affect a graph's trace
eligibility or establish additional dependency edges.

**Accepted operation identities.** An operation correspondence row contains input
and target location IDs qualified by their declared dataset/revision scopes.
Several correspondence rows may refer
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
target strings. SQLite stores individual input/target correspondence rows and
structural partition/output scope for empty groups. A published cache entry with
zero rows is a valid empty result under R2. Preserve membership and use
separate input and target fields for relationship references, with their
surrounding scopes, as specified in item 4. Lyra attaches current-execution provenance
automatically to returned pairs and completed empty groups.

**Accepted constraint — existing target locations.** Every operation target must
resolve through the declared target dataset's provider. One-to-many and
many-to-one correspondences between existing locations remain supported.
Operations may perform spatial calculations to determine correspondences, but
creating locations, assigning their identities, and registering their geometries
and attributes are outside the initial implementation's operation contract. Newly
constructed locations, including merged or split locations, are prepared externally
and exposed as a registered dataset. Their external preparation lies outside Lyra's
trace graph.

**Resolution.** Primary metric ownership, ordinary handling of other output
columns, and the accepted operation rules above settle R6. Operation-row references
follow R1's identity and current-result scopes. A target location retains its own
identity, distinct from correspondence rows and their current execution or reuse
context. String metric-row keys and provenance-reference integrity follow R7.

### R7. Mechanical identity and provenance validation guarantees

**Resolved — accepted string identities and shared integrity boundaries.** Affects
items 3, 4, 5, 6, 11, 12, 13, and 22.

**Accepted string keys and ownership.** Metric-row keys are strings, non-null and
unique within one owning location partition of one named output, with stability
under the permutations and batching permitted by item 3. Authors represent numeric
or composite identifiers as stable, unambiguous strings. Use the ordinary declared
string-column validation at the shared producer boundary and standard JSON, SQLite
text, and Arrow string representations. Public reference models use shared strict
string types without an additional string/repr fallback or custom identity codec.

Every location-scoped named output declares ownership and a row key. Missing
declarations are invalid definitions. R6 fixes one scalar owner column with a
non-null location ID belonging to the primary location input actually supplied to
the invocation, qualified by that input's dataset and revision. Check membership
without another provider lookup. Location records and operation mapping IDs retain
their strict string contract through Pydantic. Providers and operations retain
their node-kind identities. Unpartitioned outputs require ordinary schema validation
without mandatory ownership or row keys. Secondary metric associations remain
outside the initial scope.

**Accepted names and completion.** Reject references to unknown input or output
names in definitions and resolved bindings during definition or plan validation,
before resource acquisition. At the shared producer boundary, a metric must return
every declared named output and no undeclared output names. Empty outputs require
explicit schema-valid empty tables. Apply this rule to both metric output scopes;
column validation remains governed by the declared Pandera schemas.

Operation mappings retain their accepted completion semantics: reject input keys
not supplied to the invocation and repeated target IDs within a tuple; normalize
omitted supplied keys to empty target groups after a successful return. Omitting
an operation's location key is distinct from omitting a declared metric table.

**Accepted automatic record construction.** Lyra produces provenance as the executor
resolves node contexts, parameters and bindings, supplies inputs, and executes or
reuses computations. Row identities, output membership, and completed groups derive
from validated results. Actual invocations exist only for executed work. Eligible
traces retain these records in the shared SQLite store independently of transient
jobs and any intentionally unstored intermediate values. Authors supply ordinary
tables or operation mappings, not a separate provenance-reference payload.

Current-execution references identify records within their intended request, node
use, named output or parameter, partition, and row/group scope as applicable.
An invocation reference identifies an actual invocation; a row reference must
belong to the recorded output and group. Shared stored-value references may identify
cached computations without a historical producing invocation. Resolve row/group
identity and membership from retained metadata or stored results as appropriate;
do not require snapshots of otherwise unstored value tables.

**Accepted enforcement and ownership.** Use one shared path from validated outputs
and resolved execution records to provenance construction. Ordinary construction
invariants, scoped primary and foreign keys where applicable, and transactional
writes of related records enforce structural relationships. Verify the construction,
storage, and retrieval behavior with ordinary isolated tests. Do not add a separate
provenance revalidation subsystem, recursive integrity sweeps, or repeated producer
validation in storage or inspection. Independent external-input boundaries,
including cache reads and public interfaces, retain their existing validation.

These protections address structural consistency. Authors still own calculation
correctness, the intended owner of a finding, key stability, determinism, partition
independence, and complete dependency and origin declarations. An author may return
a structurally valid row with an unintended supplied owner; Lyra cannot infer that
semantic mistake. Broken generated relationships indicate an implementation or
store-integrity failure, not another author-facing assignment mechanism.

**Accepted inspection outcomes.** Shared reference parsing rejects malformed caller
references as validation errors. A well-formed caller reference without a matching
target returns not found. A broken internal link in an existing retained trace is
an integrity error. The existence of a similarly keyed record in a different scope
does not satisfy that link. Inspection uses ordinary lookups and reports failures
encountered, without rerunning handlers, acquiring external data, or fabricating
records to repair the trace. Exact reference syntax and error models remain
implementation choices within the shared contracts.

Intentionally unstored intermediate values are unavailable while their required
identity, membership, and context remain resolvable. A completed empty group has
no rows; a skipped or reused computation has no new handler invocation. A request
covering zero locations can retain its context without partition or row records.
These are valid states. Historical cache-producer records are outside the guarantee.

**Resolution.** String key types, named-output completeness, automatic metadata
construction, and the shared integrity and inspection rules above settle R7.
Exact row contributions and semantic completeness remain outside the trace contract.
Coordination between durable provenance and result publication, including the
calculation's outcome when provenance persistence fails, remains under R10.

### R8. Parameter normalization across the API and worker

**Resolved — accepted contract.** Affects items 5, 7, and 8.

The API-to-worker parameter handoff and omission of supplied/default origin
tracking are accepted. Represent parameter forwarding through references to the
immediate source node context and parameter, and treat each named parameter as one complete
value, including nested objects and lists. Together, the constraints below
resolve parameter normalization, transport, and binding provenance for the
initial implementation.

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
their defaults. Cache keys, execution, and traces use the same effective values.
No custom restoration machinery or additional validator-behavior checks are
required.

**Accepted constraint — simplified parameter provenance.** Record effective
values without distinguishing explicitly supplied values from defaults. Do not
retain supplied-field masks or `origin=default` metadata, including for nested
values. Named upstream input references and parameter-binding relationships remain
part of provenance; this simplification does not remove those relationships.

**Accepted constraint — immediate parameter bindings.** For each forwarded named
parameter, store its immediate source node context and parameter name alongside
the receiving node context and parameter name in the inspected request. Use
references to specific node uses, not node names alone. Contexts distinguish
execution from cache reuse or skipped work; they do not require a historical
cache producer or imply a handler invocation. Recover longer forwarding chains by
following these links during inspection; do not duplicate ultimate-source references. Lyra records the
bindings it already resolves without requiring additional author declarations.

Each named parameter is one provenance unit, even when its value is a nested
object or list. Retain the complete effective value without tracking separate
origins for nested fields or list elements. This records actual binding
relationships without inferring influence inside validators or restoring
supplied/default origin distinctions. For A's `search_radius` forwarded to B's
`radius`, then to C's `distance`, record B-to-A and C-to-B binding references.

### R9. Traceability and automatic route selection

**Resolved — normal routing followed by automatic eligibility.** Affects items 1,
8, 10, 14, and 18.

Select routes using the existing shortest-route rules, explicit tie preferences,
and ambiguity handling. Determine trace eligibility from the selected graph
afterward. Traceability does not filter route discovery, favor a longer route,
alter tie preferences, or resolve an ambiguous route.

If the selected graph contains an unpartitioned metric, execute the otherwise
valid calculation and report complete tracing unavailable with that node and its
path. Do not reject the calculation solely because tracing is unavailable. Missing
required ownership or row-key declarations remain invalid definitions; routing
ambiguity and other plan errors retain their normal failure behavior.

Clients submit one request type without a tracing-mode or require-a-trace flag.
Eligible executions automatically retain current-execution provenance; result
interfaces advertise trace availability only after the required current records
are retained and resolvable. Cache hits follow R2's values-only contract. Durable
publication remains under R10.

### R10. Durable trace access from results and local executions

**Open — specification gap.** Affects items 8, 11, 12, 13, 14, and 22.

Inspection follows row and completed output-group references, but the contract
does not explain how a caller obtains those references from result interfaces,
especially when a result combines computed and reused groups, contains empty
groups, or covers zero locations. Item 11 fixes stored-result identity and value
publication without settling the caller's access to current-request trace
references. Local execution and API/worker execution share the SQLite implementation
accepted in item 11. Store setup, reference access, and publication of results and
their automatically determined trace availability still need specification.

**Accepted constraint.** Stored identities and trace relationships remain
resolvable independently of transient job records within the supported current
store. Cached values, structural output scopes, and cache entries are published
transactionally under R2. Trace availability requires the complete supported
current-execution metadata to be durably retained and resolvable; graph eligibility alone is insufficient. The
publication sequence and behavior on provenance-storage failure remain open.
Completeness covers metric rows, operation mappings, completed
groups including empty outcomes, and their current execution or reuse under item 4.
Historical cache-producer records, exact row-contribution information, and
rejected-candidate explanations are outside that scope.
Compatibility with results across incompatible code or schema changes is not
required.

Inspection follows R7's accepted distinction between intentionally unavailable
values, valid empty/reused/skipped states, unknown caller references, and broken
internal trace links. That distinction does not settle when results are published
or whether provenance-persistence failure fails the calculation.

**Decisions needed.** Specify:

- Whether result interfaces expose row references directly or support lookup by
  table reference and row key, including assembled results.
- How result interfaces expose or resolve completed metric and operation
  output-group references, including groups with no rows, and access to the
  current-request context for a result covering zero locations.
- How local executions configure and initialize the SQLite store for automatic
  provenance retention, and how callers subsequently resolve their references.
- When a calculation result and its trace availability may be published relative
  to durable storage of complete, resolvable current-execution metadata, including whether
  a provenance-storage failure fails the result or permits success with a trace
  reported unavailable.
