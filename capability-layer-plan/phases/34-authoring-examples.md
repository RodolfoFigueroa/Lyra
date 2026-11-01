# Phase 34 — Authoring examples

Read the [shared workflow](index.md). Continue after
[phase 33](33-shallow-dependency-inspection.md). Apply ledger decisions **1–28**.

Complete a small set of executable examples using the finished APIs. Earlier
phases should already have kept affected examples working.

## Work

- Reuse and extend representative service-free examples for concrete location
  contracts, provider registration, implicit and named editions, Python factories,
  concrete dependencies, and private intermediates.
- Demonstrate canonical opt-in, route preferences, native-only inputs, batched
  conversion, empty intermediates, and multiple named inputs through shared
  fixtures rather than a separate elaborate example for every combination.
- Show declared location revisions, unversioned data, and run-wide invalidation,
  preserving source/release meanings and metric output granularity.
- Exercise local native returns with separate inspection and a representative
  remote discovery, edition-selection, submission, polling, and result workflow
  using in-process fakes.
- Use actual implementation spellings and remove superseded executable patterns.
  Keep explanatory comments focused on what readers need to run each example.

## Acceptance and validation

- Run representative examples in the existing project environment without live
  services, credentials, sockets, wheel installation, or package smoke checks.
- Check the behaviors the examples are meant to illustrate, including explicit
  selection of named editions and preserved calculation methodology after routing.
- Run the index's Python checks and relevant example/workflow tests for any Python
  changes. Reuse behavioral coverage instead of asserting documentation wording.

## Boundary

This phase completes executable examples and their immediate usage instructions.
The next phase reconciles broader documentation and bundled authoring guidance
against those examples and the implemented contract.
