# Capability layer decision ledger

Target design for compact public capabilities over explicit, edition-aware
execution graphs. These decisions describe planned behavior, not implemented APIs.
API spellings identify concepts and do not impose compatibility requirements.

## Accepted

1. **Compile public capabilities from node declarations.** Keep contracts and
   exposure metadata alongside metric and location-operation definitions. Lyra
   generates discovery, schemas, routing, validation, and graph mappings from those
   declarations. No separate publication API or per-metric route approval is
   required. Canonical routing avoids wrapper metrics for each source location type.
   This avoids duplicate authoring and drift between interfaces.

2. **Give each named location input one concrete type.** Nodes may have several
   inputs, each with a fixed contract. Reusable references identify the location
   family, schema, and concrete source/geographic edition bindings. Sharing a
   geographic unit identity does not make different source contracts interchangeable.
   Bindings propagate supplied types and validate compatibility. For provider-backed
   locations, node declarations and provider registration reference the same
   contracts; handlers receive ordinary resolved location values. Authors share
   implementation through functions and factories. Concrete contracts make domain meaning explicit
   without duplicating schemas, metadata, or calculation code.

3. **Distinguish subject selection from calculation methodology.** Public inputs
   select the locations being evaluated. Internal dependencies define relationships
   required by the calculation, such as streets surrounding a scored block.
   Canonical routing supplies public inputs without replacing those explicit
   bindings. Alternative selections preserve the metric's declared output meaning
   and granularity; selecting a state for tract population still returns tract rows.

4. **Keep canonical location selectors optional.** Authors explicitly mark
   operations canonical to participate in automatic routing. A canonical operation
   has exactly one named location-set input and one location-set output. Resources,
   context, fully bound internal dependencies, and fixed/default parameters are
   allowed; dependencies must be satisfiable from that input and declared bindings
   without additional caller arguments. This makes each operation a typed routing
   edge. Explicit internal dependencies may invoke noncanonical operations, which
   do not independently become eligible for automatic routing. Plugins may declare
   no canonical operations; other enabled plugins may still supply compatible
   routes. For an input with no canonical conversions available in that catalog,
   callers must supply its native type. Authors may still bind derived selections
   inside their graphs.
   Canonical registration enables shared selection convenience; it is not required
   for graph execution.

5. **Precompute deterministic routes over the enabled catalog.** Select the
   compatible canonical path with the fewest operations, counting each operation
   equally. Matching concrete source and target contracts use a zero-operation path;
   matching geography alone is insufficient. For each reachable compatible pair,
   multiple shortest paths require an explicit route preference, shared across
   metrics. Compute routes when the complete catalog is assembled for the application
   or local runner. Multiple paths are permitted, allowing optimized database-backed
   shortcuts without making Lyra responsible for SQL planning.

6. **Express tie preferences as operation sequences.** Declare preferences at plugin
   registration using ordered operation references; manifests store stable
   operation identifiers. Infer the concrete source and target contracts from the
   sequence, including edition bindings. A preference applies to that pair across
   metrics, not as a global operation priority. Validate canonical status, compatible
   connections, shortest-path length, and absence of conflicting preferences.
   Invalidated preferences cause catalog errors rather than being silently ignored;
   a preference that still names the unique shortest path remains valid. This keeps
   exceptional choices explicit without repeating type declarations or adding weights.

7. **Allow cycles in the canonical conversion graph.** Opposite-direction
   conversions are independently declared; neither implies an inverse relationship.
   Equal positive operation costs yield finite shortest routes without repeated
   cycles. Detect reachability and shortest-path ambiguity without enumerating all
   possible paths. Automatic routing never expands a same-type selection through a
   round trip. The selected route plus internal calculation dependencies must still
   form an acyclic execution plan. This supports bidirectional geography without
   requiring a hierarchy or permitting recursive execution.

