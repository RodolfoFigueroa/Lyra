# 10. Execution reports and durable success

Automatically explain each successful managed execution at the graph and node
level, and make a complete report part of successful result publication. Earlier
milestones provide a shared executor with normalized requests, resolved graph
bindings, validated results, and actual execution/cache outcomes. Their value store
publishes complete immutable values; this milestone adds independently retained
execution records and the final result/report publication guarantee.

## Accepted scope

### One report contract for all managed execution

Generate reports for successful local, development, and remote executions through
the shared runtime, including location-scoped, unpartitioned, and hybrid graphs.
Clients submit one request type with no reporting mode or retention switch.
Authors return ordinary tables or operation mappings, without a lineage payload
or reporting wrapper. Output tables acquire no mandatory reporting columns.

Managed requests contain parameters and registered provider/ID selections.
Providers and managed provider adapters acquire records; metrics may consume
declared upstream tables. Operation handlers compute from their sole source,
optional candidates, and optional typed parameters; selectors receive that source,
the symbolic target relation, and the same effective parameters when declared.
Neither callback acquires external resources directly.
`run()` selects public metrics, while `run_development()` can select private
concrete roots before the shared preparation path. Direct handler calls and caller
injection of full records, arbitrary tables, or stored dependency replacements
are outside this managed report contract.

A report retains:

- The normalized request and durable execution ID.
- The selected graph and route, named input/output and parameter bindings, node
  kinds, output scopes, and enough context to distinguish separate resolved uses
  of the same definition.
- Each named location input's provider scope and the actual
  owning scope of its location-scoped outputs, including empty selections.
- Effective parameter values, including defaults and nulls, without
  supplied/default flags or origins for nested fields and list elements.
- Relevant implementation and deployment-build revisions and declared
  external origins, including their informational descriptions.
- Observed execution, cache reuse, skipped work, and final status.

Expose the normalized request through `report.request`, with the selected root
metric, complete caller selections, and effective root parameters. Routing and
cache reuse do not replace or narrow those selections. Derived inputs remain in
the resolved graph bindings rather than becoming request inputs.

A resolved node use is a use of a definition with particular bound inputs and
effective parameters. Distinguish two uses of the same definition in the selected
graph without inventing public row/group or parameter-origin reference objects.
Expose one summary per resolved use in `report.nodes`; several consumers of the
same resolved use share that summary, which reflects its actual observed work.
`report.root_use_id` identifies the root entry. Named data bindings link producing
uses through these same report-local IDs.
Report fields beyond those sketched below, physical persistence layout, and
transport-envelope names remain implementation choices.

Reports distinguish, for example, two uses of a block metric with different ID
selections or parameters under the same declared provider requirement. Retain the
provider ID, code revision, and origin identities for each input, including cached
and skipped work, without acquiring records to populate metadata. Operation targets
retain their declared bindings independently of the selected source provider.

Use the normalized request, resolved plan, declarations, and actual outcomes to
construct this information. Retain associated final-output retrieval descriptions
alongside the report. Reports describe the normally selected route without changing
shortest-route rules, preferences, ambiguity, or definition validation.

Effective values come from ordinary Pydantic normalization: supported JSON scalars
and containers, including all defaults and nulls. The API queues these values and
the worker revalidates them under the author's deterministic, idempotent, I/O-free
validator contract. Report those same values used by execution and computation
keys. For A's `search_radius` forwarded to B's `radius`, then C's `distance`, retain
the named bindings and each node's values; do not track whether a value was
explicitly supplied or defaulted, or split nested objects into influence records.
Store direct named forwarding in `report.parameter_bindings`, using the existing
report-local source and target use IDs.

