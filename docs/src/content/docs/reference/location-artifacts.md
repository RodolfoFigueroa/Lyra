---
title: Read and inspect location artifacts
description: Read standalone Arrow location files and inspect decoded contents and integrity.
contentType: Reference
contentPlan: reference/location-artifacts
sidebar: { label: "Read Location Artifacts" }
---

Use `lyra.data` to inspect a standalone Arrow location file, convert it to a GeoDataFrame, or export locations. You can read a copied `locations.arrow` file without the original cache directory or running services.

Import `read_location_artifact` to inspect metadata, preview up to five features, and convert the decoded locations:

```python {3,5-7}
from lyra.data import read_location_artifact

artifact = read_location_artifact("locations.arrow")
print(artifact.metadata)
print(artifact.preview(limit=5))
locations = artifact.to_locations()
frame = locations.to_geodataframe()
```

The reader returns metadata describing the decoded locations:

- Scope: the collection and unit type
- Concrete contract: source and geographic identity, plus the data revision if the value declares one
- Coordinate reference system (CRS)
- Property keys and feature count
- `columns`: column names in storage order, with each column’s Arrow type and whether it allows nulls
- `id_column` and `geometry_column`: names of the ID and geometry columns
- Content fingerprints: hashes computed from the location data
- `producer_schema_validated`: always `False`

Reading validates the location data and recomputes its fingerprints to check content integrity. Fingerprints do not establish authenticity. The reader needs no cache manifest or producer plugins, and it does not validate against a producer schema.

Previews return independent feature dictionaries and preserve:

- Declared properties and nulls
- JSON-compatible scalar values
- Coordinate dimensions

Set `limit` to a nonnegative integer to bound the number of features the preview converts to dictionaries. Reading still validates the whole artifact. You can modify the returned metadata, previews, and frames without changing the decoded locations or source file.

Conversion uses `Locations.to_geodataframe()` and rejects data that a GeoDataFrame cannot preserve, including unsupported coordinate dimensions. See [location operations](../../plugins/location-operations/) for the location model and frame conversion contract.

Invalid or unsupported contents raise `ArtifactDecodeError` with a reason; import this exception from `lyra.data` to handle decoding failures. File access failures raise `OSError`.

## Export locations to GIS formats

Follow [Export locations to GIS formats](../../use/location-exports/) for export commands, optional drivers, and format limits.

## Inspect and export location values

Use the [Locations value reference](../locations/) for access, slicing, conversions, construction, and equality. Artifact readers validate stored data without loading a producer plugin.
