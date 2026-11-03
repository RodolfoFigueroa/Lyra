---
title: Locations value reference
description: Look up immutable location access, selections, conversions, construction, and equality.
contentType: Reference
contentPlan: reference/locations
sidebar: { label: "Locations Value Reference" }
---

`Locations` stores immutable location data. Use this reference to inspect rows, select IDs, export values, and bind source contracts.

## Inspect rows and properties

Use `len(locations)` to read the row count, or `locations.is_empty` to check for zero rows. `bool(locations)` indicates whether any rows exist. Read scope metadata through `collection`, `unit_type`, and an independent `crs` value.

`locations.ids` is a read-only lazy sequence: indexing reads one ID, and slicing creates another view. Use `list(locations.ids)` to materialize IDs. `property_names` lists stored names in declaration order, and `property(name)` returns a lazy typed scalar sequence. Every row contains every declared property, with `None` representing nulls.

## Select rows

Use `slice(offset, length=None)` for a contiguous range that shares immutable buffers. Use `select_ids(ids)` to select rows in the requested order. Offsets and lengths must be nonnegative integers. ID selections reject duplicates and unknown IDs, and empty selections retain the stored property names.

## Export native tables

`to_dataframe()` exports IDs and properties without reading geometry; `to_geodataframe()` includes geometry. Both methods preserve declared data types and use a fresh `RangeIndex`. By default, column names and order follow the stored declaration.

Pass `properties` to select property columns in the requested order, or `properties=[]` to export only structural columns. Use `id_column` and, for GeoDataFrames, `geometry_column` to rename structural columns. Exports reject duplicate property names, unknown properties, and names that conflict with structural columns.

## Export features and JSON

`iter_features()` streams independent feature models in batches of 256 by default; use `batch_size` to change the batch size. `to_features()` returns a list. Use `to_dict()` or `to_json()` for a payload containing features and scope metadata.

These presentation methods export strings, numbers, Booleans, and nulls, and convert dates and timestamps to ISO 8601 strings. Unsupported scalar values raise `ValueError` naming the column and value type. Presentation exports do not preserve pandas data types; use native frames or Arrow artifacts to preserve declared types.

Pass `limit` to bound the number of features these methods export; the default includes all rows. Limits must be nonnegative integers, and batch sizes must be positive integers; neither accepts Boolean values. A zero limit reads no rows. You can change exported objects without affecting the source value.

## Construct locations and bind contracts

Call `Locations.from_geodataframe()` with the frame and a `LocationOutputSchema` as `schema`. Alternatively, call `build()` on that schema. Both methods require `collection` and `unit_type`.

To construct a value with a concrete contract, call `schema.build_contract(frame, contract)`. This method checks that the contract’s family structure matches the producer schema, then validates the frame. To bind an existing value, call `locations.with_contract(contract)` with a `LocationContract` matching its collection and unit type. This method binds identity; the producer schema remains responsible for validating contents.

Both methods retain the contract’s source, geographical edition, optional edition, and optional data revision. Slices, ID selections, Arrow artifacts, and scoped exports preserve this metadata. Read it through `locations.contract` and `locations.data_revision`.

## Compare location values

Pass a `Locations` instance to resolved handlers. Content equality compares these attributes:

- Geometry coordinates
- Scalar types
- Row order
- Scope
- CRS
- Concrete contract identity, including source and geographical edition

Content fingerprints include contract identity. The data revision remains separate metadata and does not affect content equality.
