# 07 · Unify provider selection and interventions

Planning and execution should select the same location IDs, correspondence pairs, and computation groups for a bound input. Give `BoundLocation.related` and `NativeLocationInput.related_selection` a shared traversal that traces inputs to their common ancestor and follows correspondence pairs forward. Planning can use indexed pairs and represent unknown membership until acquisition. Execution applies the resulting selection to its frame. Preserve pair order, shared targets, completed empty groups, and the independence of unrelated inputs.

## Intervention submission

An omitted `add`, an empty JSON list, and a zero-row native additions frame all mean zero additions. For removal-only requests, both Python clients skip metric discovery and acceptance skips empty-frame column and CRS validation. Keep the accepted provider schema available and validate populated additions against it. An intervention with no additions and no removals is invalid. Remove `add_omitted` and field-presence branches from preparation and worker handoff. Provenance records the same actions for all three spellings.

Encode intervention geometry directly with column-level `to_wkt` and read it with `GeoSeries.from_wkt`, using the current precision, explicit JSON nulls, and the provider's declared CRS. Integers travel as decimal strings. Retained and database representations that consume WKB continue to use it.

## Managed providers and staging

`ManagedLocationProvider` owns the common query logic for exact IDs and interventions. Filter the base relation on `location_id` for exact lookups. Membership checks also use the base relation. Stage additions and removals when a selector first runs a predicate query against the modified provider data. Exclude removed base IDs, append additions, and reuse the resulting relation for subsequent predicate queries in that execution. PostGIS and Parquet adapters own backend-specific materialization, geometry conversion, and staging names.

Use unique staging names so providers sharing a session cannot overwrite one another's data. The Parquet provider closes its DuckDB connection and the execution's `PostgresIO` owner closes its PostgreSQL session on success and failure. Connection close releases temporary staging, including after partial setup. Remove per-table unregister and DROP operations, cleanup callbacks, and reverse-order cleanup. A custom `PostgresIO` that returns a session to a pool must clear its temporary objects before returning it. Propagate selector and COPY errors.

## Verification

Exercise multihop and sibling selection. For each intervention target, verify that omitted additions, an empty list, and a zero-row frame preserve identical removals through worker handoff. Cover WKT round trips for high-precision, 3D, empty, and missing geometry. Use in-process fakes to verify independent staging for two providers on one session, distinct values across executions, and closure of the owning connection after failed setup.
