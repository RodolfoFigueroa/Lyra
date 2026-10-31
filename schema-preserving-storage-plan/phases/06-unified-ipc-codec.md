# Phase 06 — Unified IPC codec

After phase 05, read the [index](index.md) and
[decision ledger](../decision_ledger.md). Implement the artifact foundation before
changing cache publication paths.

## Work

- Encode typed metric tables and location snapshots through one straightforward
  uncompressed Arrow IPC implementation. Preserve the existing geometry contract
  and native pandas reconstruction metadata; shared storage need not imply a
  new public result type.
- Store the type contract, structural/spatial metadata, protocol versions, and
  content fingerprints needed for independent reconstruction and verification.
  Do not serialize executable validation functions.
- On reads, check the supported protocol, Arrow structure, metadata consistency,
  stored types/nullability, and content integrity before returning usable data.
  Establish immutable ownership for snapshots, including mutable byte inputs.
- Reject malformed, truncated, tampered, or unsupported artifacts with contextual
  errors. Support only the new representation in this codec.
- Prepare narrow codec entry points that runtime and cache callers can adopt
  without duplicating conversion rules. Use native categorical behavior and keep
  the known empty-category limitations visible.

## Acceptance

- Metric and location round trips retain values, declared frame dtypes, order,
  spatial metadata, and fingerprints for the supported fidelity matrix.
- Empty/all-null columns survive. Artifact reads reconstruct a fresh RangeIndex,
  independently of the original producer index.
- Changed payloads or identity-relevant metadata fail integrity checks.
  Unsupported versions and malformed type metadata fail clearly.
- A standalone decode works without a registered plugin and does not claim to
  execute producer checks. Returned immutable contents cannot alias mutable
  input bytes.
- Writes use uncompressed IPC without compression options, backend selection,
  compatibility readers, or category sidecars.

## Verification and boundary

Follow shared verification and use temporary files/bytes for codec tests. Test
both table kinds and fingerprint round trips. Leave cache locks, bundle
publication, and runtime routing to later phases; the codec must already be ready
for the location runtime switch in phase 08.
