# 10. Execution reports and durable success

Automatically explain each managed execution at the graph and node level, and
make a complete report part of successful result publication. Earlier milestones
provide a shared executor with normalized requests, resolved graph bindings,
validated results, and actual execution/cache outcomes. Their value store publishes
complete immutable values; this milestone adds independently retained execution records
and the final result/report publication guarantee.

## Accepted scope

### One report contract for all managed execution

Generate reports through the shared runtime for local, development, and remote
execution, including location-scoped, unpartitioned, and hybrid graphs. Clients
submit one request type with no reporting mode or retention switch. Authors return
ordinary tables or operation mappings, without a lineage payload or reporting
wrapper. Output tables acquire no mandatory reporting columns.

Managed requests contain parameters and registered dataset/ID selections.
Providers and managed dataset adapters acquire records; declared upstream nodes
supply dependency tables. Operation handlers and optional target selectors compute
from their declared inputs without acquiring external resources directly.
`run()` selects public metrics, while `run_development()` can select private
concrete roots before the shared preparation path. Direct handler calls and caller
injection of full records, arbitrary tables, or stored dependency replacements
are outside this managed report contract.

A report retains:

- The normalized request and durable execution ID.
- The selected graph and route, named input/output and parameter bindings, node
  kinds, output scopes, and enough context to distinguish separate resolved uses
  of the same definition.
- Effective parameter values, including defaults and nulls, without
  supplied/default flags or origins for nested fields and list elements.
- Relevant implementation, deployment-build, and dataset revisions and declared
  external origins, including their informational descriptions.
- Observed execution, cache reuse, skipped work, and final status.

A resolved node use is a use of a definition with particular bound inputs and
effective parameters. Distinguish two uses of the same definition in the selected
graph without inventing public row/group or parameter-origin reference objects.
Exact report models, physical persistence layout, and return-envelope names remain
implementation choices.

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

External origins have an ID, optional opaque revision, and optional description.
Combine a metric's explicit origins with resource-binding origins and
deduplicate by `(id, revision)` before resource acquisition or cache lookup. A
provider or managed target adapter derives its origin from the requested dataset
registration, even if the same implementation serves several datasets. Operations
have no direct origins; retain their target dataset and declared input bindings.
Record provider node/code revision separately from dataset identity.
`revision=None` means fixed unversioned data. Keep declared
origins for cached and skipped nodes without initializing resources to collect them.

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

Associate target acquisition with its dataset/revision and configured provider or
adapter, while the operation's code revision covers its selector. Retain observed
executed/bypassed outcomes without storing serialized Ibis expressions, compiled
SQL, credentials, or candidate membership solely for reports. Record an executed
query honestly even if some returned records were already cached. A valid operation
or downstream hit must not construct a filter or execute a query to enrich a report.

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
identity is dataset/revision/location; metric and operation keys additionally
represent their implementation, effective parameters, and bound input/origin
identities. Current reporting does not add another cache invalidation token.

Build scope, identity, and original-selection descriptions from runtime bindings
and stored result metadata. Handler-owned DataFrame attributes and local table
edits cannot rewrite those descriptions. Tables exposed to callers are independent
DataFrames or GeoDataFrames; report and result descriptions remain separate.
No location-value wrapper or handler metadata-inspection interface is required.

### Durable publication and access

Use the same SQLite store for local and API/worker reporting. Managed local runs,
including development, use a persistent default with its path configurable through
normal runtime configuration. Lyra creates and initializes it as needed; authors
do not create tables manually. Result access identifies the associated store so
callers can reopen it. Default paths and configuration spelling remain open.

Before returning local success or publishing remote success, persist every
required final value, its retrieval description, and the complete execution
report. Publish the final result description and report together transactionally
after required values are durable. A partial report cannot be a successful run's
final report, and an inspection size limit never truncates what is retained.

Required result or report persistence failure fails the execution through normal
error handling, even when calculation finished. There is no successful fallback
with an unavailable report. Complete reports are guaranteed for successful
executions; they are not required for every failed or interrupted attempt,
especially when storage itself is unavailable.

Do not hold a database transaction open while handlers run. Complete cache values
may already have been committed and remain reusable after later report publication
fails. Their presence does not publish a successful run. No cross-service
transaction with the queue or historical producer reconstruction is required.

Run/result interfaces expose the durable execution ID alongside ordinary outputs.
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
relationships are integrity errors. Do not reacquire external data or recreate
missing values to answer a report lookup. Intentionally unstored intermediates do
not make a report incomplete.

Preserve execution-scoped and resolved-node-use construction invariants, scoped
keys where applicable, and transactional relationships. Reuse validated data
without another producer-validation pass, separate report revalidation subsystem,
or recursive integrity sweep.

## Completion boundary

Every successful managed execution exposes a durable ID and has both its required
results and complete report in the associated store. Current run/result interfaces
work for ordinary, hybrid, empty, fully reused, and partly computed results.

Isolated behavioral verification covers separate uses of one definition, declared
versus observed sources, shared dependencies, skipped work, mixed reuse, and zero
locations. Include managed target queries with matches and no matches, selector
construction distinct from acquisition, and cache hits bypassing selector/query
work. Verify that input and returned-frame mutations cannot alter retained report
scope or identity. Storage-boundary failures verify that success is withheld, partial
publication is not exposed, and previously committed cache values remain reusable.
Reopened local access and simulated job expiry do not lose reports or final values.

## Deferred or excluded work

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