External origins have an ID, optional opaque revision, and optional description.
Collect them directly from metric and provider declarations, deduplicating by
`(id, revision)` before resource acquisition or cache lookup. Managed adapters use
their provider's declared origins. Resources retain their I/O role. Operations
have no direct origins; retain their target provider scope and declared input
bindings. Provider scope contains the provider ID, code revision, and origin
identities. Several providers can share an origin while retaining separate scopes.
`revision=None` means fixed unversioned data. Keep declared origins for cached and
skipped nodes without initializing resources to collect them. Expose direct
declarations in `node.origins` and the definition's declared implementation
revision in `node.code_revision`. Record deployment-build identity separately in
`report.build_revision`, using `None` for local execution without such a revision.

All valid graph scopes receive reports. Location-scoped metrics still require
non-null string `location_id`/`row_key` schema declarations, ownership membership,
and composite uniqueness; unpartitioned outputs use whole-table validation.
Missing structural declarations remain definition errors, not a scope downgrade
or a reason to select a different route. Plan validation precedes resource use.

### Honest outcomes and limited explanation scope

Record handler execution only when it occurs. Observe exact-ID resolution and
managed target-query acquisition at their respective runtime boundaries. Distinguish
selector construction, target acquisition, and mapping execution within the resolved
operation use; constructing an Ibis expression is not external acquisition. An
executed query with zero matches is still acquisition. Cached or bypassed work
must not claim fresh acquisition. Direct origins describe a declared boundary,
not proof of which sources an arbitrary metric handler actually used.

Associate target acquisition with its provider scope and configured adapter,
while the operation's code revision covers its selector. Operation entries expose
these target descriptions and stage observations through `node.operation`.
Retain observed executed/bypassed outcomes without storing serialized Ibis
expressions, compiled SQL, credentials, or candidate membership solely for reports.
Record an executed query honestly even if some returned records were already
cached. A valid operation or downstream hit must not construct a filter or execute
a query to enrich a report.

A node can both execute and reuse partitions. Represent that mixture without a
durable per-partition or per-row event history. A shared dependency executing for
another consumer is not globally skipped; describe only work actually bypassed as
skipped. Declared graph bindings do not imply their values were acquired in the
current execution, and hits do not require following historical cache producers.

For example, a node reusing A/B and computing D has a mixed outcome. Its report
describes the current resolved use and observed work, without storing a separate
event for every partition or claiming the original producer ran again. A completely
reused result can bypass a node whose own cache setting is off. Reporting must not
force that node to run or invalidate a valid hit to obtain acquisition details.

Empty results and zero-location requests still receive execution IDs and reports.
Do not invent rows, partitions, provider acquisitions, or handler invocations to
make those reports appear populated. Failure remains distinct from completed
empty success.

Retain enough descriptive metadata to read the report within the supported
current store after jobs expire. Reports and stored values are independently
owned: no inverse producing-invocation link is required on values. Preserve row
identities, partition completion, mappings, and computation references where
execution, caching, or ordinary retrieval needs them, without additional retention
solely for explanation. Uncached intermediate values remain in memory unless
ordinary retrieval requires persistence.
The same immutable values may serve many executions, each with its own execution
identity and current report. Cache matching uses declared computation/record keys,
not report IDs, calculation times, or historical producers. A provider's record
identity is provider scope plus location ID; metric and operation keys additionally
represent their implementation, effective parameters, and bound input/origin
identities. Current reporting does not add another cache invalidation token.

Build scope, identity, and original-selection descriptions from runtime bindings
and stored result metadata. Handler-owned DataFrame attributes and local table
edits cannot rewrite those descriptions. Tables exposed to callers are independent
DataFrames or GeoDataFrames. The local `RunResult` from
[milestone 6](06-unified-graph-execution.md) keeps `.tables` and `.metadata`
associated with that execution; report and output descriptions remain separate
from the frames themselves.
No location-value wrapper or handler metadata-inspection interface is required.

Construct reports and result descriptions with ordinary Pydantic models under
the explicit ownership rules from
[milestone 4](04-plugin-discovery-and-normalized-jobs.md). Detach mutable metadata
when capturing it for a report and when returning it to callers. Retained reports
remain private; field access does not deep-copy their contents. Changes to nested
parameters, bindings, or descriptions in a caller's returned report cannot alter
the retained report, result access, or later retrievals. Serialize descriptive
bindings from the resolved runtime state without reconstructing executable
dependency declarations.

