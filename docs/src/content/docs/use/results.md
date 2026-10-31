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

A stable reference such as `lyra://results/JOB_ID` identifies the retained result. Descriptor and download client methods accept either this reference or a job ID. The reference does not extend the retention period.

## Retrieve tables in Python

With a connected Python client and a successful job ID:

```python
tables = client.result_tables(job_id)
table = tables["smoke_table_metric"]
print(table.head())
```

`client.results.tables(job_id)` is equivalent. Both return a dictionary, including for a single output. Async clients await these methods.

Ordinary outputs become Pandas DataFrames. Spatial outputs become GeoPandas GeoDataFrames. Empty tables retain declared columns, including the active geometry column for spatial outputs.

These tables are reconstructed from JSON. Row order and explicit identifier columns survive. Native indices and dtypes do not: dates remain ISO strings and Pandas infers missing-value representations. Failed jobs raise `MetricRunError`.

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

## Understand row identity

When joining results to other data, use the identifier columns documented by the producing plugin. The plugin determines row counts, ordering, and identifiers, which may differ from the features in your original request.

Your selected IDs do not automatically identify output rows. Your plugin or application must explicitly aggregate, join, or align data when needed.

## Understand spatial results

The server serializes spatial tables as GeoJSON using World Geodetic System 1984 (WGS84). Client-reconstructed GeoDataFrames therefore use WGS84 too. Request provenance records the submitted ID selections. The provider declares the coordinate reference system (CRS) used during resolution.

[Local execution](../local-execution/) returns native GeoDataFrames with declared data types, a fresh `RangeIndex`, and their original CRS. Server serialization leaves the worker’s native graph table unchanged.

## Save results before retention ends

Completed results remain available for the operator’s configured retention period. Download anything you need before the descriptor’s remaining lifetime reaches zero.

An unknown or expired reference cannot distinguish a never-existing job from one whose retention ended. Missing retained data cannot be recovered by repeated downloads. A new submission performs a new calculation.

MCP previews contain only a bounded sample. Use the authenticated [download handoff](../mcp/#4-download-the-complete-result) to obtain the full result.
