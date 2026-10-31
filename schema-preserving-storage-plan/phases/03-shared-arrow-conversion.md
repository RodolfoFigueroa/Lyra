# Phase 03 — Shared Pandera–Arrow conversion

After phase 02, read the [index](index.md) and
[decision ledger](../decision_ledger.md). Build the conversion foundation shared
by metrics and locations.

## Work

- Establish Arrow storage types and native reconstruction information from the
  declaration independently of observed rows. Use Pandera, Arrow conversions,
  and native pandas metadata with minimal glue; avoid a separate Lyra dtype
  hierarchy or a large handwritten mapping.
- Convert validated non-geometry columns to typed Arrow columns and reconstruct
  their declared pandas dtypes. Preserve column order, names, nullability, and
  relevant type parameters. Keep geometry on its existing supported path.
- Normalize supported missing markers to a single logical null, allowed only
  where declared nullable. Reject infinity, overflow, and incompatible values.
  Storage conversion must not add coercion, truncate precision, or replace
  integers with floats to accommodate nulls.
- Cover supported numeric widths, signed/unsigned boundaries, booleans, strings,
  timestamp units/timezones, and Arrow-backed date-only declarations. Preserve
  signed zero. Apply coercion only through the declared validation contract.
- Use native categorical support and straightforward metadata integration. Give
  ordinary categories basic coverage and record known native empty-category
  limitations without adding repair sidecars or custom codecs.

## Acceptance

- Populated, empty, and all-null variants retain the declared types through the
  conversion round trip. Types do not depend on the first non-null value.
- Temporal precision and timezone survive exactly; date-only values do not
  become timestamps or strings. Integer bounds and floating sign bits survive.
- Undeclared coercion, infinity, and out-of-range values fail clearly. Tests
  distinguish legitimate declared coercion from storage-induced data changes.
- Index metadata is absent from stored identity and reconstruction. Producer
  frames remain unchanged, and no blanket object-dtype reconstruction is used.

## Verification and boundary

Follow shared verification and run focused declaration/conversion tests using
phase 01 fixtures. Keep the helper private until its callers are ready. Do not
replace runtime wrappers or artifact formats in this phase. Ensure native
limitations are explicit rather than concealed by fallback encodings.
