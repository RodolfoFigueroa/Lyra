# Phase 35 — Authoring guidance

Read the [shared workflow](index.md). Continue after
[phase 34](34-authoring-examples.md). Apply ledger decisions **1–28**.

Complete authoring and consumer guidance against the implemented contract and
the executable examples from the preceding phase.

## Work

- Review plugin authoring instructions, bundled skill references, and public usage
  documentation. Describe supported behavior directly and remove superseded prose
  without adding migration narratives or implementation history.
- Explain the distinctions authors need: geographic versus source identity,
  family structure versus edition metadata, public selection versus calculation
  methodology, canonical routing versus explicit dependencies, and private
  intermediates versus public entry points.
- Document revision coverage, unversioned data, run-wide invalidation, and pinning
  limitations without promising snapshots, automatic freshness, or equivalence
  between alternate routes.
- Connect concise usage guidance to the verified examples for local native
  returns, separate provenance inspection, and remote capability workflows.
  Reserve shallow graph inspection for calculation-relationship questions.
- Follow applicable skill-authoring instructions when updating bundled skills.
  Keep READMEs short and put detailed explanations in appropriate references.

## Acceptance and validation

- Review prose against the accepted ledger and actual APIs; preserve field
  definitions, source/release distinctions, and declared output meaning.
- Check links, schemas, rendering, and executable snippets where relevant.
  Do not add wording assertions or documentation snapshots.
- If a correction changes Python snippets or fixtures, run the index's required
  Python checks and relevant tests. Do not rebuild the example suite unnecessarily.

## Boundary

Do not invent new capabilities to simplify documentation or describe unsupported
transitional APIs. The completion audit follows; no migration guide is required.
