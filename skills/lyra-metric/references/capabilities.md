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
- `interventions`: optional request-specific additions and removals, with one set per public provider ID

For local runs and Python client job methods, pass the registered metric name first. Use the keywords `inputs`, `parameters`, and `interventions`. Python `None` means omission.

Import `Intervention` from `lyra.data.interventions`. Give each set a catalog, ID, revision, and at least one addition or removal. New IDs must be absent from the base dataset. Removal IDs must exist there.

For `add`, pass a GeoDataFrame in Python or an array of inline records over HTTP or MCP. Include every declared column, even nullable attributes. For `remove`, list distinct string IDs.

The catalog, set ID, and revision identify immutable contents. Change the ID or revision when you change an action or attribute. Change the metric’s `code_revision` when a validation change can reject previously accepted output. Lyra includes the base scope and set identity in cache keys for dependent inputs and operation targets.

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

Lyra uses records directly when the selected provider matches the input’s target provider. For other providers, it chooses the route with the fewest operations across all enabled plugins. To resolve a tie, set `route_preferences` on `PluginDefinition` to a list such as `[(first_operation, second_operation)]`. List the decorated operations in execution order.

Lyra infers each preference’s source and target providers from its operation sequence and uses that preference across metrics. The sequence must contain connected canonical operations and use a shortest route. Lyra rejects invalid or conflicting preferences during catalog compilation. A preference may name the only shortest path.

Opposite-direction edges do not imply inverses. The conversion graph can contain cycles, but accepted execution plans cannot. Same-provider inputs never expand through a round trip.

Alternative paths need not yield equivalent locations; adding a shortcut may change results. Execution keeps the accepted route after a failure or when another route has cached results.

Canonical routing applies to public selections and to inputs on metric and operation dependencies. Every `Input(...)` reads its declaring caller’s effective inputs. Explicit bindings take precedence over matching names when selecting the caller’s source. The dependency input declares the provider it requires.

An explicit operation always selects records from its declared target provider. If the next input requires a different provider, Lyra adds a canonical route between them. Lyra rejects missing or ambiguous routes before it creates a job or reads data.

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

Call `runner.run()` with the metric name, `inputs=`, and `parameters=` to execute a public metric. Read its [tables, metadata, and report](authoring.md#read-tables-and-metadata-from-one-run) from the returned result. Lyra attaches the complete `ExecutionReport` before returning. `run_development()` follows the same publication rules, including for private roots.

Keep the addresses in `.metadata` to [reopen reports and results](authoring.md#persist-and-reopen-local-results). The report has matching IDs and `status="succeeded"`. Nested edits to returned values cannot change saved state or later reads; field access doesn’t make further copies.

Read `report.request` for complete normalized caller selections and effective root parameters, including defaults and nulls. Derived inputs and selected routes remain in the resolved graph.

Read `report.request.interventions` for all accepted changes, including additions outside the selection. To inspect the records the calculation selected, read its [retained location inputs](authoring.md#inspect-retained-location-inputs).

A resolved use combines a definition with particular inputs and effective parameters. `report.nodes` contains one summary per use; several consumers of the same use share its summary. Two uses of one definition can have different `use_id` values. These IDs are local to the report, and `root_use_id` identifies the root.

Follow `node.inputs[name].source_use_id` to find a binding’s producing use. The binding selects either a metric’s named `output_name` or location records with a `provider_scope`. For metric outputs, read `node.outputs` for schemas and ownership, including empty outputs.

Read `parameter_bindings` for direct named forwarding between source and target uses. Defaults and literals have effective values without forwarding records. Equal values alone don’t establish a binding.

Interpret `node.outcome` as current observed work:

| Outcome | Meaning |
| --- | --- |
| `executed` | Computation or acquisition without reuse |
| `reused` | Reused needed values without executing this use’s work |
| `mixed` | Both execution and reuse |
| `skipped` | Neither execution nor value reuse needed |

A shared use aggregates its actual work. A handler returning an empty table can be `executed`; a zero-partition use needing no invocation is `skipped`. There is no per-row history or historical producer reconstruction.

Use `node.code_revision` and `node.origins` to read declarations even for reused or skipped work. Lyra deduplicates origins by `(id, revision)` and preserves their informational descriptions. `None` revisions identify fixed, unversioned contents. A provider’s origins don’t become a consuming metric’s direct origins.

Read `report.build_revision` separately to identify the deployment build; it can be `None` locally. Reporting adds no cache invalidation token.

For operations, inspect these fields on `node.operation`:

- `target_scope`: declared target provider scope
- `target_adapter`: configured adapter, either `parquet`, `postgis`, or `None`
- `selector`, `target_query`, and `mapping`: observed stages

Each stage is `executed` if it ran at least once, otherwise `skipped`. Selector and query stages are `not_applicable` without a selector. Constructing an expression doesn’t acquire data, while executing a query does, even with zero matches. Reusing a mapping can still require provider record acquisition for forwarding.

Operation origins are empty; the target provider declares acquisition sources. A declaration doesn’t prove that acquisition occurred. Report collection never initializes resources or acquires data solely for metadata.

Return ordinary tables and mappings with explicit, deterministic declarations. Reporting doesn’t require storing uncached intermediates.

Use `runner.inspect()` for an independent diagnostic record of the latest attempt. It includes native input counts and `execution.succeeded`, plus these observation labels:

- `started` and `completed`
- `cache_hit` and `partial_hit`
- `shared_reuse`

Inspection is `None` before a run or after preparation fails. After execution starts, it can include partial observations from a failed attempt. A later attempt replaces these diagnostics; durable reports represent complete successful executions only.

Search results list the source provider IDs each metric accepts. Inspect the selected metric before submitting:

- Check parameter constraints and defaults in `parameters`.
- Read the result schemas in `outputs`.
- For an input you supply, read its `target_provider` in `location_inputs`. Choose a source provider ID from `providers[target_provider].accepted_sources`.
- For an input Lyra derives, read `operation` and `source_provider` in `location_inputs`. Its `record_schema` describes one row per distinct pair of source and target record IDs.
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