### Durable publication and access

Use the same SQLite store for local and API/worker reporting. Managed local runs,
including development, use the `result_store_path` configuration, persistent
default, and path-resolution rules established in
[milestone 8](08-sqlite-result-storage.md). Lyra creates and initializes the store
as needed; authors do not create tables manually. Local result metadata retains
the resolved `store_path` and `result_ref` so callers can reopen the result.

Before returning local success or publishing remote success, persist every
required final value, its retrieval description, and the complete execution
report. Publish the final result description and report together transactionally
after required values are durable. A partial report cannot be a successful run's
final report, and an inspection size limit never truncates what is retained.

Required value storage or final result/report publication failure fails the
execution, even when calculation finished. Local `run()` and `run_development()`
raise `ExecutionPersistenceError` from `lyra.contracts.errors`, exposing the
attempted execution's `execution_id` and chaining the underlying exception.
They return no `RunResult`; that ID does not guarantee a retained report exists.
Remote executions use the existing failed-job path, with clients continuing to
receive `MetricRunError`. Calculation errors retain their existing behavior.

Retained `ExecutionReport` records represent successful executions only, with
`status="succeeded"` and a required `result_ref`. Failed or interrupted attempts
use the existing error paths; durable failure reports and partial-execution report
models are deferred. There is no successful fallback with an unavailable report.

Do not hold a database transaction open while handlers run. Complete cache values
may already have been committed and remain reusable after later report publication
fails. Their presence does not publish a successful run. No cross-service
transaction with the queue or historical producer reconstruction is required.

Run/result interfaces expose the durable execution ID alongside ordinary outputs.
Extend the local `RunResult` with `.metadata.execution_id` and an eagerly attached
`.report`, an ordinary `ExecutionReport` Pydantic model, while preserving its named
native tables and attached metadata. Return a detached complete report after
durable publication; reading `.report` performs ordinary attribute access.
Provide shared read-only report lookup and full local Python access, independent
of transient RQ/job records. The ID, report, and associated final-result access
remain usable within the current store after job expiry. The next milestone adds
the complete public transport inspection/download interfaces.

Retain reports, final descriptions, and all stored values indefinitely within the
supported current store. Expired jobs, changed keys, and changed cache policies do
not delete them. Storage growth is accepted. Closed alpha requires no migrations
or historical readers across incompatible code/schema changes; an explicit
development-store reset is allowed. This does not weaken access within a supported
store or make transient job lifetime the retention policy.

Read-only lookup shares strict reference parsing: malformed identifiers fail
validation, valid identifiers without targets are not found, and broken required
relationships are integrity errors. Report lookup exceptions share milestone 8's
`ResultAccessError` base and expose the requested execution ID. Errors follow the
requested lookup: a missing report required by an existing result is a result
integrity failure. Do not reacquire external data or recreate missing values to
answer a report lookup. Intentionally unstored intermediates do not make a report
incomplete.

Preserve execution-scoped and resolved-node-use construction invariants, scoped
keys where applicable, and transactional relationships. Reuse validated data
without another producer-validation pass, separate report revalidation subsystem,
or recursive integrity sweep.

## API sketches

### Execution identification and attached reports

With the `block_area` metric and its provider registered on the runner, ordinary
local execution returns the native tables, execution metadata, and complete report
together:

```python
arguments = {
    "blocks": {"provider": "city_blocks", "ids": ["block_1", "block_2"]},
}
result = runner.run("block_area", arguments)

area = result.tables["area"]
execution_id = result.metadata.execution_id
report = result.report

assert report.execution_id == execution_id
assert report.result_ref == result.metadata.result_ref
assert report.status == "succeeded"
```

