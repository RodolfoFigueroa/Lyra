# 9. Execution reports and durable success

Automatically explain each managed execution at the graph and node level, and
make a complete report part of successful result publication. This milestone
covers the reporting and retention obligations in decisions 1, 2, 4, 5, 8,
10–13, and 24 and R2/R3/R5/R7–R10 of [the ledger](../DECISIONS.md).

## Accepted scope

### One report contract for all managed execution

Generate reports through the shared runtime for local, development, and remote
execution, including location-scoped, unpartitioned, and hybrid graphs. Clients
submit one request type with no reporting mode or retention switch. Authors return
ordinary tables or operation mappings, without a lineage payload or reporting
wrapper. Output tables acquire no mandatory reporting columns.

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

Use the normalized request, resolved plan, declarations, and actual outcomes to
construct this information. Retain associated final-output retrieval descriptions
alongside the report. Reports describe the normally selected route without changing
shortest-route rules, preferences, ambiguity, or definition validation.

### Honest outcomes and limited explanation scope

Record handler execution only when it occurs. Provider acquisition is observed
only when that provider handler runs. Cached or bypassed work must not claim fresh
acquisition. Direct origins describe a declared boundary, not proof of which
queries or sources an executing handler actually used.

A node can both execute and reuse partitions. Represent that mixture without a
durable per-partition or per-row event history. A shared dependency executing for
another consumer is not globally skipped; describe only work actually bypassed as
skipped. Declared graph bindings do not imply their values were acquired in the
current execution, and hits do not require following historical cache producers.

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

### Durable publication and access

Use the same SQLite store for local and API/worker reporting. Managed local runs,
including development, use the automatically initialized persistent store from
[milestone 7](07-sqlite-result-storage.md); callers can identify and reopen that
store later.

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
locations. Storage-boundary failures verify that success is withheld, partial
publication is not exposed, and previously committed cache values remain reusable.
Reopened local access and simulated job expiry do not lose reports or final values.

## Deferred or excluded work

Bounded HTTP/MCP inspection, complete remote JSON downloads, and their Python
client support arrive in [milestone 10](10-result-and-report-retrieval.md). They
cannot weaken durable publication or retention.

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
