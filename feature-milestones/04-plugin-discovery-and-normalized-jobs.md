# 4. Plugin discovery and normalized jobs

Use live plugin definitions and one parameter authority, then queue compact
requests that a compatible worker can prepare. The API and worker share the same
deployed definitions and effective parameter values; the executor owns execution
plan validation before resource acquisition.

## Accepted scope

### Plugin loading and discovery

Discover explicitly enabled installed plugins through standard Python entry
points. API and worker processes load their definitions into the shared catalog.
Pydantic generates parameter discovery schemas, while executable node and output
declarations remain available for planning and validation. The shared catalog
describes node kinds, Pandera schemas, and dependency bindings. As subsequent
milestones introduce output scopes, external origins, primary-input ownership,
generated operation-forwarding schemas with source/target scopes, optional target
selectors, and managed dataset-adapter capabilities, discovery exposes that metadata
from the same live definitions. Selector declarations identify the target dataset
and declared input bindings without executing callbacks or inspecting external
tables. No separate manifest authority is introduced for those features.

Select public metrics directly by their registered names. Remove metric
family/edition declarations, edition selectors, and cross-edition schema
comparison. Adapt discovery, request preparation, and Python, HTTP, and MCP
interfaces together; private roots remain available through development execution.
Discovery describes each metric's own parameters, outputs, and named inputs.
As milestones 5 and 6 introduce reusable location inputs, expose their required
schemas separately from concrete dataset restrictions and dataset registrations.
Schema acceptance does not select a provider or establish location equivalence.

As [milestone 6](06-unified-graph-execution.md) establishes native table inputs,
discover spatial arguments as ordinary `GeoDataFrame` values and nonspatial
table arguments as `DataFrame` values. Explicit named bindings distinguish
registered location inputs, operation correspondences, and metric dependencies;
a GeoDataFrame annotation alone cannot determine the input's role. Validate those
bindings against live schemas without constructing a custom location value or
acquiring data. Dataset identity and source/target scopes belong to catalog and
runtime bindings, not handler-owned frame attributes.

Remove mandatory generated plugin manifests, synchronization and verification
machinery, parallel JSON Schema parameter validation, and custom default handling.
A manifest export may remain only as an optional inspection facility; it cannot
be required for loading or execution.

Use one validated live declaration representation for each dependency kind,
consumed directly by planning. Remove parallel authoring and manifest dependency
models and their recursive conversion machinery. Serializable discovery and
inspection descriptions are derived views, not executable declarations to reload.
Resolved runtime bindings remain distinct: they identify a particular use of a
declaration with concrete inputs and effective parameters.

Preserve named inputs and outputs, matching-name forwarding with explicit
overrides, whole-parameter forwarding, and nested operation bindings. References
inside nested bindings retain their declaring caller's scope. Literal JSON objects
remain literal even when their fields resemble a serialized input reference;
only explicit reference declarations establish input bindings.

Plugin imports, registration, and parameter validation must be lightweight and
avoid external I/O. Acquire services and resources during execution. The API must
be able to import enabled plugins and their dependencies. Increased startup time
and memory, and startup failure from a broken plugin import, are accepted
tradeoffs. No new deployment-synchronization machinery is required.
The deployment example already uses one image for API and workers. Entry-point
naming, startup diagnostics, and catalog internals remain implementation choices.

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
This supported JSON contract does not require accepting every Pydantic model
feature or arbitrary Python objects.

Ordinary field and model validators remain supported. Authors guarantee that
they are deterministic, idempotent, free of external I/O, and preserve normalized
values after JSON transport and revalidation. Trust that contract without extra
behavioral proof runs or I/O enforcement. Violations are plugin bugs; inconsistent
execution or incorrect cache reuse from a broken validator is an accepted risk.
Ordinary validation at public and worker boundaries remains required and is not
a behavioral proof of the validator itself.

Preserve named parameter forwarding. Each named parameter is one complete value,
including nested objects and lists. Do not track supplied-versus-default flags,
origins of nested fields, or validator internals. Execution, future computation
keys, and future reports use the same effective values.

For example, forwarding A's `search_radius` to B's `radius`, then C's `distance`,
retains those named graph bindings and each node use's effective values. It does
not create public parameter-origin references or an independently traversable
history. Forwarding a nested object or list treats the entire named parameter as
one value.

### Metadata ownership

Use ordinary Pydantic models and explicit ownership boundaries for requests,
declarations, and execution metadata. Remove `OwnedModel` and its copying on
attribute access. Capture independent values when accepting mutable caller or
author data, retain authoritative state privately, and provide detached mutable
values when passing parameters to handlers or exposing inspection results.
Copies must isolate nested containers and models, not only their outer objects;
`frozen=True` alone does not provide that isolation.