For a successful execution, `report.execution_id` matches the result metadata and
`report.result_ref` is required and identifies that execution's retained
final-result description. Its `status` is `"succeeded"`.
The execution ID addresses the report; the result reference addresses the retained
result. Empty and fully cached executions expose the same structure, each with
its own execution ID and result reference. `run_development()` returns this same
structure under the shared managed-execution contract.

The complete `ExecutionReport` is attached eagerly as a detached value after
successful persistence. Accessing `result.report` performs no storage lookup and
does not copy the report on each field access. The report remains associated with
this execution when another run starts on the same runner.

### Normalized request

`report.request` is an ordinary descriptive model with these fields:

| Field | Meaning |
| --- | --- |
| `metric` | The selected concrete root metric's name. |
| `inputs` | Caller selections keyed by input name, each exposing `provider` and `ids`. |
| `parameters` | Complete effective root parameters, including defaults and nulls. |

With milestone 6's `count_large_blocks` metric and its dependencies registered:

```python
result = runner.run("count_large_blocks", {
    "blocks": {"provider": "city_blocks", "ids": ["A", "B"]},
    "parameters": {"minimum_area": 2000.0},
})

request = result.report.request

request.metric                     # "count_large_blocks"
request.inputs["blocks"].provider   # "city_blocks"
request.inputs["blocks"].ids        # ["A", "B"]
request.parameters
# {"minimum_area": 2000.0, "include_requested_total": False}
```

The request retains the complete accepted A/B selection even if only B needs
computation. When routing converts a caller's blocks into counties, it still
records that source block selection; resolved bindings describe the route.
Only caller-selectable inputs appear in `request.inputs`. The operation-derived
`selected` input in this example appears in the graph bindings.

Use the existing request normalization and metadata ownership rules. At return,
`request.parameters` matches the root node's effective parameters and
`result.metadata.parameters`. Retain no separate raw-request copy or
supplied/default flags. Caller mutations cannot alter the retained request or
later reads. This description introduces no replay method or guarantee of
reproducing results against changed registrations.

### Node uses and observed outcomes

`report.nodes` is a list of summaries, one per resolved use of a definition.
Each summary exposes these fields:

| Field | Meaning |
| --- | --- |
| `use_id` | Opaque identifier unique within this report. |
| `node_id` | The metric, operation, or provider definition used. |
| `kind` | The node kind. |
| `outcome` | The observed execution/reuse summary for this use. |

```python
for node in result.report.nodes:
    print(node.use_id, node.node_id, node.outcome)
```

Milestone 6's `compare_block_areas` metric uses `block_area` for both main blocks
and independent reference blocks. If the main computation is partly cached and
the reference computation is fully cached, two entries can look like:

| `use_id` | `node_id` | Bound use | `outcome` |
| --- | --- | --- | --- |
| `u1` | `block_area` | Main blocks A/B | `"mixed"` |
| `u2` | `block_area` | Reference blocks X/Y | `"reused"` |

The bound-use column explains the example; detailed binding fields are separate
from these summary fields. The illustrative IDs identify entries only within the
report. They are not standalone retrieval references and need not remain stable
across executions.

For successful executions, each summary uses one of four outcomes:

| Outcome | Meaning |
| --- | --- |
| `"executed"` | This use performed computation or acquisition without reusing values. |
| `"reused"` | Its needed values were reused without executing its own work. |
| `"mixed"` | This use both executed work and reused values. |
| `"skipped"` | This use needed neither execution nor value reuse. |

A shared resolved use referenced by several consumers has one summary aggregating
its actual work. One consumer bypassing it cannot make it globally `"skipped"`
when another consumer caused it to execute. Distinct bound inputs or effective
parameters retain separate resolved uses, even when `node_id` is the same.

With no reused values, a handler that runs and produces an empty table is still
`"executed"`. A zero-partition use requiring no invocation or reuse is `"skipped"`.
Both can belong to a successful execution. These summaries describe node uses
without a per-partition or per-row event history.

### Resolved inputs, parameters, and output scopes

