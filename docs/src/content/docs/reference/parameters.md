---
title: Parameter and handler rules
description: Look up supported parameter types, model settings, and function signature constraints.
contentType: Reference
contentPlan: reference/parameters
sidebar: { label: "Parameter And Handler Rules" }
---

Use this page when a registration or preparation error points to a model field or handler signature. For a worked adapter, see [Define metric inputs](../../plugins/authoring/).

## Metric handler signatures

Handlers are synchronous functions. Lyra accepts positional-or-keyword and keyword-only arguments and calls handlers with keyword arguments.

| Argument | Annotation and rule |
| --- | --- |
| `parameters` | A concrete compatible Pydantic model; omit it when there are no ordinary inputs |
| Any location argument name | `geopandas.GeoDataFrame`; explicitly declared with `LocationDependency`; optional `Annotated` metadata uses `Field(description="…")` |
| `context` | Optional service argument, annotated `RunContext` for runtime services |
| Dependency arguments | Names match the decorator’s `inputs` mapping |

Lyra rejects argument defaults, positional-only arguments, variadics, async handlers, and generators. Return annotations are optional; the explicit `outputs` declaration governs runtime result validation.

## Operation callback signatures

Lyra invokes operation callbacks by position. Argument names can differ between callbacks; `.bind()` uses the source declaration’s name.

The operation’s declaration determines its table arguments:

| Callback | Positional arguments, in order |
| --- | --- |
| ID-only mapping handler | Source `GeoDataFrame` |
| Target selector | Source `GeoDataFrame`, symbolic Ibis table |
| Mapping handler with a selector | Source `GeoDataFrame`, candidate `GeoDataFrame` |

The only additional argument is optional keyword-only `parameters`, annotated with a `MetricParameters` subclass. Lyra infers this model from the mapping handler, independently of metric models. Define defaults on the model; omit the argument when the operation needs no parameters.

When you declare a selector, both callbacks must annotate `parameters` with the same model class or both omit it. Each callback receives independent working frames and parameter instances. The [target selection reference](../target-selection/) describes candidate selection and mapping.

Operation callbacks must be synchronous functions. Lyra rejects generators and callback argument defaults.

## Model settings

Set `extra="forbid"` and `validate_default=True` on the root parameter model and every nested model. Inherit from `MetricParameters` to apply both settings and reject non-finite float values.

Root parameter fields require descriptions. Nested field descriptions and examples are optional. Fields use ordinary Pydantic names without aliases.

## Supported values

Parameter models accept these JSON values when their members follow the same contract:

| Type | Notes |
| --- | --- |
| Strings and booleans | Ordinary JSON scalar values |
| Integers and finite floats | Non-finite numbers are excluded |
| Null | Declare nullability explicitly |
| `Literal` enums | Values use one homogeneous scalar type |
| Lists | Use supported element types |
| String-keyed dictionaries | Open mappings with supported value types |
| Unions and nested models | All members follow the same contract |
| `JsonValue` | Recursive JSON scalars, lists, and string-keyed objects |

Use lists with fixed output columns when a calculation accepts several values. Independent parameter sweeps can be separate jobs.

## Defaults and validators

Use `Field(default_factory=...)` for containers and nested models. Pydantic validates defaults when constructing an instance. Lyra serializes every effective parameter field, including defaults and nulls.

During preparation, Lyra validates parameters for the requested metric and each selected dependency or operation invocation. It uses each invocation’s registered Pydantic model before acquiring locations or running handlers. Operations on [automatic routes](../../concepts/selection/) use their own defaults.

Lyra retains the complete effective values for execution, [cache identity](../../concepts/caching/), and inspection. Omitting a parameter and supplying its default produce the same effective values.

Lyra forwards parameter values between nodes using these rules:

- Parameters with matching names forward automatically; explicit bindings override them.
- Each named object or list forwards as one complete value.
- Fields unique to the receiving model use that model’s defaults.
- Missing required values cause preparation to fail.

Each invocation of a producer retains its own effective values. Nested operation references use the parameters of the caller that declares the binding.

Factories and validators must follow these rules:

- Return the same values for the same inputs (determinism).
- Do not perform external input/output (I/O), such as accessing files or services.
- Preserve normalized values when validators run again (idempotence), including after JSON serialization and parsing.

Lyra supports ordinary field and model validators and trusts authors to meet these obligations. Validation at request and worker boundaries does not prove validator purity. Workers revalidate the queued effective values, including defaults.

Discovery generates parameter schemas without running parameter default factories or validators. Pydantic schema generation can reject a literal dictionary default containing a `$ref` key. You can still supply dictionaries as parameter values.

## Effective parameter example

Pydantic validates parameter values and applies their defaults. This model combines nested defaults with a validator that removes surrounding whitespace from labels:

```python title="effective_parameters.py"
from pydantic import Field, JsonValue, field_validator
from lyra.sdk import MetricParameters


class Filters(MetricParameters):
    tags: list[str] = Field(default_factory=list, description="Required tags.")


class Parameters(MetricParameters):
    radius: float = Field(100.0, gt=0, description="Search radius.")
    label: str | None = Field(None, description="Optional label.")
    filters: Filters = Field(default_factory=Filters, description="Search filters.")
    options: dict[str, JsonValue] = Field(default_factory=dict, description="Options.")

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None
```

Serialization includes every effective field, including defaults and nulls:

```python title="effective_parameters.py"
values = Parameters(options={"enabled": True}).model_dump(mode="json")
assert values == {
    "radius": 100.0,
    "label": None,
    "filters": {"tags": []},
    "options": {"enabled": True},
}
assert Parameters(label=" north ").label == "north"
```

Workers receive defaults and nulls explicitly. They revalidate the complete values for every selected node use, so validators must preserve values after JSON transport.

Lyra copies nested request values at acceptance and declaration values at registration. It gives handlers independent parameter models and returns independent descriptions through inspection. Changing those objects cannot alter retained bindings, effective values, or computation identity. Internal field reads use the retained values without copying them.

## Unsupported model features

The parameter contract excludes:

- `Any`, arbitrary classes, root models, recursive models, and unresolved generic models
- sets, tuples, bytes, datetimes, decimals, and non-string dictionary keys
- aliases, field exclusions, custom serializers, and computed fields
- custom JSON Schema hooks or structural overrides

Registration errors identify the node and field path. Adapt incompatible models explicitly; Lyra does not mutate or clone them to change their settings.

## Node, output, and dependency naming rules

Metric and output names match `^[a-z][a-z0-9_]*$` and cannot start with `lyra_`. Node names are unique across enabled plugins. Output names are local to each producer.

Dependency input names are local Python argument names. They cannot use the reserved names `parameters` or `context`, or collide with declared location or resource arguments.