8. **Treat routing as behavior, not an equivalence claim.** Alternative paths need
   not produce the same locations. Canonical means the selected default under the
   routing policy. Adding a shortcut can change results as well as performance.
   Record the selected path and keep it fixed during execution; failures or warmer
   caches on other paths must not trigger rerouting. Structural validation cannot
   prove equivalence between arbitrary operations, and authors are not required to
   establish it.

9. **Use batched set-selection semantics.** Canonical operations accept batches of
   one concrete source contract and return the union of their related target
   locations. Return each target identity once, in deterministic order. Public ID
   selections remain nonempty; empty intermediate selections pass through normal
   execution, and downstream handlers must support them. Lyra does not fabricate
   empty metric results because counts, summaries, and multi-input calculations may
   require other behavior. Authors can implement batches with native database
   queries rather than per-ID calls. Operations return complete location rows under
   their declared target contract, allowing a single optimized query to supply IDs,
   geometry, and attributes without mandatory separate acquisition calls.
   Selection does not imply grouping or aggregation; calculations needing
   source-to-target membership must retain that association explicitly.

10. **Keep v1 public requests canonical-only across all interfaces.** Ordinary local
    execution, REST, and MCP accept a public metric, an edition when applicable,
    declared ordinary parameters, named source ID selections, and the optional
    run-wide cache revision. Selections identify collection and unit type. Resolve
    each selection to its required target contract through canonical routing. Input names
    retain their native subject meaning regardless of the selected source type.
    Requests do not name operations or supply pipelines, so no selection-language
    discriminator is needed. Unsupported relationships fail rather than being
    approximated. Author-declared graphs and development interfaces may still use
    noncanonical operations. This keeps the shared public contract small.

11. **Allow private intermediate metrics.** Metrics are public by default;
    `private=True` excludes them from discovery and direct public submission while
    preserving execution, caching, dependencies, and relevant graph inspection.
    Local development/test access remains possible without publication. Privacy
    controls entry points, not secrecy. The flag applies only to metrics; canonical
    status controls location-operation routing. This keeps cached intermediates out
    of ordinary discovery without losing their relationships.

12. **Separate geographic identity from source observations.** Unit identity
    includes collection, unit type, geographical edition, and ID. Equal IDs across
    different geographical editions imply no correspondence. Observations sharing
    that full geographic identity may describe the same unit while having different
    attributes or definitions. Source edition, observation period, release, and
    geographical edition remain distinct: several periods may use one geography.
    Source-contract identity must therefore remain separate from unit identity to
    prevent accidental data substitution or inferred joins across different
    geographical editions.

13. **Require a shared structural schema within each edition family.** Declare
    columns, types, nullability, structural constraints, ID/geometry conventions,
    and units once. Metric families share named native input roles and location
    families, named output schemas, and the complete ordinary caller-parameter
    contract: names, types, requiredness, nullability, defaults, bounds, allowed
    values, and other declared validation constraints. Validate that shared contract
    during compilation so family inspection can expose one authoritative parameter
    schema. Concrete edition bindings differ without changing those shared contracts.
    Values, boundaries, available IDs, periods, provenance, statistical definitions,
    and concrete dependency bindings may differ. Source attributes may remain
    properties of provider-returned locations. Preserve field definitions in source
    and result metadata: equal structure enables reuse without establishing
    comparability. Factories may vary fixed internal bindings and methodological
    constants, with their meanings documented in edition metadata. Variants needing
    incompatible public parameter contracts must use separate metric families or
    deliberately adopt a common contract supported by every edition.

14. **Instantiate concrete edition graphs through Python factories.** Authors
    explicitly register supported editions while sharing graph-building and
    calculation code. Instances have distinct identities and bindings; absent shared
    dependencies, they are disconnected execution components. Corresponding nodes
    retain their registered family association as metadata, not a dependency edge;
    equal schemas alone do not merge nodes into one family. Explicit specializations
    handle edition-specific behavior without year branches
    throughout handlers. Bind dependencies through concrete producer/output
    references, preserving edition identity rather than resolving an unqualified
    output name across variants. Factories use ordinary Python organization without
    a required graph-bundle abstraction. Authors declare output meanings rather than
    relying on inference from arithmetic.