Expose the root and each use's bound data through ordinary report fields:

| Field | Meaning |
| --- | --- |
| `report.root_use_id` | Identifies the root entry in `report.nodes`. |
| `node.parameters` | Complete effective parameter values, including defaults and nulls. |
| `node.primary_input` | The primary input's declared name; `None` for an unpartitioned metric. |
| `node.inputs` | Resolved data bindings keyed by declared input name. |
| `node.outputs` | Metric output descriptions keyed by output name, using milestone 6's schema/scope contract. |

An operation's `primary_input` is its sole declared source name. Metric output
descriptions expose `scope`, `table_schema`, and `owner_scope` as established in
milestone 6. Each data binding exposes:

| Field | Meaning |
| --- | --- |
| `source_use_id` | The producing entry in this report. |
| `output_name` | The selected named metric output, or `None` for provider/operation location records. |
| `provider_scope` | The concrete provider scope for a location input, or `None` for a metric-table dependency. |

A metric table's owning scope is available through its producer's named output
description. For an operation-derived location input, `provider_scope` describes
the forwarded target records. Provider scopes retain the provider ID, code revision,
and origin identities used by that execution.

With milestone 6's comparison metric registered, inspect its two area uses:

```python
comparison_result = runner.run("compare_block_areas", {
    "blocks": {"provider": "city_blocks", "ids": ["A", "B"]},
    "reference_blocks": {"provider": "city_blocks", "ids": ["X", "Y"]},
})

report = comparison_result.report
uses = {node.use_id: node for node in report.nodes}
root = uses[report.root_use_id]

main = uses[root.inputs["areas"].source_use_id]
reference = uses[root.inputs["reference_areas"].source_use_id]

root.inputs["areas"].output_name       # "area"
main.node_id                          # "block_area"
reference.node_id                     # "block_area"
main.use_id != reference.use_id       # True

main.parameters
main.primary_input                    # "blocks"
main.inputs["blocks"].provider_scope
main.outputs["area"].owner_scope
```

The two uses link to their respective upstream inputs through existing
report-local IDs. Inspection requires no additional reference format or lookup
API. These descriptions remain available for reused, skipped, and empty results;
they come from the resolved plan and retained declarations without acquiring data
or requiring retained intermediate tables. Named parameter-forwarding bindings
and operation-specific target/stage details are separate from these core fields.

### Implementation revisions and declared origins

Expose code and data declarations through these fields:

| Field | Meaning |
| --- | --- |
| `node.code_revision` | The definition's declared revision, including `None` when omitted. |
| `node.origins` | Its direct origin declarations, exposing `id`, `revision`, and `description`. |
| `report.build_revision` | The deployment-build revision, or `None` for local execution without one. |

```python
for node in report.nodes:
    print(node.node_id, node.code_revision)

    for origin in node.origins:
        print(origin.id, origin.revision, origin.description)

report.build_revision
```

Origins remain direct: consuming provider records does not automatically add the
provider's origins to a metric's `node.origins`. A metric querying that same source
directly declares it independently. In milestone 5's census example:

| Node | Direct origin | Declared use |
| --- | --- | --- |
| `census_tracts` | `("national-census", "2025")` | Provider acquisition of tract records. |
| `queried_tract_population` | `("national-census", "2025")` | The metric's own census query. |

Both entries retain the informational description `"National census release."`.
Deduplicate origins within each node by `(id, revision)` while preserving these
separate declarations. Operations have `origins=[]`; their data
dependencies remain visible through source bindings and
`node.operation.target_scope`.

These fields use declarations captured for the execution and remain available for
reused and skipped nodes without acquiring data. Resource bindings do not become
origins. Node and operation-stage outcomes describe observed work; an origin
declaration alone does not prove that a source was accessed. Deployment-build
revisions remain separate from computation identity and introduce no additional
cache invalidation token.

### Named parameter forwarding