Ordinary internal field reads do not copy retained structures. Mutating an
original request or declaration after acceptance or registration, a handler's
parameter model, or an exposed description cannot alter retained bindings,
effective values, or computation identity. Later reports use the same ownership
rules without introducing another model base or metadata wrapper.

### API-to-worker handoff

Require the API and workers to share one immutable plugin build. Queue the
normalized request, all effective parameter values, build revision, and required
dataset revisions. Remove per-job captured executable graph contracts and their
comparison and reconstruction machinery.

Carry the selected metric name and each named location selection's concrete
dataset identity through this handoff. Once dataset binding is introduced, workers
resolve those selections against the same registrations and pinned revisions;
they do not choose a dataset from a schema or reconstruct a metric edition.

Optional target selectors come from the worker's live operation definitions.
Do not queue Ibis expressions, backend connections, credentials, or serialized
callbacks. Managed adapter setup and target-filter execution belong to runtime
acquisition in [milestone 7](07-operation-target-selection-with-ibis.md).

Workers reconstruct parameter models through ordinary Pydantic validation. Values
defaulted by the API already arrive explicitly, so workers do not choose those
defaults again. Compatible jobs are prepared against the worker catalog. The
executor owns complete execution-plan validation before resources or handlers;
remove the worker's duplicate validation and translate executor failures at that
boundary. Independent request boundaries retain their own input validation.

Omitting a parameter whose default is `radius=100` therefore queues the same
effective `radius=100` as an explicit equal input. Include every default and null
field in that handoff; supplied-field masks and custom model restoration are
unnecessary. Worker revalidation must preserve the queued values under the author
contract, so execution, keys, and reporting cannot independently select defaults.

A build mismatch fails before resource acquisition or handler execution with the
existing terminal failure status and a clear `deployment_changed` reason. Callers
may resubmit. Build identity covers executable code, routing policy, and
declarations; the public discovery fingerprint has a separate purpose. Deployment
revisions do not become computation-cache invalidation tokens.
Metric and operation cache implementation identity instead uses the author's
`(node_id, code_revision)`; provider records use immutable dataset/revision/location
identity. Rejecting inexpensive, short pending jobs after an incompatible
deployment is an accepted cost. Build identification and the compatibility-check
mechanism remain implementation choices.

## Completion boundary

Discovery, local execution, and remote submission work using live definitions.
Queued requests retain effective values across transport, and incompatible jobs
fail before acquiring resources. Tests use fake plugin catalogs and queue/service
boundaries to cover defaults, nested values, supported validators, invalid values,
parameter forwarding, import failures, and deployment mismatch.

Verify direct metric discovery and selection consistently through local Python,
remote Python, HTTP, and MCP, including unknown names and private-root restrictions.
Later input discovery distinguishes required schemas from concrete dataset bindings
without acquiring provider resources or duplicating metric definitions.

Dependency checks cover named output selection, explicit and matching-name
forwarding, nested caller-scoped bindings, and literal JSON objects resembling
references. Ownership checks mutate nested constructor inputs, handler parameters,
and returned metadata and verify that retained state and later reads are unchanged.
These checks protect boundary behavior without requiring custom field-access hooks.

Examples and authoring guidance use the supported loading and parameter contracts.
Existing workflows remain functional without mandatory manifests or captured
executable contracts.

## Deferred or excluded work

New dataset and graph contracts follow in
[milestones 5](05-datasets-and-external-origins.md) and
[6](06-unified-graph-execution.md); managed target selection follows in
[milestone 7](07-operation-target-selection-with-ibis.md). Durable request/report
retention and publication after job expiry belong to
[milestone 10](10-execution-reports-and-durable-success.md).

Closed-alpha changes require no historical readers, executable snapshots, or
compatibility machinery for incompatible builds. Navigable parameter-origin
history, supplied/default tracking, arbitrary Python parameter objects, and
proofs of validator purity or idempotence are outside scope.

## Reference points

Current entry points include [plugin loading](../src/lyra/runtime/loading.py),
[parameter contracts](../src/lyra/sdk/parameters.py),
[dependency declarations](../src/lyra/sdk/dependencies.py),
[dependency contracts](../src/lyra/contracts/dependencies.py),
[accepted-request models](../src/lyra/contracts/accepted.py),
[metric discovery](../src/lyra/contracts/discovery.py),
[metric edition selection](../src/lyra/contracts/editions.py),
[plan preparation](../src/lyra/runtime/planning/preparation.py),
[worker execution](../src/lyra/server/worker.py), and
[job observations](../src/lyra/server/job_store.py).
Python packaging documents [entry-point plugin discovery](https://packaging.python.org/en/latest/guides/creating-and-discovering-plugins/).
