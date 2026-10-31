# Phase 13 — Application and authoring integration

After phase 12, read the [index](index.md) and
[decision ledger](../decision_ledger.md). Check that application entry points and
authoring materials consistently expose the implemented contract.

## Work

- Exercise LocalRunner, worker execution, provider resolution, location
  operations, named metric outputs, and mixed dependency graphs. Include both
  fresh and cached execution through application-facing boundaries.
- Check public result inspection and serialization, SDK/API consumers, and
  registry/schema metadata for assumptions about indexes, sparse properties,
  generic constructors, or object-dtype exports. Fix remaining integration gaps.
- Cover consumer assumptions with contextual errors and integration tests. Do
  not add consumer input-schema declarations or change the native frame interface.
- Audit examples, skills, bundled references, and generated authoring material.
  Describe the current supported contract directly: explicit producer schemas,
  column-based identifiers, normalized indexes, typed artifacts, and explicit
  presentation exports. Remove obsolete instructions rather than adding
  historical comparisons or migration guides.
- Explain the current cache revision responsibility where authoring guidance
  needs it: source freshness and unversioned implementation changes are not
  detected by result validation. Keep READMEs short and avoid architecture or
  verification narratives there.

## Acceptance

- Representative application paths produce the same typed behavior through local
  and worker entry points. Cached values satisfy the same output checks as fresh
  results before consumers run.
- Provider ordering, scope, geometry, and errors remain correct in mixed graphs.
  Tests use fake providers/resources and in-process application transports.
- Executable examples use supported construction and declaration APIs, including
  nullable and empty outputs where relevant.
- Public imports and descriptions no longer advertise removed capabilities.
  Authoring materials do not imply arbitrary checks can be recovered from
  artifact metadata or that presentation JSON preserves pandas dtypes.

## Verification and boundary

Follow shared verification, run relevant application and executable-example
checks, and run the full suite. Check documentation links and generated structure
where useful, without adding prose assertions or whole-document snapshots.
Do not build/install wheels, contact external services, or broaden the feature
with unrelated API or deployment changes.