`report.parameter_bindings` is a flat list of ordinary embedded report records.
Each names the source use and parameter and the receiving use and parameter.
For milestone 6's `count_large_blocks` example, let `u0` identify the metric use
and `u1` identify its `large_blocks` operation use. Their `minimum_area` forwarding
record serializes as:

```json
{
  "source_use_id": "u0",
  "source_parameter": "minimum_area",
  "target_use_id": "u1",
  "target_parameter": "minimum_area"
}
```

The IDs are illustrative report-local IDs. Effective values remain in each use's
`node.parameters`; the binding record describes only the connection. Inspect the
records through ordinary attributes:

```python
for binding in report.parameter_bindings:
    print(
        binding.source_use_id,
        binding.source_parameter,
        binding.target_use_id,
        binding.target_parameter,
    )
```

Explicit `Input(...)` bindings and automatic matching-name forwarding produce
the same record shape. Only resolved forwarding creates a record; coincidentally
equal values do not. Literal bindings and receiving-model defaults appear in
`node.parameters` without a forwarding record or supplied/default flag. In the
example, `include_requested_total` remains a metric parameter and has no
forwarding record to the operation.

Forwarding a nested object or list creates one record for the complete parameter.
A chain from A's `search_radius` to B's `radius`, then C's `distance`, retains its
two direct bindings. These records have no separate binding IDs, parameter-reference
objects, or additional lookup methods.

Bindings remain visible for reused and skipped nodes. They describe the selected
plan and do not establish that either node executed.

### Operation targets and observed stages

Operation entries expose a small `node.operation` detail object. For a computed
use of milestone 7's `mature_trees_near_blocks` operation:

```python
details = node.operation

details.target_scope.provider_id   # "trees"
details.target_adapter             # "parquet"
details.selector                   # "executed"
details.target_query               # "executed"
details.mapping                    # "executed"
```

`target_scope` reuses the provider-scope description, including provider ID, code
revision, and origin identities. `target_adapter` identifies the configured
managed query adapter: `"parquet"`, `"postgis"`, or `None` when the target provider
has no managed query adapter. These fields describe the configured target even
when work is skipped and are available without initializing resources. A configured
adapter does not itself imply that a query ran.

For successful executions, the stage fields use these values:

| Value | Meaning |
| --- | --- |
| `"executed"` | The stage actually ran at least once for this resolved use. |
| `"skipped"` | The stage applies, but no execution was needed. |
| `"not_applicable"` | The operation has no selector, so selector construction and target querying do not apply. |

The mapping stage always applies and can be `"executed"` or `"skipped"`.
Typical observations are:

| Situation | `selector` | `target_query` | `mapping` |
| --- | --- | --- | --- |
| Selector-backed operation computes missing values | `"executed"` | `"executed"` | `"executed"` |
| Its correspondences are fully reused | `"skipped"` | `"skipped"` | `"skipped"` |
| Operation without a selector computes values | `"not_applicable"` | `"not_applicable"` | `"executed"` |

A query returning zero candidates is still `"executed"`. If the mapping handler
accepts those empty candidates and returns empty correspondences, it is also
`"executed"`. Partial reuse can give the operation `outcome="mixed"` while all
three stages are `"executed"` for its missing partitions. Stage summaries retain
no per-partition history or invocation counts.

Target-query observations describe acquisition performed by the provider.
Ordinary target-record resolution follows the provider's acquisition and reuse
policy, so an operation can reuse its mapping while a provider still acquires
records needed for forwarding. The operation's correspondence outcome and the
provider's acquisition outcome remain distinct. These fixed descriptive fields
require no retained expressions, SQL, candidate membership, or event log.

### Reopening reports and results

Extend milestone 8's existing `ResultStore` interface with `read_report()`:

```python
from lyra.runtime.results import ResultStore

with ResultStore.open(result.metadata.store_path) as store:
    report = store.read_report(execution_id)
    restored = store.read_result(report.result_ref)

assert restored.metadata.execution_id == execution_id
assert restored.report.execution_id == execution_id
```

