---
title: Supply spatial inputs
description: Select accepted source IDs and understand automatic conversion and selection failures.
contentType: How-to
contentPlan: use/spatial-inputs
sidebar: { label: "Supply Spatial Inputs" }
---

Supply a named ID selection for each [location input](../../plugins/authoring/#declare-spatial-inputs) that your metric requires. Local execution and remote interfaces use the same selection format. This guide explains how to choose accepted sources and interpret selection failures.

## Inspect accepted sources

Inspect the selected metric before choosing inputs. The Hypertext Transfer Protocol (HTTP) application programming interface (API) exposes these contracts:

| Endpoint | What to read |
| --- | --- |
| `GET /metrics` | Public families and the source types each edition accepts |
| `GET /metrics/{name}` | Shared `native_inputs`, `parameters`, and `outputs` |
| `GET /metrics/{name}?edition=your_edition_here` | The named edition’s concrete `bindings` and `accepted_selections` |
| `GET /data-types` | The shared `location_selection_schema` |

Omit `edition` for a family with an implicit variant. Its inspection returns the executable contract directly. The Python client and Model Context Protocol (MCP) tools expose the same contracts.

## Supply each named selection

For each input, provide a collection, unit type, and nonempty list of string IDs:

```json
{"collection": "example:areas", "unit_type": "area", "ids": ["area-1"]}
```

Use only the source types advertised for that input and edition. Lyra removes duplicate IDs while preserving their first-occurrence order. The metric edition determines the concrete source and geographical edition for each accepted scope.

Equal IDs across different geographical editions imply no correspondence. Observations sharing a geography are not interchangeable sources. See [source contracts](../../plugins/location-providers/#declare-concrete-source-contracts) for the identity fields.

Supply every named input independently, including supporting context. For a metric requiring explicit block and lot selections, supply both and check their completeness. [Aggregate selected lots by block](../../plugins/location-example/) demonstrates that calculation.

## Understand automatic selection

Lyra chooses a [canonical route](../../plugins/location-operations/#offer-canonical-public-selection) to convert your selection into the metric’s declared native input. An exact contract match needs no operations. An input without conversions accepts only its native type.

Selection preserves the metric’s declared output granularity. Selecting a state for a tract metric still returns tract rows. A batch selects each related target identity once; it does not group or aggregate results.

Your request supplies IDs, not operation names or pipelines. Alternative routes may produce different results, and the accepted route stays fixed during execution. Explicit dependencies preserve the calculation’s methodology after selection resolves its native inputs.

The [capability example](https://github.com/RodolfoFigueroa/lyra/blob/main/examples/location-plugin/USAGE.md) selects blocks for street ratings and supplies street context independently. It returns street rows and inspects the original selections separately from the resolved native inputs.

## Handle selection failures

Preparation rejects invalid requests before acquisition or job creation. During execution, the provider resolves requested IDs under the pinned contract, then selected operations produce native locations.

| Condition | Outcome |
| --- | --- |
| Malformed selection, unsupported source type, or supplied geometry | Preparation rejects the request |
| Unknown ID | Execution fails with the affected input, source scope, and unresolved IDs |
| Existing source with no related targets | Selection returns an empty set and downstream handlers still execute |
| Unavailable revision or invalid provider output | Execution fails |

Workers keep provider resources available to handlers and close them when execution finishes. Providers declare the coordinate reference system (CRS); calculations perform any needed reprojection. See [spatial results](../results/#understand-spatial-results) for output encoding and [local ID selections](../local-execution/#supply-id-selections) for provider configuration.