15. **Allow editions to be omitted.** An uneditioned capability normalizes to one
    implicit variant, with no public selector or mandatory factory. Explicitly
    editioned capabilities require selection even with one named edition; there is
    no implicit latest. Behavior follows declarations, not installed variant counts.
    Relevant temporal metadata still describes the data. Edition selection resolves
    concrete source and geography bindings, rather than becoming an ordinary handler
    parameter or being repeated in every location selection. For each named input
    and accepted source collection/unit type, the selected metric variant must
    identify exactly one concrete source contract. Reject ambiguous bindings during
    catalog compilation rather than guessing an edition.

16. **Validate and bind requests before acquiring data.** Local execution, REST, and
    MCP share catalog-based preparation: validate public metric identity, edition,
    input and parameter schemas, nonempty ID selections, and accepted source types.
    Bind the concrete metric, a canonical route for each named location input,
    effective parameters, and relevant location revisions before local data access
    or remote enqueueing. Persist accepted remote bindings; workers must honor them
    rather than silently replan against a changed catalog. Resolve IDs, validate
    acquired data, execute routes, and supply native inputs to ordinary dependencies
    during execution. Invalid plugin contracts, dependency cycles, and unresolved
    routing ties fail catalog compilation. This rejects statically invalid requests
    early while keeping source access in the executor's resource lifecycle.

17. **Preserve binding ownership after canonical preparation.** Resolve public
    selections into the metric's native inputs before ordinary dependency forwarding.
    `Input(...)` references retain their declaration owner and see those effective
    inputs, not the caller's original source selection. Bind canonical hops explicitly
    to the preceding result. Ordinary explicit bindings override matching-name
    forwarding, with producer defaults supplying remaining parameters. Canonical
    selectors use their own defaults/fixed bindings rather than inheriting metric
    parameters by name. Internal type mismatches require explicit bindings, not
    automatic rerouting. This preserves calculation methodology and existing
    forwarding semantics.

18. **Reuse the executor's native invocation cache.** Canonical selectors execute
    within the same resource scope and caching machinery as ordinary location
    dependencies. Resolve public selections by executing or validly reusing the bound
    selectors before the metric cache lookup. Key the concrete metric on effective
    parameters, complete ordered native location contents, native contracts/editions
    and data revisions, declared calculation dependencies, and the computation
    namespace. Revisions used only to select those native inputs do not become part
    of the metric key. Different public selections may therefore reuse a result when
    all effective native identities match; equal IDs alone are insufficient. A
    native dataset revision change still invalidates the metric even if its values
    happen to match. Reused native results receive the current execution's provenance,
    including its original selection, route, and pinned revisions. Cache complete
    ordered batches, with no per-ID, subset, or
    permutation-insensitive reuse guarantee. Set-selection semantics govern membership
    and uniqueness, not cache-key order independence. Hits may still require initial
    source acquisition and intermediate artifact reads. This reuses native caching
    without promising zero-I/O hits or incremental computation.

19. **Version location data separately from editions.** An edition identifies the
    intended dataset or geography; a data revision identifies its underlying data
    version. Corrections may advance a revision without changing geographic identity
    or introducing a user-selectable edition. Revisions also apply to implicit
    singleton editions and participate in provenance. Scope each revision to its
    concrete location data contract; equal revision strings do not identify the
    same dataset. Include declared revisions from native location contracts and
    transitive declared dependencies before cache lookup. A location-producing
    operation also includes its target dataset's revision when present, so target
    or membership updates invalidate it even if source inputs are unchanged. Do not
    collect revisions from every possible canonical route. Revisions used solely
    for public canonical selection stop at the resolved native-input boundary;
    revisions of native datasets and explicit calculation dependencies still apply.
    This does not discard relationships required by the metric's methodology.
    With an otherwise unchanged computation
    namespace, revision-only changes preserve unrelated cached results. Authors do
    not repeat transitive revision declarations. This makes location data revisions
    the normal data-invalidation mechanism without conflating maintenance with
    dataset selection.