`read_report(execution_id)` loads the complete report without loading output
tables. `read_result(result_ref)` returns a `RunResult` containing its named native
tables, metadata, and eagerly attached complete report. `read_table()` continues
returning just the requested native table. These read-only operations need no
runner, plugin loading, or external acquisition.

Returned reports, metadata, and tables remain usable after the store closes.
Each returned report is detached from retained state; caller mutations, including
changes to nested fields, cannot alter persisted reports or subsequent reads.
Reports use ordinary Pydantic field access under milestone 4's ownership rules.
Full local access follows these contracts independently of the bounded transport
inspection interfaces introduced in milestone 11.

### Report lookup failures

Expose these exceptions from `lyra.contracts.errors`, sharing milestone 8's
`ResultAccessError` base:

| Exception | Condition |
| --- | --- |
| `InvalidExecutionIDError` | The supplied execution ID fails shared identifier validation. |
| `ReportNotFoundError` | The ID is valid, but this store has no retained report for it. |
| `ReportIntegrityError` | The retained report or its required relationships are damaged. |

Each exposes `execution_id`. Callers can catch a specific exception or catch
`ResultAccessError` for both result and report lookup failures. Chain underlying
storage exceptions when relevant.

```python
from lyra.contracts.errors import ReportNotFoundError
from lyra.runtime.results import ResultStore

with ResultStore.open(store_path) as store:
    try:
        report = store.read_report(execution_id)
    except ReportNotFoundError as error:
        print(error.execution_id)
```

A valid execution ID from an unsuccessful attempt without a retained report
raises `ReportNotFoundError`, consistent with success-only report retention.
Errors follow the requested lookup. If `read_result(result_ref)` finds a result
whose required report is missing, it raises `ResultIntegrityError` with that
`result_ref` and `output_name=None`. If `read_report(execution_id)` finds a report
whose required final-result description is missing or incorrectly linked, it
raises `ReportIntegrityError` with that `execution_id`.

Checking required report relationships loads no output tables and performs no
recursive integrity sweep. Opening a nonexistent store retains milestone 8's
standard `FileNotFoundError` behavior. Remote inspection and transport error
details belong to milestone 11.

### Required persistence failures

A successful return includes durable results and a complete report. Catch required
persistence failures through the public local exception:

```python
from lyra.contracts.errors import ExecutionPersistenceError

try:
    result = runner.run("block_area", arguments)
except ExecutionPersistenceError as error:
    print(error.execution_id)
```

The same exception contract applies to `run_development()`. Required value-storage
or final result/report publication failures raise this exception even after
calculation has finished, with the underlying exception chained as its cause.
No `RunResult` is returned, and the attempted execution ID does not promise an
available report. Remote failures follow the ordinary failed-job path and surface
through `MetricRunError`; calculation failures retain their existing behavior.

Previously committed complete cache values remain reusable after a later
publication failure. Their presence does not establish successful execution or
publish a partial final result/report pair. Durable reports describing failed or
interrupted attempts are deferred.

## Completion boundary

Every successful managed execution exposes a durable ID and has both its required
results and complete report in the associated store. Current run/result interfaces
work for ordinary, hybrid, empty, fully reused, and partly computed results.

Verify that local results eagerly expose the complete report and matching
execution ID/result reference, including through `run_development()` and reopened
`read_result()` access. Report-only lookup loads no output tables, and returned
reports remain usable after the store closes. Ordinary `.report` access performs
no storage lookup; later executions cannot replace an earlier result's report.

Verify report lookup's three exception categories, exposed execution IDs, and
shared `ResultAccessError` base, including absent reports for unsuccessful
attempts. Broken required report/result links raise the integrity exception for
the requested lookup with its corresponding identifier; report lookup checks
these relationships without loading output tables or sweeping retained values.

Verify that `report.request` retains the selected root and complete caller
selections through routing and cache reuse, excludes derived inputs, and preserves
effective parameters matching the root node and result metadata, including
defaults and nulls.

