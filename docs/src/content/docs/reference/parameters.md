---
title: Parameter and handler rules
description: Look up supported parameter types, model settings, and function signature constraints.
contentType: Reference
contentPlan: reference/parameters
sidebar: { label: "Parameter And Handler Rules" }
---

Use this page when a registration or manifest error points to a model field or handler signature. For a worked adapter, see [Define metric inputs](../../plugins/authoring/).

## Handler signatures

Handlers are synchronous functions. Lyra accepts positional-or-keyword and keyword-only arguments and calls handlers with keyword arguments.

| Argument | Annotation and rule |
| --- | --- |
| `parameters` | A concrete compatible Pydantic model; omit it when there are no ordinary inputs |
| Any location argument name | `Locations`; required and non-nullable when declared; optional `Annotated` metadata uses `Field(description="…")` |
| `context` | Optional service argument, annotated `RunContext` for runtime services |
| Dependency arguments | Names match the decorator’s `inputs` mapping |

Lyra rejects argument defaults, positional-only arguments, variadics, async handlers, and generators. Return annotations are optional; the explicit `outputs` declaration governs runtime result validation.

## Model settings

The root parameter model and every nested model must set `extra="forbid"` and `validate_default=True`. Inherited settings count. `MetricParameters` supplies these settings for the usual case.

Root parameter fields require descriptions. Nested field descriptions and examples are optional. Fields use ordinary Pydantic names without aliases.

## Supported values

Parameter models accept these JSON-compatible values when their members follow the same contract:

| Type | Notes |
| --- | --- |
| Strings and booleans | Ordinary JSON scalar values |
| Integers and finite floats | Non-finite numbers are excluded |
| Null | Declare nullability explicitly |
| `Literal` enums | Values use one homogeneous scalar type |
| Lists | Use supported element types |
| String-keyed dictionaries | Open mappings with supported value types |
| Unions and nested models | All members follow the same contract |

Use lists with fixed output columns when a calculation accepts several values. Independent parameter sweeps can be separate jobs.

## Unsupported model features

The parameter contract excludes:

- `Any`, arbitrary classes, root models, recursive models, and unresolved generic models
- sets, tuples, bytes, datetimes, decimals, and non-string dictionary keys
- aliases, default factories, custom serializers, and computed fields
- custom JSON Schema hooks or structural overrides

Defaults must be deterministic JSON values. Manifest generation validates both defaults and examples against their schemas. A Pydantic schema-generation limitation can reject a literal dictionary default containing a `$ref` key; ordinary dictionary parameters remain supported.

Registration errors identify the node and field path. Adapt incompatible models explicitly; Lyra does not mutate or clone them to change their settings.

## Node, output, and dependency naming rules

Metric and output names match `^[a-z][a-z0-9_]*$` and cannot start with `lyra_`. Node and output namespaces are globally unique across enabled plugins. An output can share its own producer’s node name.

Dependency input names are local Python argument names. They cannot use the reserved names `parameters` or `context`, or collide with declared location or resource arguments.
