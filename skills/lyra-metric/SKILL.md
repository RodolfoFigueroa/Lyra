---
name: lyra-metric
description: Adapt an existing Python geospatial calculation into a new Lyra plugin or add it as a metric to an existing plugin, preserving its behavior and validating its contract. Use for metric authoring, not running deployed metrics or deploying plugins.
---

# Adapt a workflow to Lyra

Produce a locally validated plugin around the user’s existing calculation. Keep the calculation independent of Lyra and introduce a small adapter. Read [the authoring reference](references/authoring.md) before implementing the adapter. It includes a complete example and the supported SDK contract. You can use it without a Lyra checkout. Read [capability contracts](references/capabilities.md) when choosing provider and origin identity, canonical selection, private intermediates, or revision coverage.

## Establish the contract before coding

Inspect the target repository’s instructions, calculation, documentation, tests, dependency declarations, and existing plugin factory. Determine whether the user wants a new plugin or a metric added to the existing one. Preserve existing registrations, package layout, build backend, and unrelated configuration.

Use the bundled reference as the authoring contract. Declare `lyra[sdk]` as a plugin dependency. Include `lyra[runtime]` in the development environment for the native authoring checks and local tests.

Before implementing the adapter, run the native authoring checks from the target project. Replace `your_skill_directory_here` with the installed skill’s directory. Execute the helper without reading its source into context.

```sh
uv run python your_skill_directory_here/scripts/check_authoring.py
```

The helper reports the installed Lyra version and tests the `lyra-authoring` contract in your development environment. It uses in-process fixtures to check:

- Provider acquisition and metric execution
- Exact-ID selection and resource cleanup
- Rejection of invalid native frames
- Declarations, parameters, and units

After the checks pass, test your adapter with the workflow’s own fixtures.

On failure, use the concise diagnostic and relevant version-matched documentation to resolve the specific issue. Read a narrowly targeted SDK implementation only when a concrete error or contradiction remains unresolved. Do not routinely read installed Lyra source or dump complete schemas to learn the contract.

Ask about unresolved incompatibilities; do not upgrade dependencies automatically. If the helper cannot run, report the blocked check and continue only independent work.

Consult these platform facts before asking the user about integration:

