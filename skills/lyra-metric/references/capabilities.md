# Capability contracts for plugin authors

Read this reference when choosing source identity, registering concrete metrics, offering canonical selection, or defining revision coverage. It complements the [adapter and resource contract](authoring.md).

## Provider and origin identity

Declare one provider per collection of records. Register the class in `PluginDefinition.resources` with these attributes:

| Attribute | Meaning |
| --- | --- |
| `id` | Public provider ID |
| `schema` | Strict native Pandera schema |
| `code_revision` | Optional implementation revision |
| `origins` | Direct external data sources |
| `description` | Optional plain-text description |

Each `Origin` has an `id`, an optional `revision`, and an optional description. Its identity is `(id, revision)`. Descriptions, declaration order, and repeated equivalent origins do not change identity.

The provider scope combines the public ID, code revision, and origin identities to identify a dataset and its revision. Together with `location_id`, it identifies a record’s geometry, attributes, and relationships. The scope also fixes complete dataset membership.

Equal IDs from different providers establish no correspondence. Use distinct provider IDs to expose different releases simultaneously.

Declare named metric inputs through `inputs={name: LocationDependency(provider="provider_id")}`. Handlers receive native GeoDataFrames with all registered columns. An operation declares one such source and a target provider ID through `target=...`. The target provider determines its output schema and scope; matching schemas alone do not establish a route or scientific comparability.

## Concrete metrics and private intermediates

Use ordinary Python factories to create concrete graphs. Give each decorated metric a unique `name` and register every generated handler in `PluginDefinition`. Each metric owns its parameter, input, and output contracts. Bind dependencies by producer name and named output.

Mark intermediate metrics `private=True` to exclude them from public discovery and submissions. Lyra still executes them as dependencies. For development, call `runner.run_development()` with the metric name, named ID selections through `inputs=`, and settings through `parameters=`. Dependency inspection can show private nodes.

## Public selection and calculation methodology

Public requests use these values across local execution, Python clients, HTTP, and Model Context Protocol (MCP):

- `metric`: the registered public metric name
- `inputs`: named source ID selections
- `parameters`: ordinary calculation values
- `cache_revision`: an optional value to control cache reuse

For local runs and Python client job methods, pass the registered metric name first. Supply selections and calculation settings through keyword-only `inputs=None` and `parameters=None`. Python `None` means omission.

HTTP `POST /jobs` accepts the shared request as its body; MCP `lyra_run_metric` accepts it as tool arguments. Lyra treats omitted mappings as empty mappings and still checks required selections and parameters.

Lyra rejects explicit JSON null sections and unknown envelope fields. Values inside `parameters` may be null only where the metric schema permits them. Unknown metric names fail, and public submissions reject private roots.

Each selection contains `provider` and `ids`. Every ID must be a nonempty string; the list may be empty. Omit inputs declared with `LocationDependency(operation=...)`; Lyra computes those inputs.

Opt an operation into automatic routing with `canonical=True`. It has one provider source and one provider target. Optional typed parameters use the operation’s defaults or fixed bindings; they do not inherit matching metric parameters.

Operations compute from the supplied frame and parameters, without resource acquisition or table dependencies. They cannot declare extra location inputs or direct origins. Optional `RunContext` supports reporting.

A canonical selector returns the union of related target records, each identity once in deterministic order. Compute complete records from the supplied source and parameters. Handlers must support empty intermediate selections because execution continues with those selections. Retain source-to-target membership explicitly when the calculation needs it; selection does not group or aggregate results.

Canonical operations are optional. Explicit dependencies can use noncanonical operations, and other enabled plugins may supply compatible canonical routes. Without a route, a public input accepts its native type only. Requests do not name operations or construct pipelines.

Lyra chooses the route with the fewest operations across the enabled catalog. An exact provider scope match requires zero operations. For shortest-path ties, register ordered decorated operation references through `PluginDefinition(route_preferences=[(first_operation, second_operation)])`.

Lyra infers each preference’s source and target providers from its operation sequence and uses that preference across metrics. The sequence must contain connected canonical operations and use a shortest route. Lyra rejects invalid or conflicting preferences during catalog compilation. A preference may name the only shortest path.

Opposite-direction edges do not imply inverses. The conversion graph can contain cycles, but accepted execution plans cannot. Same-provider inputs never expand through a round trip.

Alternative paths need not yield equivalent locations; adding a shortcut may change results. Execution keeps the accepted route after a failure or when another route has cached results.

