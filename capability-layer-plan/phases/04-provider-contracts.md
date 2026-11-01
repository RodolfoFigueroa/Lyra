# Phase 04 — Provider contracts

Read the [shared workflow](index.md). Continue after
[phase 03](03-location-value-identity.md). Apply ledger decisions **2, 12, 20, 22**.

Make provider registration and lookup reference the same concrete contracts as
the location values established in the preceding phase.

## Work

- Replace provider scope declarations that cannot distinguish concrete source or
  geographic editions with references to the shared location contracts.
- Validate registration and serialize enough information for catalog construction
  without creating provider instances, reading credentials, or acquiring data.
- Select a concrete contract during lookup and construct its resolved values using
  the existing typed location machinery. Preserve complete geometry and attributes;
  do not substitute another observation of the same geographic units.
- Keep acquisition in the current resource lifecycle. Leave a clear binding point
  for execution-time revision enforcement without claiming metadata creates a
  snapshot.
- Update directly affected provider implementations, callers, and fixtures. Runtime
  snapshot and artifact identity should already be complete; fix only integration
  gaps in that work.

## Acceptance and validation

- A fake provider exposes multiple concrete editions within one family and returns
  values with the requested contract identity.
- Malformed or conflicting registrations fail without service access.
- Manifest round trips retain contract identity and source metadata.
- Verify existing acquisition setup and cleanup using fakes, and run the index's
  Python checks and affected provider/resolver tests.

## Boundary

Do not expand provider administration or add adapter registries. Full revision
pinning and unknown-ID execution semantics belong to
[pinned location acquisition](17-pinned-location-acquisition.md).
