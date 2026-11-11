# 4. Plugin discovery and normalized jobs

Use live plugin definitions and one parameter authority, then queue compact
requests that a compatible worker can prepare. The API and worker share the same
deployed definitions and effective parameter values; the executor owns execution
plan validation before resource acquisition.

## Accepted scope

### Plugin loading and discovery

Discover explicitly enabled installed plugins through standard Python entry
points. Each plugin distribution provides one entry point named `plugin` in the
`lyra.plugins` group, pointing to a synchronous, parameterless factory returning
`PluginDefinition`. Deployment configuration and local execution select plugins
by installed distribution name. API and worker processes load their definitions
into the shared catalog.
Pydantic generates parameter discovery schemas, while executable node and output
declarations remain available for planning and validation. The shared catalog
describes node kinds, Pandera schemas, and dependency bindings. As subsequent
milestones introduce output scopes, external origins, primary-input ownership,
generated operation-forwarding schemas with source/target scopes, optional target
selectors, and managed provider-adapter capabilities, discovery exposes that metadata
from the same live definitions. Operation discovery identifies its sole named
source, fixed target provider, and optional parameter model. Selector discovery
uses those same declarations without executing callbacks or inspecting external
tables. No separate manifest authority is introduced for those features.

Select public metrics directly by their registered names. Remove metric
family/edition declarations, edition selectors, and cross-edition schema
comparison. Adapt discovery, request preparation, and Python, HTTP, and MCP
interfaces together; private roots remain available through development execution.
Discovery describes each metric's own parameters, outputs, and named inputs.
As milestones 5 and 6 introduce provider registrations, expose each named location
input's required provider ID, registered schema, code revision, and declared origins.
Expose metrics' own direct origins on their declarations. Operation-derived inputs
expose the generated forwarding schema.

As [milestone 6](06-unified-graph-execution.md) establishes native table inputs,
discover spatial arguments as ordinary `GeoDataFrame` values and nonspatial
table arguments as `DataFrame` values. Explicit named bindings distinguish
registered location inputs, operation correspondences, and metric dependencies;
a GeoDataFrame annotation alone cannot determine the input's role. Validate those
bindings against live schemas without constructing a custom location value or
acquiring data. Provider scope and source/target bindings belong to catalog and
runtime bindings, not handler-owned frame attributes. Metrics may combine named
location inputs and metric-table dependencies. Operations have one named location
source and no metric-table dependencies under milestone 6's signature contract.

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

`Dependency(producer=..., output=...)` selects a metric output;
`LocationDependency(operation=...)` invokes a location operation. Both accept
parameter bindings through `parameters=` and location bindings through
`.bind(producer_input=Input("caller_input"))`. An operation-form
`LocationDependency` can itself be a location-binding value. `.bind()` returns a new declaration without
running handlers or mutating the original; supplied bindings replace bindings
for those names, while other existing bindings and matching-name forwarding
remain in effect. Its keyword names identify the invoked producer's location
inputs, and `Input(...)` refers to the declaring caller's effective inputs.
An operation's `.bind()` key is its sole declared source name, independent of the
callback's first argument name. Milestone 6 binds operation tables by position;
metric handler tables continue binding by their declared names. Nested operations
can supply that sole source without introducing another operation input.

Plugin imports, registration, and parameter validation must be lightweight and
avoid external I/O. Acquire services and resources during execution. The API must
be able to import enabled plugins and their dependencies. Increased startup time
and memory, and startup failure from a broken plugin import, are accepted
tradeoffs. No new deployment-synchronization machinery is required.
The deployment example already uses one image for API and workers. Startup
diagnostics and catalog internals remain implementation choices.

### Effective parameter values

Keep `MetricParameters` as a small shared Pydantic parameter base with validated
defaults and unknown fields rejected. Support ordinary JSON scalars and
containers: strings, booleans, integers, finite floats, null, lists, string-keyed
dictionaries, and nested Pydantic models of supported fields. Use Pydantic's
`JsonValue` for recursive JSON structures and library validation and encoding
options to reject non-finite numbers.

Metrics and operations may use different `MetricParameters` subclasses; explicit
parameter bindings connect their fields. Infer an operation's optional model from
the mapping handler's keyword-only `parameters` annotation. A parameterized
operation's selector, introduced in milestone 7, accepts that same model as
keyword-only `parameters`, even if one callback does not use it. Both callbacks
omit the argument for an unparameterized operation. Reject selector-only or
conflicting model declarations before execution. One operation use has one set of
validated effective values for both callbacks.

