# 06 · Validate producer tables once

A plugin hands Lyra a frame that already has its declared types. Lyra validates each fresh frame once, owns the accepted data, and retains enough schema information to serve a typed Arrow download after the producing plugin is gone.

## Producer contract

At registration, reject `coerce=True` on metric output and provider schemas or individual columns, for both Pandera `DataFrameSchema` and `DataFrameModel` declarations. Each named metric output must declare at least one column. A geometry-only output is valid even though it has no scalar columns.

Plugin authors convert source values before returning them and supply read-only validation predicates. Validate fresh frames with all declared Pandera checks, including dtype, nullability, and uniqueness. Signed 64-bit dtype validation covers the integer range, so remove the per-value `Decimal` scan. Apply Lyra's opaque location ID, active geometry, CRS, and serializability rules once where applicable. Retire tests dedicated to mutations inside validation callbacks. Keep the ownership test that modifies the producer's original frame after handoff and verifies that the runtime's accepted data is intact.

For metrics using `cache="per_location"`, the validation boundary is the frame returned by the handler, which may contain only the locations with cache misses. Document the [partition-independence requirement](09-metric-cache-decisions.md#per-location-validation) in the plugin authoring guidance.

For provider records and intervention additions, a declared pandas string column accepts both Python and PyArrow string storage. A nullable `bool` declaration accepts pandas `boolean`. Apply those rules to populated, empty, and all-null columns without losing values or bypassing declared checks. Update plugin guidance to require a new metric `code_revision` when a validation change could reject a previously accepted output.

## Retained schema and geometry

Derive Arrow fields once per loaded table registration from stored column order, logical types, nullability, units, descriptions, and geometry CRS. Rebuild them from the registration alone on SQLite reopen. Attach dataset scopes when assembling each download schema so the selected intervention's metadata stays specific to that download.

Use native PyArrow conversion for validated scalar columns. Remove the custom pandas string-metadata rewrite and the special row-count handling for outputs with zero declared columns. A frame read back into pandas may use either supported string backend. Its logical types, column order, values, and nulls must survive the round trip.

Use retained DataFrames and Arrow downloads to exercise the production WKB path. Remove `geometry_buffers.py`, `geometry_shapely.py`, the tests specific to their standalone codec, and the unused `data/location_scope.py` validator. Limit Arrow conversion tests to registered output types. Replace zero-column row-count tests with a registration rejection case and a valid geometry-only round trip. Verify typed zero-row and all-null columns, nullable integers above 2⁵³, geometry-only output, and empty versus null geometry wherever the schema permits both. Provider schemas may enforce stricter geometry rules. Repeat retrieval after reopening the store without loading the plugin and check the declared CRS.