- Each provider returns GeoDataFrames in its declared CRS. Its [provider scope](references/capabilities.md#provider-and-origin-identity) identifies the dataset and revision; matching IDs or schemas do not make providers interchangeable.
- Providers support Point, MultiPoint, LineString, MultiLineString, Polygon, and MultiPolygon geometries.
- Use ID selections for public execution and fake provider overrides in graph tests. Direct handler tests receive native GeoDataFrames matching the provider schema.
- Describe expected units, geometry kinds, and required properties with `Annotated[gpd.GeoDataFrame, Field(description="…")]`.
- Initialize Earth Engine in the requested `BaseIO` resource’s `setup()` method. Configure its project settings and secrets through named resources.

Establish any narrower workflow requirements from its implementation, tests, or documentation.

Trace ordinary parameters, spatial requirements, returned values, and external data or services through the implementation and tests. Establish which facts are documented, demonstrated, missing, or contradictory. A type annotation or a successful sample run alone does not establish scientific meaning or validity across regions and scales.

## Ask rather than guess

Ask the user when missing or conflicting information requires a decision that affects the calculation, its public contract, or interpretation of results. First consult the bundled platform contract and inspect workflow evidence. The following are subjects to investigate, not a questionnaire to ask verbatim:

- Parameter meanings, defaults, valid ranges, and list semantics
- CRS and reprojection requirements, geometry types, required zone attributes, geographic coverage, and valid spatial scales
- Output meanings, units, column types, nullability, and the correspondence between returned rows and input features
- Required datasets, credentials, services, and intended handling of missing data

Use the reference’s spatial-input and resource lifecycle contracts without asking the user to reconfirm these platform facts. Keep existing reprojection to EPSG:4326 without asking the user to reconfirm code that already establishes it. Do not add a restriction requiring incoming geometry to already be EPSG:4326 when the workflow handles reprojection.

Separate the size or administrative level of an input region from the raster resolution used in a calculation. Inspect the chosen datasets and any available matching documentation for actual limitations. Missing documentation alone does not justify new restrictions, blocking the adapter, or generic disclaimers in metric metadata.

Do not claim universal coverage or scientific validity at all scales. Report incomplete validation in the handoff, and keep known consumer-relevant limitations in metadata. Ask if a concrete decision remains, such as choosing a reduction resolution or handling regions without source data. Continue otherwise.

Read available evidence first, but do not silently resolve contradictions between code, documentation, tests, and user requirements. Explain the conflict and ask. Do not invent plausible units, descriptions, ranges, defaults, or validation rules to satisfy Lyra’s metadata requirements. Do not replace an unknown unit with `dimensionless` or an unknown meaning with a generic label.

Group related questions concisely. Say what is unknown and why the answer matters; cite the relevant code or documentation when available. Suggested choices are proposals, not decisions.

Wait for answers before implementing dependent behavior; silence is not confirmation. Continue independent work only where it does not embed the unresolved choice; do not register a guessed or placeholder contract. If you cannot deliver questions interactively, report them and leave the dependent work incomplete.

Routine private helper names and layout choices can follow repository conventions. Public names should follow the user’s terminology and existing project conventions; ask when their meaning is ambiguous. Obtain agreement before changing the calculation’s scientific or computational semantics. Never silently drop rows, fill missing results, relabel rows by position, or impose arbitrary output columns.

## Implement the smallest adapter

Build and register the adapter with these steps:

1. Preserve the existing callable and its defaults. Put ordinary inputs in a concrete `MetricParameters` subclass, and use the SDK’s resolved spatial arguments. Convert geometry for the existing callable without changing its CRS or attributes unless the workflow explicitly requires that transformation.
2. Declare the output columns and their [units](references/authoring.md#output-units). If the measurement unit is unknown, ask for it. If parameters change which columns the calculation returns, ask for a fixed schema.

   Preserve the producer’s row IDs. Ask for a mapping only if the calculation needs one. Publish native tables and use the job temporary directory for scratch files. Agree on an adaptation before wrapping an unsupported async or generator workflow.

   Describe the metric so callers can choose inputs and interpret results without its README. Include methodology when it affects those decisions, and explain the scale of each score. Use source links to support the [metric, parameter, and column descriptions](references/authoring.md#descriptions-for-independent-consumers).

3. Choose components by their role:

   - **Providers**: declare the record schema and identity, then register the provider as a resource. Managed providers support exact-ID reads and [base membership lookup](references/authoring.md#support-intervention-sets). For a custom provider, implement `resolve(ids)` and add `existing_ids(ids)` if you support interventions. Keep graph execution out of provider preparation.
   - **Operations**: map source IDs to tuples of existing target IDs using optional candidates and typed parameters. Keep callbacks computational; they cannot acquire data or consume table dependencies.
   - **Metrics**: return tables with declared meaning and row granularity. Keep result locations separate from supporting context. Set `private=True` for intermediate metrics that callers shouldn’t request directly.

   Mark one location input `primary=True` to assign output rows to its locations. Declare stable `location_id` and `row_key` pairs with composite uniqueness. Without a primary, outputs describe the complete selection without assigning rows to locations. Follow the [ownership rules](references/authoring.md#declare-ownership-and-complete-dependencies).

   Choose a [cache policy](references/authoring.md#choose-a-cache-policy) separately from output ownership. Use `per_location` only when each primary location’s result is independent of the other primary locations in the batch. Development runs follow the same policies and can reuse values.

   Declare a provider’s public `id`, strict `schema`, and `origins`, plus its optional `code_revision`. For an operation, declare its source with `inputs={name: LocationDependency(provider=...)}` and its target with `target_provider=...`. Register operations in `location_operations` and bind their results through `LocationDependency`.

   For managed Parquet and PostGIS providers, follow [managed target selection](references/authoring.md#managed-target-selection). That reference covers computational Ibis selectors and candidate mapping.

   Keep public selection and calculation methodology distinct. Opt eligible batched selectors into canonical routing only when they express a supported default relationship. Bind methodological dependencies explicitly to concrete producers.

   Follow [capability contracts](references/capabilities.md) for metric factories and revision decisions. Change the node ID or optional `code_revision` when outputs change; an operation’s revision covers both selector and mapper. Use [persistent result access](references/authoring.md#persist-and-reopen-local-results) to configure local storage and reopen results.

   Managed execution attaches a complete report automatically. Keep ordinary table and mapping returns without adding reporting wrappers, reporting columns, or extra acquisition. Inspect `result.report` alongside `.tables` and `.metadata` to check the managed contract.

   Acquire data within metrics or their calculation helpers. Use separate metrics and table dependencies when that improves reuse or testing. Preserve scientific behavior and describe sources clearly. Copy dependency tables before changing them.

   Register each metric in the plugin factory. For a new project, add package metadata and entry-point registration. For an existing project, extend its factory and configuration while retaining its other metrics.
4. Declare directly imported dependencies using the repository’s dependency policy. Use `uv` for Python environment and command management. Ask before adding runtime dependencies unless the user has authorized them; explain each dependency’s purpose and whether existing dependencies suffice.

   Use the project’s configured package source. If the source is unknown, ask rather than assuming a public index hosts the package.

   For Earth Engine workflows, follow the reference’s [resource lifecycle](references/authoring.md#earth-engine-lifecycle). Keep project settings and credentials in named resource configuration, separate from metric parameters. Validate offline with fake credentials and clients; ordinary authoring requires no live credential discovery.

5. Register the factory as `plugin` in `[project.entry-points."lyra.plugins"]` and inspect it with `lyra-plugin describe`. Include installation metadata. Publishing and deployment require a separate request.

## Verify and hand off

Compare the adapted calculation with the original on a small, representative fixture. Test multiple features with distinct values and nontrivial identifiers so swapped rows cannot pass accidentally. Exercise parameter preparation when a parameter model is declared, and result normalization, alongside direct function calls. Verify invalid parameters, output names, native table types, empty results, public encoding, and any workflow-specific row relationships. Use fake acquisition to exercise dependencies. When extending a plugin, verify existing metrics remain registered and retain their behavior.

Run live inspection plus the repository’s required formatting, linting, type checking, and tests. Use the existing project environment. Do not perform wheel or package-installation smoke checks unless requested. Do not start production jobs or expensive data retrieval to validate an adapter without authorization. Local SDK validation does not require platform services; the underlying workflow may require them.

Review the output from `lyra-plugin describe` without relying on the README. Check that consumers can select the metric, supply meaningful inputs, and interpret its results and limitations. Compare the metadata with the workflow evidence; restore omitted consequential details, remove details that do not affect consumer decisions, and resolve contradictions before handoff. Keep implementation inventories and generic validation caveats out of descriptions. This is a semantic review, not a description-length check.

If data, tools, or services are unavailable, report the exact blocked check and what is still unverified. Do not fabricate fixtures with scientific assumptions or describe a mocked calculation as validated against real data.

Finish with the changed files, established contract, tests and their outcomes, remaining questions or limitations, and an example request using known valid inputs. Distinguish a locally validated plugin from deployment verification.
