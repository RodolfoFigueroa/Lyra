---
title: Read and inspect location artifacts
description: Read standalone Arrow location files, inspect their contents, and export locations to GIS formats.
contentType: Reference
contentPlan: reference/location-artifacts
sidebar: { label: "Read Location Artifacts" }
---

Use Lyra’s software development kit (SDK) to inspect a standalone Arrow location file, convert it to a GeoDataFrame, or export locations. You can read a copied `locations.arrow` file without the original cache directory or running services.

Import `read_location_artifact` to inspect metadata, preview up to five features, and convert the decoded locations:

```python
from lyra.sdk import read_location_artifact

artifact = read_location_artifact("locations.arrow")
print(artifact.metadata)
print(artifact.preview(limit=5))
locations = artifact.to_locations()
frame = locations.to_geodataframe()
```

The reader returns metadata describing the decoded locations:

- Scope: the collection and unit type
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

Invalid or unsupported contents raise `ArtifactDecodeError` with a reason; import this exception from `lyra.sdk` to handle decoding failures. File access failures raise `OSError`.

## Export locations to GIS formats

Use `export_locations` to share locations with geographic information system (GIS) tools. Pass a decoded artifact or a `Locations` value. GeoParquet is the default format:

```python
from lyra.sdk import export_locations

export_locations(artifact, "exported-locations")
export_locations(locations, "package-export", file_format="geopackage")
export_locations(locations, "shape-export", file_format="shapefile")
```

Each call returns the path to a new directory containing the selected GIS format and `scope.json`. The metadata file records the collection, unit type, and exact CRS declaration. Keep these files together:

- GeoParquet: `locations.parquet`
- GeoPackage: `locations.gpkg`
- Shapefile: `locations.shp` and its companion files

The parent directory must exist. Choose a new destination; an existing destination raises `FileExistsError`. Cache operations and inspection do not create GIS files.

GeoParquet uses the existing Arrow support. GeoPackage and shapefile require `pyogrio` and the corresponding Geospatial Data Abstraction Library (GDAL) write drivers. The exporter loads these optional drivers only when you request those formats.

Exports preserve feature order, string IDs, CRS meaning, coordinates, and property values. Before publishing the directory, the exporter reads the output back and rejects any detected change. It does not reproject, repair geometry, truncate values, or convert scalars to strings to make an export succeed.

### Format limits

The exporter rejects contents that the requested format cannot preserve. All formats reject:

- Columns containing more than one non-null scalar type
- Integers outside the signed 64-bit range
- Geometry structures or dimensions that frame conversion cannot preserve

GeoParquet supports null properties. GeoPackage and shapefile must also satisfy these requirements:

- One geometry type across all features
- Field names that remain distinct when compared without regard to case
- Field names containing only ASCII letters, digits, or underscores, with no leading digit
- No field names matching `fid` or `geom`, regardless of case

Shapefile has additional limits:

- Field names: at most 10 characters
- Values: non-null strings or integers
- Integers: from `-2**53` to `2**53`, inclusive
- Strings: nonempty, without leading or trailing whitespace, and at most 254 UTF-8 bytes

Other driver limitations can also cause an export failure. The exporter rejects output that fails its preservation check before creating the destination directory.

## Inspect and export location values

`Locations` owns immutable columns. Use `len(locations)` to read the row count, or `locations.is_empty` to check for zero rows. `bool(locations)` indicates whether any rows exist. Read scope metadata through `collection`, `unit_type`, and an independent `crs` value.

`locations.ids` is a read-only lazy sequence: indexing reads one ID, and slicing creates another view. Use `list(locations.ids)` to materialize IDs. `property_names` lists stored names in declaration order, and `property(name)` returns a lazy typed scalar sequence. Every row contains every declared property, with `None` representing nulls.

Use `slice(offset, length=None)` for a contiguous range that shares immutable buffers. Use `select_ids(ids)` to select rows in the requested order. Offsets and lengths must be nonnegative integers. ID selections reject duplicates and unknown IDs, and empty selections retain the stored property names.

`to_dataframe()` exports IDs and properties without reading geometry; `to_geodataframe()` includes geometry. Both methods preserve declared data types and use a fresh `RangeIndex`. By default, column names and order follow the stored declaration.

Pass `properties` to select property columns in the requested order, or `properties=[]` to export only structural columns. Use `id_column` and, for GeoDataFrames, `geometry_column` to rename structural columns. Exports reject duplicate property names, unknown properties, and names that conflict with structural columns.

`iter_features()` streams independent feature models in batches of 256 by default; use `batch_size` to change the batch size. `to_features()` returns a list. Use `to_dict()` or `to_json()` for a payload containing features and scope metadata.

These presentation methods export strings, numbers, Booleans, and nulls, and convert dates and timestamps to ISO 8601 strings. Unsupported scalar values raise `ValueError` naming the column and value type. Presentation exports do not preserve pandas data types; use native frames or Arrow artifacts to preserve declared types.

Pass `limit` to bound the number of features these methods export; the default includes all rows. Limits must be nonnegative integers, and batch sizes must be positive integers; neither accepts Boolean values. A zero limit reads no rows. You can change exported objects without affecting the source value.

Call `Locations.from_geodataframe()` with the frame and a `LocationOutputSchema` as `schema`. Alternatively, call `build()` on that schema. Both methods require `collection` and `unit_type`.

Artifact readers validate stored data without loading a producer plugin.

Pass a `Locations` instance to resolved handlers. Content equality compares these attributes:

- Geometry coordinates
- Scalar types
- Row order
- Scope
- CRS
