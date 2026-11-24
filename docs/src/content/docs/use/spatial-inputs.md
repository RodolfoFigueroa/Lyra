---
title: Supply spatial inputs
description: Select accepted source IDs and understand automatic conversion and selection failures.
contentType: How-to
contentPlan: use/spatial-inputs
sidebar: { label: "Supply Spatial Inputs" }
---

Supply a named ID selection for each [location input](../../plugins/authoring/#declare-spatial-inputs) your metric requires. To add or remove records for one run, include an intervention set in the same request. Local and remote runs use the same selection rules.

## Inspect accepted sources

Inspect the selected metric before choosing inputs. The HTTP API exposes these contracts:

| Endpoint | What to read |
| --- | --- |
| `GET /metrics` | Public metric summaries with accepted source provider IDs for each input you supply |
| `GET /metrics/{name}` | Each input’s `target_provider` in `location_inputs`. Look it up in `providers` for its record schema and `accepted_sources`. |
| `GET /data-types` | The shared `location_selection_schema` |

The Python client and Model Context Protocol (MCP) tools expose the same metric contracts.

## Supply each named selection

For each input, supply a provider ID and a list of record IDs. This selection requests one street:

```json
{"provider": "city_streets", "ids": ["street-1"]}
```

Choose a provider ID from the input’s entry in `accepted_selections`. Supply each record ID as a nonempty string, or send `[]` to select no records. Lyra keeps the first occurrence of each ID in the order you supplied.

Matching IDs or columns do not make records from different providers interchangeable. A [provider scope](../../plugins/location-providers/#define-revision-coverage) identifies the dataset through its provider ID, code revision, and origins.

Supply every named input independently, including supporting context. For a metric requiring explicit block and lot selections, supply both and check their completeness. [Aggregate selected lots by block](../../plugins/location-example/) demonstrates that calculation.

## Prepare changes for a catalog

Group all changes for one provider into a single entry in `interventions`. Set `catalog` to its public provider ID and give the set an `id` and a `revision`. Each set needs at least one change.

| Change | What to supply |
| --- | --- |
| Add records | In `add`, include every declared column, even nullable attributes. Each ID must be unique among the additions and absent from the entire base dataset. |
| Remove records | In `remove`, list distinct string IDs that exist in the base dataset. |
| Replace a record | Remove the original ID and add the complete replacement under a new ID. Other records still refer to the original ID. |

Prepare each addition according to the provider’s column types, units, and geometry rules. Obtain missing attributes, geocode addresses, and prepare complex geometries before submission. Reproject coordinates into the provider’s CRS when needed. If an earlier run selected a record you need, you can [read its retained input](../results/#read-retained-location-inputs).

Keep the same set ID and revision when you reorder changes. If you change an addition or removal, change the ID or revision. For a run against the base datasets, omit `interventions` or pass an empty list.

During preparation, Lyra checks every target provider and validates its addition records against its schema. Before computing or reusing a result, Lyra checks the IDs against the base dataset, even for catalogs the metric never reads. Later runs can reuse this check for the same [provider scope and set identity](../../concepts/identity/#identify-a-dataset-with-interventions).

Use a [native Python proposal](../local-execution/#run-with-a-street-proposal) or send [inline JSON over HTTP or MCP](../../reference/mcp-payloads/#submit-an-inline-street-proposal).

## Understand automatic selection

If you select the input’s target provider, Lyra uses its records directly. To select another provider, choose one with a [canonical route](../../plugins/canonical-selection/) to that target. Lyra runs the operations on that route to obtain the target records.

Selection preserves the metric’s declared output granularity. Selecting a state for a tract metric still returns tract rows. A batch selects each related target identity once; it does not group or aggregate results.

Your request supplies IDs, not operation names or pipelines. Alternative routes may produce different results, and the accepted route stays fixed during execution. Explicit dependencies preserve the calculation’s methodology after selection resolves its native inputs.

The [capability example](https://github.com/RodolfoFigueroa/lyra/blob/main/examples/location-plugin/USAGE.md) selects blocks for street ratings and supplies street context independently. It returns street rows and inspects the original selections separately from the resolved native inputs.

## Handle selection failures

Preparation rejects invalid requests before acquisition or job creation. During execution, the provider resolves requested IDs under the provider scope captured during preparation, then selected operations produce native locations.

| Condition | Outcome |
| --- | --- |
| Malformed selection, unsupported source type, or geometry inside an ID selection | Preparation rejects the request |
| Unknown ID | Execution fails with the affected input, source scope, and unresolved IDs |
| Existing source with no related targets | Selection returns an empty set and downstream handlers still execute |
| Unavailable revision or invalid provider output | Execution fails |

Workers keep provider resources available to handlers and close them when execution finishes. Providers declare the CRS; calculations perform any needed reprojection. See [spatial results](../results/#understand-spatial-results) for output encoding and [local ID selections](../local-execution/#supply-id-selections) for provider configuration.