20. **Keep explicit data revisions on location definitions.** Bundle source metadata
    and revisions with location definitions, without a separate author-facing source
    abstraction. Revisions are optional for both named and implicit editions.
    Omission means unversioned: keep normal content-based caching, record the absence
    in provenance, and use the run-wide cache revision when external changes require
    invalidation. Do not invent a default revision or imply automatic freshness.
    Production of purely derived locations is invalidated through declared
    dependencies and needs no independent revision. An explicit revision covers the location dataset's geometry,
    attributes, and any relationships included in that dataset, even when stored
    across several files or tables. Authors must define this coverage accurately;
    unrelated data read by a handler is not implicitly covered. Metrics and location
    operations inherit relevant location revisions but declare no independent
    revision fields. Shared values can use ordinary Python constants or factories.
    This accepts coarser invalidation in exchange for simpler authoring and adds no
    code-version tracking mechanism. Exact class names, field names, and declaration
    syntax remain implementation choices.

21. **Retain an optional run-wide cache revision.** `cache_revision` contributes an
    additional identity component alongside location revisions and computation
    identity; it never replaces them. A fresh value gives all nodes in that run a fresh
    namespace, and subsequent runs with that value may reuse it. This is a per-run/job
    control, not a server-wide purge; existing entries remain available under their
    previous identities. External changes not covered by declared location revisions
    use this broad invalidation mechanism in v1, including unversioned datasets.
    It also supports deliberate
    recomputation and unversioned implementation changes without requiring exhaustive
    automatic change detection.

22. **Pin location data revisions for each execution.** Resolve declared revision values from
    catalog metadata when preparing a local execution or accepting a remote job.
    Use those bindings consistently for data access, caching, and provenance.
    Fail clearly if execution cannot honor them rather than substituting newer data. Authors and
    providers are responsible for revision identifiers accurately describing the
    accessed data; pinning metadata neither detects unreported changes nor creates
    a storage snapshot. Historical data retention is not required; rejecting an
    outdated queued job is sufficient for v1. This avoids executing
    changed data under an earlier cache identity without adding snapshot management.

23. **Precompute immutable execution metadata.** Compile route bindings and reuse
    computation namespaces and dependency revision identities for fixed catalog,
    provider, and revision bindings instead of serializing and hashing the graph at
    every cache lookup. Reuse immutable location snapshots and their fingerprints
    within a run. Retain the existing catalog-wide namespace for graph contracts and
    installed package versions; this adds no source-code hashing or independently
    authored node versions. Keep location revision values outside that structural
    namespace and incorporate them into affected invocation keys separately. Changing
    only those revisions must not invalidate the whole catalog; concurrent contract
    or package-version changes may do so. This limits steady-state overhead while
    separating data invalidation from the existing computation namespace.

24. **Separate compact discovery from metric inspection.** List and search return
    one entry per family with public variants; private variants are neither advertised
    nor publicly selectable. Entries include concise meaning, declared output
    granularity, and edition-selection information. Search also indexes compiled
    accepted source types and preserves edition-specific availability. Inspection
    provides input and output schemas, parameter constraints, units, definitions,
    and accepted location selections. For named editions, family inspection gives
    the shared contract and public edition choices; selecting an edition gives its
    exact executable contract. Uneditioned capabilities expose that contract directly.
    Keep dependency details out of ordinary discovery. The executable contract and
    result provide enough context for routine requests without inspecting graph
    structure or repeating whole edition graphs.

25. **Describe accepted selections through structured contracts.** For each named
    location input and concrete metric edition, compile its native type and accepted
    source collection/unit-type pairs from valid canonical routes, including direct
    native selections. Shared tool instructions explain ID selection and conversion;
    output meaning and granularity remain author-declared. Authors describe each
    metric and operation once, without additional descriptions for every accepted
    metric/route combination. V1 needs no compiled natural-language sentences or
    route-summary generator. Preserve operation descriptions as relationship
    metadata without inventing flattened containment or equivalence claims. This
    keeps discovery deterministic and avoids duplicate authoring and semantic
    overreach without requiring an agent-facing route explorer.