Verify unique report-local use IDs, distinct uses of the same definition, and one
summary for a shared resolved use. Outcome checks distinguish execution, reuse,
mixed work, and skipped work, including executed empty outputs and zero-partition
uses requiring neither invocation nor reuse.
Verify that the root and named data bindings resolve to the appropriate report
entries, preserving effective parameters, primary-input names, and output/provider
scopes through reused, skipped, and empty outcomes without additional acquisition.
Parameter-binding checks cover explicit and matching-name forwarding, renamed
parameters, direct chains, and whole nested values; literals and receiving defaults
retain their effective values without creating forwarding records.

Isolated behavioral verification covers separate uses of one definition, declared
versus observed sources, shared dependencies, skipped work, mixed reuse, and zero
locations. Include managed target queries with matches and no matches, selector
construction distinct from acquisition, and cache hits bypassing selector/query
work. Verify stage markers for operations with and without selectors, mixed reuse,
and skipped work, retaining target scope and adapter descriptions without resource
initialization. Operation mapping reuse remains distinct from any provider
acquisition needed for forwarding. Verify that input and returned-frame mutations
cannot alter retained report scope or identity, and that nested mutations to
returned reports and result descriptions leave retained records and subsequent
retrievals unchanged.
Storage-boundary failures verify that success is withheld, partial
publication is not exposed, and previously committed cache values remain reusable.
Verify the local `ExecutionPersistenceError`, its execution ID and chained cause,
for both run methods, and the normal remote failed-job/`MetricRunError` path.
Returned and retained reports have `status="succeeded"` and a required result
reference; failed attempts return no `RunResult` or durable failure report.
Reopened local access and simulated job expiry do not lose reports or final values.

Verify reports for separate concrete block and tract metrics, changed provider
code and origin revisions, and two uses of one metric with different ID selections
or parameters in a single graph. Actual input and output scopes must remain distinct
through empty, cached, and mixed-reuse outcomes, while declared operation targets and
independent origins retain their identities. Include two providers sharing an
origin and a metric declaring a direct read of that origin alongside a provider
input; report declarations without treating resource bindings as data sources.
Verify direct-origin lists and declared code revisions on reused and skipped
nodes, empty direct origins on operations, and separate deployment-build reporting,
including `build_revision=None` for local execution without a build revision.

## Deferred or excluded work

Durable reports for failed or interrupted attempts and their partial-execution
models are deferred. Existing execution errors and failed-job handling remain the
failure interfaces.

Bounded HTTP/MCP inspection, complete remote JSON downloads, and their Python
client support arrive in [milestone 11](11-result-and-report-retrieval.md). They
cannot weaken durable publication or retention.
Their contract is a complete inline report when it fits, otherwise an explicitly
partial preview preserving execution ID and status, identifying omissions, and
linking to a full authenticated JSON download. The download may exceed the
inspection limit. Large reports must remain fully durable and accessible, rather
than fail execution solely because inspection is bounded.

Finding-level provenance, public row/group trace references, empty-group
inspection, intermediate member listing, input-selection history, navigable
parameter-origin chains, graph navigation/visualization, recursive expansion,
downstream-impact search, and detailed partial failure traces are deferred until a
concrete workflow justifies them. Do not retain extra identities or membership now
to prepare for those features.

Exact contributing rows, rejected-candidate explanations, exhaustive-selection
proofs, historical cache ancestry, arbitrary file-artifact lineage, cell/parameter
influence tracking, and explicit capture of every node output are outside this
report guarantee. Reports do not archive external data or executable code or
explain external location preparation. Differentiability remains an unspecified
long-term goal. Automatic cleanup and historical compatibility remain outside the
initial implementation.

## Reference points

The current [execution metadata contracts](../src/lyra/contracts/provenance.py)
and [executor](../src/lyra/runtime/execution.py) provide the starting points for
report construction and observed outcomes.
