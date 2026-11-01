# Phase 17 — Pinned location acquisition

Read the [shared workflow](index.md). Continue after
[phase 16](16-shared-request-preparation.md). Apply ledger decisions **2, 9, 16, 20, 22, 26**.

Resolve prepared source selections within the executor's resource lifecycle,
honoring their concrete contracts and pinned revision values.

## Work

- Connect prepared source bindings to provider acquisition. Resolve complete
  batches under the selected source and geographical edition rather than using
  collection/unit type alone.
- Require the provider/execution boundary to honor a declared revision or fail
  clearly. A provider may reject an unavailable older revision; historical storage
  and snapshots are not required. Do not substitute current data under an older
  revision identity.
- Preserve unversioned acquisition when no revision was declared. Do not invent a
  freshness guarantee or attempt to detect unreported external changes.
- Validate returned identities, schema, geometry conventions, attributes, and
  ordering before constructing or reusing immutable location values.
- Fail unresolved requested IDs with the input name, source scope, and missing
  IDs. Do not silently discard unknown IDs or conflate them with a known source
  unit whose later conversion yields no targets.
- Ensure declared revisions used by subsequent explicit or canonical operations
  can be honored through the same execution contract, not only initial provider
  resolution.

## Acceptance and validation

- Use fake providers that honor a requested revision, reject it, or return invalid
  data. Test mixed known/unknown IDs and wholly unresolved selections.
- Check that acquisition begins only after preparation and uses the existing
  resource setup/cleanup lifecycle on both success and failure.
- Verify edition-specific identity and revision metadata survive acquisition.
- Run the index's Python checks and affected resolver, provider, and lifecycle
  tests.

## Boundary

Do not execute canonical routes yet or add mandatory acquisition after an operation
already returns complete target rows. Revision pinning is a contract with data
producers, not automatic snapshot management.