26. **Keep failure semantics consistent across interfaces.** Preparation failures
    reject requests before source acquisition or job creation. Unknown requested IDs
    fail execution and identify the affected input, source scope, and unresolved IDs;
    do not silently discard them. An existing source unit with no related targets
    produces a valid empty selection and continues normally. Local calls raise
    preparation or execution errors at the corresponding stage. Remote failures
    after acceptance belong to the job and are reported as failed status; successfully
    inspecting that status is a successful inspection request. Shared semantic error
    categories keep clients consistent without fixing exception names, error codes,
    or transport mappings.

27. **Return native outputs with inspectable execution provenance.** Local calls
    return native named tables, with bound metadata available separately for local
    inspection. Remote interfaces retain job handles, polling, bounded previews, and
    authenticated full-result access. Ordinary result views identify the public
    metric, selected edition, and output metadata. Detailed provenance retains
    validated original selections, effective parameters, concrete metric/contract
    identity, per-input routes, pinned location revisions, the optional run-wide
    cache revision, and resolved native-input summaries such as counts and content
    fingerprints. Preserve relevant source and release distinctions. Distinguish
    the accepted plan from observed execution and cache reuse; planned or unobserved
    dependencies must not be reported as executed. This does not require timing
    traces or replayable snapshots. Intermediate tables and geometries need not be
    embedded in responses. Retaining these records does not require an MCP explorer
    for every execution detail. This supports interpretation without imposing a remote job
    envelope on local callers or promising historical data availability.

28. **Limit v1 MCP graph access to shallow dependency inspection.** Provide one
    bounded, on-demand lookup for a concrete metric variant, returning direct
    declared dependencies and consumers, private-node markers, and relevant producer
    outputs and consumer input names. Return only direct neighbors, with a finite
    response limit and explicit indication of omitted relationships. Include
    operations when they are declared calculation dependencies; possible canonical
    selection routes are not additional dependencies of every metric invocation.
    Tool guidance reserves this lookup for
    questions about calculation structure or relationships, not as a prerequisite
    for execution or interpreting successful results. Ordinary results should not
    prompt graph inspection, and failures should identify relevant inputs, scopes,
    or unavailable revisions directly. This preserves structural context for agents
    while discouraging unnecessary investigation of routine calculations.

## Deferred

- **Edition-dependent public parameter contracts.** Per-edition defaults,
  requiredness, and validation constraints would require conditional schemas and
  more inspection context. V1 uses a shared parameter contract for each family.
- **Caller-selected operations and richer selection syntax.** Named-operation
  overrides, nested expressions, predicates, and caller-authored pipelines enlarge
  the public contract. Canonical selection plus author-owned dependencies covers v1.
- **Operation-discovery tools for constructing selections.** Agents cannot request
  operations in v1. Operations remain inspectable as part of dependency reasoning,
  without a separate selector-search interface.
- **Advanced MCP graph and execution inspection.** Recursive graph expansion,
  arbitrary path queries, full canonical conversion-graph exploration, per-node
  execution traces, timing and cache-hit inspection, and interactive job-plan
  exploration are deferred. The backend retains graph relationships and agreed
  provenance; compact contracts, results, and shallow dependency inspection suffice
  for v1 without inviting agents to investigate every execution detail.
- **General graph synthesis and cost-based routing.** Canonical location routing is
  the bounded composition mechanism. General metric composition, adaptive planning,
  and query-cost estimates would introduce unrelated complexity.
- **Further caching optimizations.** Per-location incremental caching, reuse of
  batch subsets, narrower invalidation for code/contract changes, and request-level
  shortcuts that bypass input resolution are deferred. Native invocation caching
  with dependency-aware location revisions is sufficient initially and preserves the
  established input-identity rules.
