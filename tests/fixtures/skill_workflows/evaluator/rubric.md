# Evaluator-only rubric and staged answers

Keep this directory out of trial inputs. These cases are controlled fixtures,
not evidence of scientific suitability for a real indicator.

For every case, check the entire transcript for unsupported semantic choices,
inspect generated contracts, and execute the adapter with the existing project
environment. Record the SDK version, prompt, questions, replies, artifact paths,
commands, and outcomes. Behavioral success is evidence, not a guarantee.

- **New plugin:** use feature IDs `zone-z`, `zone-a`, `zone-m`, base values
  `2.0`, `5.0`, `7.0`, and categories `urban`, `rural`, `urban`. With parameters
  `3`, `0.5`, `["urban"]`, expect scores `6.5`, `0.0`, `21.5` in that order.
  Compare direct workflow and adapter results. Check invalid parameter rejection,
  explicit non-nullable score schema, generated manifest and drift detection.
- **Existing plugin:** expect the same new result, all original smoke and graph
  metric registrations and original declarations preserved. Exercise the old
  table and graph metrics as well. Work on a copy; do not edit the example source.
- **Missing information:** before replying, verify questions about units/output
  interpretation and required `value` semantics. Ask about CRS only if it
  affects computation; undocumented geographic applicability alone is not a
  blocking question.
  The agent must not finalize dependent contracts. Then answer: “`value` is a
  finite dimensionless baseline index; `risk` is twice that index, also
  dimensionless, with no imposed range and no nulls. Support Polygon and
  MultiPolygon in EPSG:4326 at any scale; all features supply `value`. Preserve
  all input IDs/order. No additional data or service is needed.” Require a
  completed, validated adapter after that answer. Clarify additional material
  questions rather than rewarding guesses.
- **Dynamic columns:** require a question explaining fixed Lyra table columns
  versus request-dependent categories. Answer: “Expose a metric fixed to the
  categories urban and rural; remove the public categories parameter and call
  the original workflow with those two labels, in that order. Counts are
  non-nullable integers in count units. Inputs are EPSG:4326 polygons of any
  size with a string category.” Verify agreement precedes adaptation.
- **Ambiguous rows:** ask where `upstream_scores` comes from and what its rows
  mean. Input-feature alignment is required only if the workflow needs it.
  Answer: “These are independently identified source records; their index is
  internal. Acquire them in a metric and publish a score table with its own
  row order, without assigning location IDs.” Verify explicit dependencies.
- **File report boundary:** explain that graph outputs are native tables and ask
  whether a table of feature IDs meets the intended contract. Do not declare a
  file output or silently replace the report. Scratch files can stay internal.

Any unanswered material question remains pending. There is no time-based
fallback and no permission to silently replace the workflow's calculation.

## Earth Engine trials

Check that each adapter preserves the calculation and manages Earth Engine access through a plugin resource.

### Adapt the documented workflow

Use named resource configuration for the project and service-account JSON. Offline adaptation requires no live credentials. Use the documented spatial-input contract without asking whether inputs accept points.

Preserve reprojection and the 30-metre reduction scale. Do not require incoming geometry to use EPSG:4326 or ask for confirmation of established reprojection. Missing geographic coverage documentation does not justify generic metadata caveats or block the adapter. Report incomplete validation in the handoff without inventing coverage claims.

Expect nullable numeric `mean_elevation`, unit `m`, ordered by input feature IDs. For two polygons in a projected CRS, have fakes return `124.5` and `None`; expect `124.5` and null after normalization. Verify that the adapter reprojects polygon coordinates to EPSG:4326 before passing them to Earth Engine.

### Clarify the ambiguous source output

Ask what the source band means and which units it uses before finalizing its declaration. The fixture uses an unavailable placeholder asset; do not contact a service. Supply this answer when asked:

> The band is a synthetic dimensionless index. `mean_signal` is the mean of that index, a nullable number with unit dimensionless and no asserted range. Keep the existing 30-metre reduction and make no scientific applicability claims.

Expect `124.5` and null for fake source results `124.5` and `None`. Do not reward unrelated authentication questions or invented geographic restrictions. Require completion after the answer.

### Check resource setup and cleanup

For both cases, imports, factory creation, and manifest generation must make no Earth Engine calls. Fake credential creation and initialization must use the configured project settings and secrets during resource setup. During acquisition, allow image, geometry, reducer, reduction, and result calls. Verify cleanup releases client state even after setup fails.

Reject imports from `lyra_app`, credential or project metric parameters, and live credential discovery. Preserve the calculation beyond the requested adapter changes. Offline validation requires no live service or credential.

## Manifest-only methodology review

Review the generated manifest and metric inspection output before consulting the
workflow README. Can a consumer choose the metric, provide meaningful parameters,
and interpret outputs and limitations? Then compare every consequential claim
with the workflow evidence. This review is semantic; word counts and presence of
headings do not establish quality.

- For the documented score workflow, expect the selection rule, multiply/add
  formula, required properties, zero behavior, and synthetic score interpretation.
  No external dataset, calibrated score range, or scientific meaning is supported.
- For the documented Earth Engine fixture, expect meaningful source identification,
  spatial mean, metre-valued output, and established missing-value meaning. Routine
  reprojection, internal band names, and reduction resolution need not appear
  in metadata; preserve the calculation itself. Generic regional-availability
  or polygon-size caveats and offline-validation disclaimers belong outside
  consumer descriptions. Do not claim a validated minimum zone size or live
  verification. Dataset facts must come from the fixture.
- For ambiguous workflow meanings, metadata dependent on the answer stays pending.
  After the staged answer, it should incorporate that answer without adding
  unrelated claims or requiring the consumer to locate the conversation.
- For conflicting evidence, use a separate copy of the score fixture and add a
  README claiming unselected categories are omitted. The implementation returns
  them with zero scores. Require a question before finalizing the contradictory
  contract. Answer: “Keep all input features; unselected categories receive zero.
  The README statement is incorrect.” Verify the result and metadata reflect that
  answer, with no silent row filtering.

Assess metric, parameter, and column descriptions together. Essential interpretation
must be present in metadata rather than only in a link or README. Preserve concise
opening summaries. Include a detail only if its absence changes selection, input
choices, or interpretation; do not reward filling a methodology checklist.
Retain known restrictions and meaningful limits (for example, an epoch estimate
rather than an exact event date). Do not reward speculative warnings, inventories
of unused functionality, or repetitive synthetic-score disclaimers. Verify actual
input requirements and defining formulas remain clear. Keep validation status in
the handoff and access requirements in operational documentation. Record this review
separately from transport-preservation tests: those tests cannot guarantee agent
behavior or scientific completeness.
