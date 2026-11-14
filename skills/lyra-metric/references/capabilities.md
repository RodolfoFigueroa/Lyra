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

Declare named metric inputs through `inputs={name: LocationDependency(provider="provider_id")}`. Handlers receive native GeoDataFrames with all registered columns. An operation declares one such source and a target provider ID through `target_provider=...`. The target provider determines its output schema and scope; matching schemas alone do not establish a route or scientific comparability.

## Concrete metrics and private intermediates

Use ordinary Python factories to create concrete graphs. Give each decorated metric a unique `name` and register every generated handler in `PluginDefinition`. Each metric owns its parameter, input, and output contracts. Bind dependencies by producer name and named output.

Mark intermediate metrics `private=True` to exclude them from public discovery and submissions. Lyra still executes them as dependencies. For development, call `runner.run_development()` with the metric name, named ID selections through `inputs=`, and settings through `parameters=`. Dependency inspection can show private nodes.

## Public selection and calculation methodology

Public requests use these values across local execution, Python clients, HTTP, and Model Context Protocol (MCP):

- `metric`: the registered public metric name
- `inputs`: named source ID selections
- `parameters`: ordinary calculation values

For local runs and Python client job methods, pass the registered metric name first. Supply selections and calculation settings through keyword-only `inputs=None` and `parameters=None`. Python `None` means omission.

HTTP `POST /jobs` accepts the shared request as its body; MCP `lyra_run_metric` accepts it as tool arguments. Lyra treats omitted mappings as empty mappings and still checks required selections and parameters.

Lyra rejects explicit JSON null sections and unknown envelope fields. Values inside `parameters` may be null only where the metric schema permits them. Unknown metric names fail, and public submissions reject private roots.

Each selection contains `provider` and `ids`. Every ID must be a nonempty string; the list may be empty. Omit inputs declared with `LocationDependency(operation=...)`; Lyra computes those inputs.

Opt an operation into automatic routing with `canonical=True`. It has one provider source and one provider target. Optional typed parameters use the operation’s defaults or fixed bindings; they do not inherit matching metric parameters.

Write an ID-only operation callback with one positional source GeoDataFrame. With `target_selector`, both callbacks receive two positional arguments:

- The selector receives the source GeoDataFrame and symbolic Ibis table.
- The mapping handler receives the source and candidate GeoDataFrames.

For parameters, both callbacks accept keyword-only `parameters` annotated with the same `MetricParameters` subclass. Otherwise, both omit it. The [operation declarations](authoring.md#select-locations-in-operations) describe input and acquisition constraints; metric handlers may accept `context: RunContext` for reporting.

An operation returns a dictionary of source IDs to tuples of target IDs. Lyra resolves ID-only targets or reuses validated selector candidates. It forwards each source/target pair with `source_location_id` and `location_id`; shared targets appear in each source group. Empty or omitted groups complete with no matches.

Make handlers support empty intermediate selections because execution continues with those selections. Keep every source/target pair when the calculation depends on relationships. Selection itself doesn’t group or aggregate results.

Canonical operations are optional. Explicit dependencies can use noncanonical operations, and other enabled plugins may supply compatible canonical routes. Without a route, a public input accepts its native type only. Requests do not name operations or construct pipelines.

Lyra chooses the route with the fewest operations across the enabled catalog. An exact provider scope match requires zero operations. For shortest-path ties, register ordered decorated operation references through `PluginDefinition(route_preferences=[(first_operation, second_operation)])`.

Lyra infers each preference’s source and target providers from its operation sequence and uses that preference across metrics. The sequence must contain connected canonical operations and use a shortest route. Lyra rejects invalid or conflicting preferences during catalog compilation. A preference may name the only shortest path.

Opposite-direction edges do not imply inverses. The conversion graph can contain cycles, but accepted execution plans cannot. Same-provider inputs never expand through a round trip.

Alternative paths need not yield equivalent locations; adding a shortcut may change results. Execution keeps the accepted route after a failure or when another route has cached results.

Canonical routing applies to public selections and to inputs on metric and operation dependencies. Every `Input(...)` reads its declaring caller’s effective inputs. Explicit bindings take precedence over matching names when selecting the caller’s source. The dependency input declares the provider it requires.

An explicit operation keeps its fixed target, which may need another canonical route to reach the next input’s provider. Matching scopes need no conversion. Missing or ambiguous routes fail before acquisition or job creation.

Automatic operations use their own parameter defaults. For explicitly invoked dependencies, Lyra chooses values in this order:

1. Explicit parameter overrides
2. Matching caller values
3. Producer defaults

Missing required values fail validation before acquisition.

## Revisions, cache reuse, and pinning

Update an origin identity when external contents change. Change a provider’s code revision or ID when its acquisition logic, schema, or collection membership changes outputs.

Metrics and operations accept optional opaque `code_revision` tokens. Change the token or node ID when declarations or callbacks change outputs, including either operation callback. Lyra preserves nonblank string tokens exactly; omission and `None` have the same stable identity.

Declare `origins=(...)` on metrics that acquire external data directly, including through managed resources. Sources received only through provider inputs or upstream outputs need no repeated declaration. An empty origin declaration promises no direct external acquisition.

Resources manage access and cleanup; their registrations don’t identify external data. Follow the [resource lifecycle rules](authoring.md#define-custom-resources) for shared instances and injected dependencies.

An omitted origin revision or provider code revision is a stable `None`; equal identities promise equal contents. Resource aliases and descriptions do not independently change identity. Represent output-affecting configuration in the consuming provider or origin identity. Lyra does not inspect handler code or queries to find undeclared dependencies.

Lyra captures provider scopes before acquisition or remote acceptance and rejects declarations that conflict with those scopes. It retains written values indefinitely. Your acquisition code must honor the captured scope even without a retained value, including with caching off.

Reuse depends on effective parameters and [computation identities](authoring.md#reuse-validated-invocation-results). Reordering location selections or source-to-target pairs doesn’t change their identity.

Lyra determines computation keys before acquiring inputs. A cache hit skips upstream work that the result no longer needs. Forwarding targets from a saved operation mapping can still require provider records.

Provider scopes and bound operation uses contribute to computation identity. Changing a native dataset or explicit dependency revision invalidates reuse even when the values match.

Computation keys combine `(name, code_revision)` with scopes and complete effective inputs. Defaults and nulls participate, parameter list order matters, and input names distinguish roles. Provider record keys add `location_id` to provider scope.

Your calculation must remain deterministic; editing code alone doesn’t change a declared identity. Follow [reuse and storage](authoring.md#reuse-validated-invocation-results) for the complete identity inputs and rules for computation scope.

## Run and inspect the contract

Call `runner.run()` with the metric name, `inputs=`, and `parameters=` to execute a public metric. Read named DataFrames or GeoDataFrames from the returned `RunResult.tables`, and execution descriptions from `.metadata`.

Keep `.metadata.store_path` and `.metadata.result_ref` to [reopen the result](authoring.md#persist-and-reopen-local-results). Call `runner.inspect()` separately to compare the accepted plan with what ran.

The plan records the original selections, effective parameters, concrete metric, routes, and pinned revisions. Execution observations record native input counts and node outcomes. Use those observations to distinguish a completed calculation from a cache hit. Even when Lyra reuses tables, provenance describes the current run.

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
