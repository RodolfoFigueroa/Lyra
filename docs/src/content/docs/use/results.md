---
title: Read and download results
description: Work with named tables, inspect provenance, and save results before they expire.
contentType: How-to
contentPlan: use/results
sidebar: { label: "Read And Download Results" }
---

When a metric succeeds, it returns one or more named tables described by its output declaration. Use those names to select the results you need; intermediate dependency tables remain inside the calculation.

## Inspect before downloading

Use `client.results.descriptor(job_id)` or `GET /jobs/{job_id}/result/descriptor` to inspect output declarations, row counts, request provenance, and remaining lifetime.

A reference such as `lyra://results/your_job_id_here` identifies the retained result. Descriptor and download client methods accept either this reference or a job ID. The reference does not extend the retention period.

## Retrieve tables in Python

With a connected Python client and a successful job ID:

```python
tables = client.result_tables(job_id)
table = tables["smoke_table_metric"]
print(table.head())
```

`client.results.tables(job_id)` is equivalent. Both return a dictionary, including for a single output. Async clients await these methods.

Ordinary outputs become Pandas DataFrames. Spatial outputs become GeoPandas GeoDataFrames. Empty tables retain declared columns, including the active geometry column for spatial outputs.

The client reconstructs these tables from JavaScript Object Notation (JSON), preserving row order and explicit identifier columns. It does not restore native indices or data types. Dates remain ISO 8601 strings, and pandas infers missing-value representations. Failed jobs raise `MetricRunError`.

## Save the complete JSON result

Save a successful job’s complete result to a local file with the download method:

```python
client.results.download(job_id, "result.json")
```

The HTTP equivalent is `GET /jobs/{job_id}/result/download`, using the agent Bearer credential. The client validates HTTP errors before opening the destination file.

The successful download contains the same full JSON envelope as `GET /jobs/{job_id}/result`:

| Field | Meaning |
| --- | --- |
| `outputs` | Output names mapped to record arrays or GeoJSON FeatureCollections |
| `output_metadata` | The declarations that describe those outputs |
| `provenance` | The submitted request and execution identity |

Empty ordinary outputs are empty arrays. Empty spatial outputs have an empty `features` array. Missing cells become null; dates and timestamps become ISO strings.

## Inspect execution provenance

Use provenance to distinguish what Lyra accepted from what it observed during execution. Results identify the public metric and selected edition alongside output metadata. The descriptor records these details:

- Validated original selections
- Effective parameters, including defaults
- Concrete contracts and the canonical route for each input
- Pinned revisions, including bindings without a revision
- The optional run-wide cache revision

When Lyra observes native inputs, it records their counts and content fingerprints. These fingerprints identify the resolved contents for comparison without embedding every intermediate table.

Read accepted bindings separately from execution observations. A planned dependency may not have executed; Lyra marks cached results as reused. When Lyra reuses a result, it records this run’s selections and route.

Matching schemas do not make source observations equivalent. Keep source meaning, field definitions, observation periods, releases, and geographical editions distinct when interpreting results.

Provenance does not create replayable snapshots or guarantee historical source availability. For local runs, inspect provenance separately with `runner.inspect()` after retrieving native tables.

Use the executable contract and result metadata to interpret a result. Reserve [dependency inspection](../mcp/#inspect-calculation-relationships) for questions about the calculation’s relationships.

## Understand row identity

When joining results to other data, use the identifier columns documented by the producing plugin. The plugin determines row counts, ordering, and identifiers, which may differ from the features in your original request.

Canonical selection preserves the declared output meaning and granularity: a tract metric selected through a state still returns tract rows. Your selected IDs do not automatically identify output rows. Your plugin or application must explicitly aggregate, join, or align data when needed.

## Understand spatial results

The server serializes spatial tables as GeoJSON using World Geodetic System 1984 (WGS84). Client-reconstructed GeoDataFrames therefore use WGS84 too. Request provenance records the submitted ID selections. The provider declares the coordinate reference system (CRS) used during resolution.

[Local execution](../local-execution/) returns native GeoDataFrames with declared data types, a fresh `RangeIndex`, and their original CRS. Server serialization leaves the worker’s native graph table unchanged.

## Save results before retention ends

Completed results remain available for the operator’s configured retention period. Download anything you need before the descriptor’s remaining lifetime reaches zero.

An unknown or expired reference cannot distinguish a never-existing job from one whose retention ended. Repeated downloads cannot recover missing retained data. A new submission performs a new calculation.

Model Context Protocol (MCP) previews contain only a bounded sample. Use the authenticated [download handoff](../mcp/#4-download-the-complete-result) to obtain the full result.
