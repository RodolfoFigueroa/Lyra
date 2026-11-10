# 4. Plugin discovery and normalized jobs

Use live plugin definitions and one parameter authority, then queue compact
requests that a compatible worker can prepare. This milestone covers decisions
5, 7, and 8 and R8 in [the ledger](../DECISIONS.md), including the executor's
plan-validation responsibility from decision 6.

## Accepted scope

### Plugin loading and discovery

Discover explicitly enabled installed plugins through standard Python entry
points. API and worker processes load their definitions into the shared catalog.
Pydantic generates parameter discovery schemas, while executable node and output
declarations remain available for planning and validation. The catalog must also
carry the dataset, origin, output-scope, and derived-schema metadata introduced by
the following milestones.

Remove mandatory generated plugin manifests, synchronization and verification
machinery, parallel JSON Schema parameter validation, and custom default handling.
A manifest export may remain only as an optional inspection facility; it cannot
be required for loading or execution.

Plugin imports, registration, and parameter validation must be lightweight and
avoid external I/O. Acquire services and resources during execution. The API must
be able to import enabled plugins and their dependencies. Increased startup time
and memory, and startup failure from a broken plugin import, are accepted
tradeoffs. No new deployment-synchronization machinery is required.

### Effective parameter values

Keep a small shared Pydantic parameter base with validated defaults and unknown
fields rejected. Support ordinary JSON scalars and containers: strings, booleans,
integers, finite floats, null, lists, string-keyed dictionaries, and nested
Pydantic models of supported fields. Use built-in `JsonValue` for recursive JSON
structures and library validation and encoding options to reject non-finite
numbers.

Pydantic owns validation, defaults, JSON Schema generation, and serialization.
Serialize every effective field, including defaults and nulls. Parameter models
cannot use custom serializers or field exclusions; unsupported values fail
without arbitrary string conversion. Remove the custom recursive serializer and
restrictions needed solely to synchronize parallel implementations.

Ordinary field and model validators remain supported. Authors guarantee that
they are deterministic, idempotent, free of external I/O, and preserve normalized
values after JSON transport and revalidation. Trust that contract without extra
behavioral proof runs or I/O enforcement. Violations are plugin bugs.

Preserve named parameter forwarding. Each named parameter is one complete value,
including nested objects and lists. Do not track supplied-versus-default flags,
origins of nested fields, or validator internals. Execution, future computation
keys, and future reports use the same effective values.

### API-to-worker handoff

Require the API and workers to share one immutable plugin build. Queue the
normalized request, all effective parameter values, build revision, and required
dataset revisions. Remove per-job captured executable graph contracts and their
comparison and reconstruction machinery.

Workers reconstruct parameter models through ordinary Pydantic validation. Values
defaulted by the API already arrive explicitly, so workers do not choose those
defaults again. Compatible jobs are prepared against the worker catalog. The
executor owns complete execution-plan validation before resources or handlers;
remove the worker's duplicate validation and translate executor failures at that
boundary. Independent request boundaries retain their own input validation.

A build mismatch fails before resource acquisition or handler execution with the
existing terminal failure status and a clear `deployment_changed` reason. Callers
may resubmit. Build identity covers executable code, routing policy, and
declarations; the public discovery fingerprint has a separate purpose. Deployment
revisions do not become computation-cache invalidation tokens.

## Completion boundary

Discovery, local execution, and remote submission work using live definitions.
Queued requests retain effective values across transport, and incompatible jobs
fail before acquiring resources. Tests use fake plugin catalogs and queue/service
boundaries to cover defaults, nested values, supported validators, invalid values,
parameter forwarding, import failures, and deployment mismatch.

Examples and authoring guidance use the supported loading and parameter contracts.
Existing workflows remain functional without mandatory manifests or captured
executable contracts.

## Deferred or excluded work

New dataset and graph contracts follow in
[milestones 5](05-datasets-and-external-origins.md) and
[6](06-unified-graph-execution.md). Durable request/report retention and publication
after job expiry belong to [milestone 9](09-execution-reports-and-durable-success.md).

Closed-alpha changes require no historical readers, executable snapshots, or
compatibility machinery for incompatible builds. Navigable parameter-origin
history, supplied/default tracking, arbitrary Python parameter objects, and
proofs of validator purity or idempotence are outside scope.
