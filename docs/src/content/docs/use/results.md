---
title: Read and download results
description: Work with named tables, inspect provenance, and save results before they expire.
contentType: How-to
contentPlan: use/results
sidebar: { label: "Read And Download Results" }
---

Inspect a completed job’s named tables, then fetch them in Python or download the complete result. Each output name matches the metric’s declaration. Intermediate dependency tables remain inside the calculation.

## Inspect before downloading

Use `client.results.inspect(job_id)` or `GET /jobs/{job_id}/result/inspection` to read saved summaries without loading the complete tables. For a successful job, inspection includes:

- Output declarations and row counts
- Numeric summaries and preview rows
- Compact [provenance](../../concepts/provenance/): a summary of the request and execution identity
- The remaining result lifetime

The `download` field gives the relative path to the full result. Truncation fields record what the inspection omits.

A reference such as `lyra://results/your_job_id_here` identifies the retained result. Inspection and download client methods accept either this reference or a job ID. The reference does not extend the retention period.

## Retrieve tables in Python

Install `lyra[client,tables]` to convert a successful job’s JavaScript Object Notation (JSON) result to named tables. Pass the job ID to your connected client.

```python
tables = client.result_tables(job_id)
table = tables["maintenance"]
print(table.head())
```

`client.results.tables(job_id)` is equivalent. Both return a dictionary, including for a single output. Async clients await these methods.

Ordinary outputs become pandas DataFrames. Spatial outputs become GeoPandas GeoDataFrames. Empty tables retain declared columns, including the active geometry column for spatial outputs.

The client preserves row order and explicit identifier columns when it reconstructs tables from JSON. It does not restore native indices or data types. Dates remain strings in the ISO 8601 standard format, and pandas infers missing-value representations. Failed jobs raise `MetricRunError`.

## Save the complete JSON result

Save a successful job’s complete result to a local file with the download method:

```python
client.results.download(job_id, "result.json")
```

The Hypertext Transfer Protocol (HTTP) equivalent is `GET /jobs/{job_id}/result/download`, using the agent Bearer credential. The client checks the HTTP response status before opening the destination file.

The successful download contains the same full JSON object as `GET /jobs/{job_id}/result`:

| Field | Meaning |
| --- | --- |
| `outputs` | Output names mapped to record arrays or GeoJSON FeatureCollections |
| `output_metadata` | The declarations that describe those outputs |
| `provenance` | The submitted request and execution identity |

Empty ordinary outputs are empty arrays. Empty spatial outputs have an empty `features` array. Missing cells become null; dates and timestamps become ISO strings.

## Inspect execution provenance

Read full `provenance` from the result or download. Use these fields to compare your request with the execution:

| Field | What to inspect |
| --- | --- |
| `provenance.accepted.plan.request` | Submitted selections and parameters; `supplied_fields` identifies which request fields you supplied |
| `provenance.accepted.plan.parameters` | Effective parameter values, including defaults |
| `provenance.execution` | Resolved input summaries and node outcomes from the execution attempt; `null` if execution has not started |

A planned dependency may use a cached result without running its handler. [Understand execution provenance](../../concepts/provenance/) explains how to interpret reuse and failures. For local runs, call `runner.inspect()` to read the accepted plan and execution observations.

## Understand row identity

When joining results to other data, use the identifier columns documented by the producing plugin. The plugin determines row counts, ordering, and identifiers, which may differ from the features in your original request.

[Canonical selection](../../concepts/selection/) preserves what each output row represents. For example, a tract metric selected through a state still returns tract rows. Your selected IDs do not automatically identify output rows. Your plugin or application must explicitly aggregate, join, or align data when needed.

## Understand spatial results

The server serializes spatial tables as GeoJSON using World Geodetic System 1984 (WGS84). Client-reconstructed GeoDataFrames therefore use WGS84 too. Request provenance records the submitted ID selections. The provider declares the coordinate reference system (CRS) used during resolution.

[Local execution](../local-execution/) returns native GeoDataFrames with declared data types, a fresh `RangeIndex`, and their original CRS. Server serialization leaves the worker’s native graph table unchanged.

## Save results before retention ends

Download results you need before the inspection’s remaining lifetime reaches zero. The operator’s retention setting controls how long you can access completed jobs and their results.

The server stores native output tables in [immutable filesystem artifacts](../../concepts/caching/#store-and-retain-artifacts). Computation lookups and retained jobs can reference the same artifact. Redis stores job state, provenance, and the artifact reference.

An unavailable reference does not tell you whether the job never existed or its retention period ended. Repeated downloads cannot recover missing retained data. A new submission creates another job, which may reuse cached calculations.

Model Context Protocol (MCP) previews contain only a bounded sample. Use the authenticated [download handoff](../mcp/#4-download-the-complete-result) to obtain the full result.