Support ordinary `Field(default_factory=...)` usage for containers and nested
parameter models. Include the resulting defaults explicitly in the effective
values sent to workers.

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
Give an operation's selector and mapping handler independent parameter instances,
including nested values, so selector edits cannot reach the mapping handler or
the retained effective values.

[Milestone 14](14-interventions.md) extends these ownership rules to caller-supplied
intervention sets and added record values. Capture their complete accepted contents
independently of caller objects; normalization, worker transport, execution keys,
and reports must retain the same contents. Interventions are request context,
separate from calculation parameters and executable dependency declarations.

### API-to-worker handoff

Require the API and workers to share one immutable plugin build. Queue the
normalized request, all effective parameter values, build revision, and required
provider scopes: provider IDs, code revisions, and declared origin identities.
Remove per-job captured executable graph contracts and their comparison and
reconstruction machinery.

Carry the selected metric name and each named location selection's provider and
IDs through this handoff. Preparation captures provider scopes from declarations;
workers resolve selections against the same registrations and pinned scopes,
using the declared provider requirements and routing policy.

When interventions are supported under milestone 14, the handoff also preserves
their accepted sets, target provider bindings, and base scopes, including targets
not named in the root's caller selections. Workers apply them throughout managed
location access. Registration and structural validation remain free of acquisition;
base-record existence and absence checks run through managed resources after plan
validation and before downstream computation or successful cache reuse. Milestone
14 owns that validation and its public request representation.

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
`(node_id, code_revision)`; base provider records use the provider ID, code revision,
declared origin identities, and location ID. Rejecting inexpensive, short pending
jobs after an incompatible deployment is an accepted cost. Build identification
and the compatibility-check mechanism remain implementation choices.

## API sketches

These sketches describe the supported authoring and selection syntax. Declaration
fragments assume the referenced handlers, inputs, parameters, and outputs exist;
full provider and graph examples belong to their respective later milestones.

### Plugin loading

The plugin's `pyproject.toml` declares its factory:

```toml
[project.entry-points."lyra.plugins"]
plugin = "city_example:create_plugin"
```

The factory registers live definitions. This fragment assumes `street_maintenance`
is already defined; register any dependencies alongside their consumers:

```python
from lyra.sdk import PluginDefinition


def create_plugin() -> PluginDefinition:
    return PluginDefinition(metrics=[street_maintenance])
```

The deployment selects the installed distribution in `lyra.toml`:

```toml
[[plugins.installed]]
distribution = "lyra-city-example"
enabled = true
```

### Parameters and effective values

```python
from pydantic import Field, JsonValue, field_validator

from lyra.sdk import MetricParameters


class Filters(MetricParameters):
    tags: list[str] = Field(default_factory=list, description="Required tags.")


class Parameters(MetricParameters):
    radius: float = Field(100.0, gt=0, description="Search radius.")
    label: str | None = Field(None, description="Optional result label.")
    filters: Filters = Field(default_factory=Filters, description="Search filters.")
    options: dict[str, JsonValue] = Field(
        default_factory=dict, description="Additional JSON options."
    )

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None
```

Given this parameters object:

```json
{"options": {"enabled": true, "weights": [1, 0.5]}}
```

The effective parameters include all defaults and nulls:

```json
{
  "radius": 100.0,
  "label": null,
  "filters": {"tags": []},
  "options": {"enabled": true, "weights": [1, 0.5]}
}
```

Supplying `"label": " north "` normalizes it to `"label": "north"`;
worker revalidation preserves that value. Unknown fields, non-finite numbers,
unsupported Python values, custom serializers, and field exclusions are rejected.

### Dependency bindings

Select a producer's named output for the consumer's `scores` input:

```python
from lyra.sdk import Dependency, Input, LocationDependency

inputs = {
    "scores": Dependency(producer="street_score", output="scores"),
}
```

The same producer name and output can be selected under different consumer input
names. Location inputs and parameters with matching names forward automatically;
the consumer's name for a dependency does not select its argument bindings.
Remaining parameters use the receiving node's defaults; required inputs without
a binding or applicable default are rejected. Explicit bindings take precedence:

```python
inputs = {
    "scores": Dependency(
        producer="street_score",
        output="scores",
        parameters={
            "radius": Input("parameters.search_radius"),
            "filters": Input("parameters.filters"),
            "mode": "fast",
            "options": {"kind": "input", "path": "parameters.search_radius"},
        },
    ).bind(
        streets=Input("selected_streets"),
    ),
}
```

Here `streets=` names the producer's location input, and
`Input("selected_streets")` refers to the caller's input.
`filters` forwards the complete nested value. The `mode` string and `options`
dictionary are literals; only an actual `Input(...)` establishes a reference.
Nested operations use the same caller scope:

```python
inputs = {
    "scores": Dependency(
        producer="street_score",
        output="scores",
    ).bind(
        streets=LocationDependency(
            operation="streets_in_region",
            parameters={"radius": Input("parameters.search_radius")},
        ).bind(
            region=LocationDependency(
                operation="region_for_blocks",
            ).bind(
                blocks=Input("blocks"),
            ),
        ),
    ),
}
```

Both references read the effective inputs of the consumer declaring this binding.
`region_for_blocks` has one source named `blocks`; `streets_in_region` has one
source named `region`. Each callback receives that source as its first positional
argument, whatever the callback calls it. The outer metric retains its named
`streets` input and ordinary metric dependencies.

### Direct metric discovery and selection

Python execution entry points take a metric name and a plain JSON-compatible
argument dictionary. Each of the selected metric's inputs declared with
`LocationDependency(provider=...)` takes a top-level selection entry with
`{"provider": str, "ids": list[str]}`; calculation parameters go under the reserved
`"parameters"` key. Inputs declared with `LocationDependency(operation=...)` are
computed from their declared bindings. Requests supplying those derived input
names fail validation before acquisition. Discovery distinguishes these derived
inputs from caller-supplied selections and excludes them from request fields.
For an open consumer client, with `arguments` matching the
selected metric's discovered input schema:

```python
matches = client.catalog.metrics(query="maintenance")
description = client.catalog.metric("city_street_maintenance")
handle = client.jobs.submit("city_street_maintenance", arguments)
```

The same registered name and argument dictionary select the metric locally:

```python
from lyra.runtime.local import LocalRunner

runner = LocalRunner(plugins=["lyra-city-example"])
result = runner.run("city_street_maintenance", arguments)
```

Each registered public name identifies one concrete metric with its own parameter
schema, named inputs, and outputs. Unknown names are rejected. Private roots are
available through `runner.run_development(name, arguments)` and cannot be
submitted as public metrics. [Milestone 6](06-unified-graph-execution.md) unifies
selection and parameter validation across local, development, and remote execution
using this argument shape and introduces the structured local `RunResult` with
`.tables` and `.metadata`. HTTP and MCP use the same direct-name selection rule;
MCP retains its tool names.

## Completion boundary

Discovery, local execution, and remote submission work using live definitions.
Queued requests retain effective values across transport, and incompatible jobs
fail before acquiring resources. Tests use fake plugin catalogs and queue/service
boundaries to cover defaults, nested values, supported validators, invalid values,
parameter forwarding, import failures, and deployment mismatch.

Verify direct metric discovery and selection consistently through local Python,
remote Python, HTTP, and MCP, including unknown names and private-root restrictions.
Later input discovery exposes concrete provider requirements, their registered
schemas and origin declarations without acquiring resources.

Dependency checks cover named output selection, explicit and matching-name
forwarding, nested caller-scoped bindings, and literal JSON objects resembling
references. Cover renamed producer inputs and missing required bindings, and
verify that `.bind()` preserves the original declaration while applying named
overrides. Ownership checks mutate nested constructor inputs, handler parameters,
and returned metadata and verify that retained state and later reads are unchanged.
These checks protect boundary behavior without requiring custom field-access hooks.
Cover distinct metric/operation parameter models, forwarding only matching or
explicitly bound fields, defaults, and missing required values. As selectors arrive,
reject mismatched callback parameter models and verify that both callbacks receive
equal effective values with independent nested ownership.

Examples and authoring guidance use the supported loading and parameter contracts.
Existing workflows remain functional without mandatory manifests or captured
executable contracts.

## Deferred or excluded work

New provider and graph contracts follow in
[milestones 5](05-location-providers-and-origins.md) and
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