- **Broader revision tracking.** Independent revisions for external data outside
  location contracts and new code-version tracking mechanisms are deferred. Location
  data revisions and the optional run-wide revision cover v1 without adding source
  abstractions or per-node revision declarations.
- **Cross-edition comparison workflows and impact modelling.** Preserve identities
  and dependencies for future work. Correspondence, statistical comparability,
  causal effects, and sensitivity cannot be inferred from graph edges or schemas.

## Implementation discretion

Within the accepted behavior, implementation may choose declaration syntax, model
layouts, identifier serialization, registration layout, inspection and local
development interfaces, numeric response limits, error names, and transport mappings.
These choices are not reasons to defer accepted capabilities. Examples do not
mandate class names, constructor/decorator signatures, or container shapes. The
accepted behavioral requirements remain fixed across those implementation choices.

## Scope boundaries

- **Source preparation belongs to providers or external tooling.** Lyra validates
  contracts; it does not supply ETL orchestration, adapter registries, transformation
  specifications, or schema reconciliation. Sources unable to satisfy a family's
  common contract belong to another family.
- **Geographic and statistical harmonization remain external.** Crosswalk generation
  and reconciliation of changing definitions are separate from edition support;
  geographic correspondence does not establish statistical comparability.
- **MCP remains consumer-facing.** Graph inspection adds no plugin, worker, queue,
  or server administration.
- **Design cleanly for closed alpha.** No migrations, compatibility shims, or
  preservation of existing interfaces are required.

## Performance validation during implementation

Use the permanent [benchmark suite](../benchmarks/USAGE.md) to understand the
performance impact of coherent, runnable changes. Its coverage is organized around
application behavior and remains useful after this capability-layer work.

Before substantial implementation, capture a clean-commit baseline for existing
behavior. Repeat measurements when practical to understand variability, retaining
raw samples and environment information. Compare useful checkpoints with both that
baseline and the previous comparable checkpoint.

Suggested checkpoints and measurement priorities:

| Runnable area | Useful measurements |
| --- | --- |
| Catalogs, contracts, editions, and routing | Compilation time and memory as catalogs grow; edition families, reachability, cycles, and route preferences. |
| Preparation and canonical selection | Native and routed selections, batching, multiple inputs, and rejected requests; preparation separately from acquisition and execution. |
| Caching, revisions, and immutable metadata | Reuse of equivalent native inputs, affected and unaffected revisions, identity and fingerprint reuse, and provenance on cache hits. |
| Public interfaces and results | Family discovery, edition inspection, private-node filtering, shallow dependency inspection, accepted remote bindings, and result projection. |
| Completion review | Cumulative impact, material regressions or tradeoffs, and changed or incomparable workloads. |

These are flexible checkpoints, not a prescribed implementation sequence. Combine,
reorder, or subdivide them to fit the work. Choose relevant subsets and representative
sizes or graph shapes according to the changes and observed risks. A full run after
every implementation step is unnecessary. Adjust frequency to measurement cost,
noise, and development needs; add cases when implementation or profiling reveals
new risks.

Incomplete transitions may temporarily prevent measurements. Record meaningful
gaps and revisit them when useful without making benchmark availability a barrier
to continued implementation. New capabilities begin their own measurement histories;
there is no need to maintain a parallel stable implementation. Adapt benchmark
invocations as APIs evolve, preserving equivalent workload definitions where
possible and distinguishing changed measurement semantics.

Cheap isolated correctness tests can protect structural performance intentions,
such as preparation without acquisition, shared dependency reuse, immutable
fingerprint reuse, and selective invalidation. Timing thresholds are not a substitute
for these behavioral checks. Review material differences using repeated observations,
absolute costs, ratios, and workload context rather than a fixed regression limit.
Summarize significant tradeoffs and comparison gaps alongside implementation progress.

After implementation, continue ordinary main-branch and manual benchmark runs using
the same suite and retained history.