Canonical selection resolves native inputs before ordinary dependency forwarding. Every `Input(...)` sees the effective inputs of its declaration owner. Explicit bindings override matching-name forwarding, then producer parameter defaults fill remaining fields. Internal type mismatches require explicit compatible bindings; public routing does not replace methodology.

## Revisions, cache reuse, and pinning

Update an origin identity when external contents change. Update a provider’s code revision or ID when output-affecting retrieval, transformation, schema, or membership logic changes. Declare `code_revision` on every metric and operation and advance it when computation behavior changes.

Declare `origins=(...)` on metrics that acquire external data directly, including through managed resources. Sources received only through provider inputs or upstream outputs need no repeated declaration. An empty origin declaration promises no direct external acquisition.

Resources manage access and cleanup; their registrations don’t identify external data. Follow the [resource lifecycle rules](authoring.md#define-custom-resources) for shared instances and injected dependencies.

An omitted origin revision or provider code revision is a stable `None`; equal identities promise equal contents. Resource aliases and descriptions do not independently change identity. Represent output-affecting configuration in the consuming provider or origin identity. Lyra does not inspect handler code or queries to find undeclared dependencies.

Preparation captures provider scopes before acquisition or remote acceptance. A changed live declaration cannot silently satisfy an older prepared scope. Declarations neither archive snapshots nor guarantee historical retention. Use `cache_revision` for deliberate cache namespaces; repeating a value permits reuse, while changing it preserves earlier entries.

The runtime resolves canonical selections before it checks the metric cache. It can reuse a complete, ordered batch when native contents, concrete contracts, effective parameters, calculation revisions, and computation identity match. A lookup can still require acquisition or intermediate reads. Changing the order or membership of the resolved native batch requires a separate lookup.

A revision used only to select locations affects the selector’s cache. Changing a native dataset or explicit dependency revision invalidates the metric even when the values match.

For metric and operation identity, Lyra combines `(name, code_revision)` with semantic contracts, dependencies, and protocol versions. Provider record identity uses its public ID, code revision, and declared origin identities. Editing source code alone does not change a node’s code revision.

## Run and inspect the contract

Call `runner.run()` with the metric name, `inputs=`, and `parameters=` to execute a public metric. It returns native DataFrames or GeoDataFrames keyed by output name. Call `runner.inspect()` separately to compare the accepted plan with what ran.

The plan records the original selections, effective parameters, concrete metric, routes, and pinned revisions. Execution observations record native input counts and fingerprints. Use those observations to distinguish a completed calculation from a cache hit. Even when Lyra reuses tables, provenance describes the current run.

On a server, search results show which source types each metric accepts. Inspect the selected metric before submitting:

- Check parameter constraints and defaults in `parameters`.
- Read the result schemas in `outputs`.
- Choose source types from `accepted_selections` and inspect their `bindings`.
- Read `computed_inputs` to understand dependency inputs; don’t include them in the request.

Submit once and poll the returned job handle. When execution finishes, preview the results or download them with your agent credentials.

Malformed or unsupported requests fail before acquisition or job creation. Unknown IDs fail execution with input, source scope, and unresolved IDs; an existing source with no related targets continues as an empty selection. Inspection can succeed for a failed remote job. Read the returned job status to distinguish execution failure from a failed inspection request.

Use MCP `lyra_get_metric_dependencies` to inspect a public metric’s direct dependencies and consumers. It returns up to 50 relationships and reports how many it omits. Each relationship identifies:

- Private nodes
- Producer outputs
- Receiving input names

The tool includes declared operation dependencies. It excludes possible canonical routes because those routes are not calculation dependencies. You don’t need this tool for routine execution or successful result interpretation.

Use the contracts above to adapt your calculation without a Lyra checkout or live service. To exercise the client locally, follow [Run the location examples](https://github.com/RodolfoFigueroa/lyra/blob/main/examples/location-plugin/USAGE.md), which uses an in-process fake transport.

## Deployment compatibility

API and workers use the same live definitions to prepare requests. Both processes import enabled distributions through their entry-point factories, so every plugin dependency must work in both. Keep loading and request preparation free of external input/output (I/O).

Deploy one immutable build and share its `build_revision` across API and workers. The revision covers executable code, declarations, and routing policy. Each queued request records:

- Normalized selections and complete effective values for each node use
- Pinned location scopes
- The build revision

Workers reject a different build with `deployment_changed` before acquiring resources or running handlers; you can resubmit. Compatible workers prepare the request against their live definitions. The executor validates the complete plan before acquiring resources.

The public discovery fingerprint describes metadata. Author `code_revision` values identify metric and operation implementations for computation reuse. A build revision is not a cache invalidation token.
