---
title: Define metric inputs
description: Add typed parameters and spatial arguments to a synchronous calculation.
contentType: How-to
contentPlan: plugins/authoring
sidebar: { label: "Define Metric Inputs" }
---

Define a [metric’s](../../concepts/model/#a-metric-declares-a-calculation) ordinary parameters and spatial inputs so callers know what to supply. Put ordinary choices in a Pydantic parameter model and declare spatial arguments on the function. Lyra derives the request schema from those annotations.

Keep the underlying calculation in its own function or library when practical. The Lyra adapter can translate inputs and pass them to that code.

## Choose the input roles

Use a parameter model for ordinary choices, such as a threshold. For spatial inputs, declare [provider dependencies](../location-providers/) and annotate the receiving arguments as `gpd.GeoDataFrame`. [The city tutorial](../tutorial/parameters/) builds and runs an adapter with both kinds of input.

## Add parameters and defaults

Inherit from `MetricParameters` and use Pydantic `Field` declarations for descriptions, constraints, defaults, and examples. For example:

```python {6-10}
from pydantic import Field
from lyra.sdk import MetricParameters


class Parameters(MetricParameters):
    value: int = Field(
        default=7,
        ge=0,
        description="Nonnegative value copied into each output row.",
        examples=[7, 12],
    )
```

Put defaults on model fields. Handler arguments themselves cannot have defaults. Omit the `parameters` argument entirely if the calculation has no ordinary inputs.

Include `parameters` when required fields need values. You may omit it when an empty object satisfies the parameter schema; preparation applies declared defaults.

## Distinguish an omitted field from null

Whether a field may be omitted is independent of whether it accepts null. Use the declaration that matches the choices your calculation supports:

| Declaration | May be omitted? | Accepts null? | Default |
| --- | --- | --- | --- |
| `value: int` | No | No | No default |
| `value: int = 1` | Yes | No | `1` |
| `value: int \| None` | No | Yes | No default |
| `value: int \| None = None` | Yes | Yes | null |
| `value: int \| None = 1` | Yes | Yes | `1` |

Use `Field(default_factory=...)` for containers and nested parameter models. Pydantic validates defaults and copies mutable literal defaults for each model instance. Use `JsonValue` for JSON values containing nested lists and objects.

Write factories and validators that follow these rules:

- Return the same values for the same inputs.
- Do not perform external input/output (I/O), such as accessing files or services.
- Keep normalized values unchanged when validators run again, including after JSON serialization and parsing.

Lyra prepares parameters for every selected metric or operation invocation before acquiring data or queueing a job. Each invocation’s Pydantic model applies defaults and validators. Lyra retains every resulting value, including nulls, and uses JSON Schema to describe parameters for discovery.

Pydantic converts compatible input types; for example, an ordinary integer field accepts the string `"7"`. Use strict Pydantic fields when inputs must match their declared types.

Use model-level after validators for cross-field checks and preserve declared types. Lyra rejects invalid parameters during preparation, before any handler runs. Request [provenance](../../concepts/provenance/) records the resulting parameter values used for execution and [cache identity](../../concepts/caching/).

## Declare spatial inputs

Declare each provider input in `inputs` with `LocationDependency(provider="provider_id")` and annotate the corresponding argument as `gpd.GeoDataFrame`. Import GeoPandas as `gpd` and `LocationDependency` from `lyra.sdk`. Metrics may declare zero or more provider inputs alongside ordinary table dependencies.

Describe an argument with `Annotated[gpd.GeoDataFrame, Field(description="your_input_description_here")]`. Import `Annotated` from `typing` and `Field` from `pydantic`.

Describe the geometry and attributes your calculation requires, using evidence from its implementation and documentation. Explain any scientific requirements callers must meet. Lyra includes these descriptions in request schemas and inspection results.

The handler receives a frame in the provider’s CRS with every registered column:

- `location_id`: the record ID
- `geometry`: the active geometry column
- All declared attribute columns

Spatial arguments cannot accept null or have defaults. Copy the frame before editing or reprojecting it, and preserve the calculation’s intended row identity and order.

A provider’s [record scope](../location-providers/#define-revision-coverage) identifies the dataset and revision its records belong to. Changing `DataFrame.attrs` cannot change that scope. Matching columns or IDs do not make records from different providers interchangeable.

Requests supply `{"provider": "provider_id", "ids": ["record-a"]}`. An empty ID list is valid; every supplied ID must be a nonempty string. [Aggregate selected lots by block](../location-example/) demonstrates two provider inputs and an explicit dependency binding.

### Derive inputs with operations

Use [location operations](../location-operations/) to derive inputs within the graph. An operation maps source IDs to existing target-provider IDs, using optional typed parameters. It cannot acquire data or consume table dependencies.

Declare the source in `inputs` and the target with `target_provider="target_provider_id"`. Lyra validates the mapping and resolves target records through the fixed target provider.

With a [target selector](../../reference/target-selection/), Lyra queries candidates before invoking your mapping handler. It then reuses the validated records for forwarding. Use `LocationDependency(operation="selector").bind(...)` to supply the operation’s result to a metric; callers supply only provider selections.

### Declare direct data sources

Declare `origins=(Origin(...),)` on `@metric` for external data the metric reads directly, including reads through resources. An [origin](../location-providers/#declare-a-provider) identifies an external dataset and its revision. Do not repeat origins for data received through provider inputs or upstream outputs. Lyra includes direct origins and provider scopes in cache identity before lookup.

## Describe output meaning and granularity

Use Pandera schema metadata to describe what the metric returns and what each row represents. Lyra preserves `meaning` and `granularity` alongside column definitions and units in generated node metadata.

For example, pass this metadata to `pa.DataFrameSchema` for a metric that returns one population value per tract:

```python
metadata = {
    "meaning": "Resident population",
    "granularity": "One row per tract",
}
```

These declarations describe the calculation independently of source selection.

## Reuse an existing parameter model

An existing `BaseModel` works when it and every nested model set `extra="forbid"` and `validate_default=True`. Inherited settings count. Adapt an incompatible model with a small subclass and compatible nested types; Lyra leaves the original model unchanged.

See [parameter and handler rules](../../reference/parameters/) for the supported types and signature constraints. Continue to [output tables](../outputs/) to describe the result and [testing](../testing/) to exercise the adapter.

## Define concrete metrics

Register separately selectable calculations with unique metric names. Follow [Register concrete metrics](../metrics/) for factories and dependency bindings.

## Assign an implementation revision

Use optional `code_revision` to identify a metric, operation, or provider implementation. Lyra treats this value as an opaque token: it compares strings without interpreting version numbers. For metrics and operations, the registered name and revision identify the implementation. For providers, the ID and revision contribute to [provider scope](../location-providers/#define-revision-coverage).

Omitting the revision is equivalent to `code_revision=None`. Supply a nonblank string; Lyra preserves it exactly, including whitespace. `"2"` and `"02"` are distinct, while integers and blank strings are invalid.

Change the token or node ID whenever your implementation changes outputs. Include changes to declarations, helpers, and callbacks; an operation’s revision covers both its selector and mapper. Lyra doesn’t inspect code or package versions to detect these changes.

The [computation key](../../concepts/caching/#reuse-tables-across-executions) also includes scopes and complete effective inputs. Defaults and nulls participate, so omitting a parameter matches supplying its default. Parameter list order matters; location and origin order don’t.

Return the same logical values for the same identity and effective inputs, even with caching off. Represent configuration and external-data changes through parameters or declared identities. Writing a key again preserves its existing value.

## Make intermediate metrics private

Metrics are public by default. Set `private=True` on `@metric` to exclude a metric from discovery and direct public execution, including `LocalRunner.run`. The `private` flag applies only to metrics.

Public metrics can depend on private metrics, and Lyra executes and caches those dependencies normally. Private metrics remain visible in dependency inspection; `PluginDefinition.describe` includes their `private` flag.

To test a private metric through the graph, call `runner.run_development()` with its name, `inputs=`, and `parameters=`. Use the same [named ID selections](../../use/local-execution/#supply-id-selections) as public calls. You can also call decorated Python functions directly for tests.

## Inspect node declarations

Call `PluginDefinition.describe(name)` to inspect a node before publishing your plugin. The returned `NodeDescription` includes the function’s name and signature. Its `manifest` field contains an independent description of the node.

Read `description.manifest.request_schema` for the complete generated input schema. The command-line interface (CLI) includes this schema in its JSON inspection output. See [Package a plugin](../publish-and-debug/) for inspection commands.

Inspect these fields on `description.manifest`:

| Field | What to inspect |
| --- | --- |
| `parameters_schema` | The ordinary parameter schema; `null` in JSON means the handler has no parameter argument |
| `location_inputs[name].contract` | The named input’s native location contract |
| `location_inputs[name].description` | The optional argument description |
| `location_inputs[name].dependency` | The declared provider or operation dependency |

Check `manifest.kind` to choose the output declaration: metrics expose table `outputs`, while operations expose `output_contract`. An operation’s generated `output_contract.family` describes its target provider structure and scope; the target provider schema supplies coercion and custom validation.

## Inspect registered implementations

Call `plugin.implementation(name)` to look up a metric or location operation. The returned `lyra.sdk.NodeImplementation` exposes these fields:

- `function`: the original handler
- `parameters`: its parameter model, or `None`
- `manifest`: the node declaration
- `accepts_context`: whether the handler accepts a runtime context

Each entry in `manifest.resources` maps a handler argument to its registered resource name. An unknown node name raises `PluginDefinitionError` and lists the available nodes.

For metrics, `schemas` maps output names to compiled Pandera schemas. Operation descriptions identify the fixed target provider and source declaration. Inspection returns independent descriptive metadata.

You cannot reassign descriptor fields. Treat the shared callable, model class, and compiled Pandera schemas as read-only.

Use `plugin.resources` to look up registered resource classes by name. Registered `LocationProvider` subclasses expose their public IDs and native schemas before resource acquisition.
