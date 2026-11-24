# Lyra authoring contract

Use this reference to adapt an external workflow to the `lyra-authoring` contract without a Lyra checkout. Run the bundled native authoring helper in your development environment to check the contract before implementing your adapter.

Use [capability contracts](capabilities.md) to choose source identities and metric registrations. That reference also covers routing, private dependencies, and revision coverage.

Passing checks do not certify scientific correctness or deployment readiness. The helper also cannot verify plugin data-source credentials or connectivity. Validate those responsibilities separately.

## Assign implementation revisions

Use optional `code_revision: str | None` to identify a metric, operation, or provider implementation. Lyra compares these opaque tokens without interpreting version numbers. The node ID and revision identify the implementation.

Omission and explicit `None` have the same stable identity. Supply a nonblank string; Lyra preserves it exactly, so `"2"` and `"02"` differ. Integer tokens are invalid.

Change the token or node ID whenever declarations, helpers, or callbacks change outputs. One operation revision covers both its target selector and mapper. Lyra doesn’t inspect code or package versions to discover changes.

Return the same logical values and membership for the same identity and effective inputs, even with caching off. Represent differences that affect outputs through declared identities or effective parameters. Different outputs under one key violate the plugin contract; repeated writes retain the existing value.

## Parameters and handlers

Decorate each synchronous metric function with `lyra.sdk.metric`. The runtime calls the function by keyword in both local and worker execution, using the declared parameter, location, context, resource, and dependency arguments:

- `parameters` is a concrete `MetricParameters` subclass. Use this base for nested models as well. Keep its settings for unknown-field rejection, validated defaults, and finite numbers. Omit this argument for a metric without ordinary inputs.
- Lyra infers each operation’s optional model from its keyword-only `parameters` annotation.
- Root parameter fields require descriptions. Preserve evidenced defaults and constraints using `Field`; omission and accepting `None` are different choices.
- Supported scalars include strings, booleans, integers, finite floats, null, and homogeneous scalar `Literal` enums. Lists, string-keyed dictionaries, unions, and nested models must contain supported values.
- Use Pydantic `JsonValue` for recursive JSON values containing nested lists and objects.
- Use `Field(default_factory=...)` for containers and nested models. Lyra serializes all effective parameter fields, including defaults and nulls.
- Factories and validators must return the same values for the same inputs. They must not perform external input/output (I/O), such as accessing files or services.
- Validators must preserve normalized values when run again, including after JSON serialization and parsing.
- Do not use aliases, `Any`, sets, tuples, datetimes, recursive models, field exclusions, custom serializers or encoders, computed fields, or custom schema overrides. Consult version-matched documentation for other types.
- Named `gpd.GeoDataFrame` arguments receive native records from inputs declared with `LocationDependency(provider="provider_id")`. Each frame includes `location_id`, active `geometry`, the provider CRS, and every declared attribute. Metrics may declare zero or more provider inputs.
- Declare `context: RunContext` to identify the job and node, log diagnostics, manage scratch files, and report progress.

Describe each location argument with `Annotated[gpd.GeoDataFrame, Field(description="…")]`. Import `Annotated` from `typing` and `Field` from `pydantic`. Explain the expected units, geometry kinds, and required properties.

Lyra includes these descriptions in generated request schemas and inspection results. Use prose to explain scientific requirements; location contracts and schemas enforce structural and source compatibility.

Provider declarations establish record scope independently of mutable frame metadata. Copy input frames before transforming them.

Metric handlers reject positional-only arguments, variadics, handler argument defaults, asynchronous handlers, and generators. Operation mapping handlers receive source and optional candidate tables positionally. Spatial arguments cannot be nullable or defaulted.

Native inputs preserve all provider attributes and `location_id` values in the declared CRS. Check that the provider supplies every property your calculation requires; reproject explicitly when needed.

Native inputs preserve declared data types, including columns with no rows or only nulls. Every row has every declared property. Lyra treats missing values as nulls and permits them only in nullable columns. Validation rejects geometry dimensions or structures that Shapely cannot preserve.

Preserve reprojection already performed by the calculation. Accept the incoming CRS values that the calculation can transform into its working CRS.

An ID selection does not establish dataset coverage or scientific applicability. Distinguish region size or administrative level from raster reduction resolution. Do not infer unlimited validity or invent restrictions from missing documentation. Report incomplete validation in the handoff rather than adding generic metadata caveats.

Metric names match `^[a-z][a-z0-9_]*$` and cannot start with `lyra_`.

## Descriptions for independent consumers

Describe what consumers need to understand about the metric. Include an evidenced detail only when it helps them select the metric, choose inputs, or interpret results. Consumers such as Model Context Protocol (MCP) clients do not have the plugin README, so essential interpretation must be self-contained. Keep claims grounded in the workflow’s documentation, implementation, tests, and user answers.

Begin with a useful summary of the quantity and operation. Sources, aggregation, formulas, thresholds, temporal meaning, resolution, and limitations are possible content, not a checklist to complete. For each detail, ask what consumer decision or interpretation would change without it. There is no required paragraph count or minimum length.

Keep overall meaning in the metric description, input choices and their effects in parameter descriptions, and individual output meanings and scales in column descriptions. Avoid repeating the same explanation across these fields. Explain established missing-value semantics when useful, rather than merely saying nulls remain null. Describe the temporal meaning of a result when known, rather than listing absent date filters or year-selection features.

Omit routine reprojection, internal band names, scaling and unit-conversion formulas, unused attributes, and error propagation unless they affect consumer decisions. Preserve actual input requirements. A formula defining a custom index can be essential even when a routine sensor conversion formula is not.

Keep known coverage restrictions and consequential interpretation limits in metadata. For example, a first qualifying five-year epoch is not an exact date of urban development. Incomplete checks and the agent’s validation status belong in the handoff; deployment access requirements belong in operational documentation. Do not turn missing documentation into generic warnings about unverified regional availability or scientifically valid polygon sizes, and do not imply universal validity. Ask about material ambiguity or conflicting evidence before finalizing dependent metadata; missing documentation alone does not require a question.

Source links support the explanation but cannot replace essential interpretation. MCP catalog listings abbreviate descriptions; individual metric inspection exposes the complete metadata, so put the useful summary first. Review the generated metadata without the README for both missing essential context and unnecessary detail. Do not inventory everything the implementation does or the agent checked.

For example, the documented Earth Engine evaluation fixture supports: “Mean elevation per input polygon from the SRTM dataset (USGS/SRTMGL1_003).” Its column description can say: “Mean elevation in metres; null when source data are unavailable.” These facts belong to that fixture, not every Earth Engine workflow. Preserve its computational resolution and reprojection in the adapter without automatically describing those operations in consumer metadata. Report that validation used offline mocks in the handoff, not in the metric description.

## Earth Engine lifecycle

Declare `earthengine-api` when your plugin imports `ee`. Select `lyra[earth-engine]` if your plugin uses `lyra.utils.ee`.

Configure and register the resource:

1. Define an `IOConfig` model for the project and declare a named secret for service-account JSON in your `BaseIO` subclass.
2. Supply settings through `[resources.<name>.config]` and secret references through `[resources.<name>.secrets]`.
3. Register the resource in `PluginDefinition(resources=...)` and inject it with `Annotated[YourIO, Resource("name")]`.

Resolve credentials through the resource lifecycle, initialize in `setup()`, acquire data inside `operation()`, and clean up in `close()`. Keep project settings and credentials out of metric parameters, and avoid imports from `lyra.server`.

The Earth Engine client shares state across a process. Use one configuration per executing process with `ee.Initialize` and `ee.Reset`. Coordinate initialization and cleanup across plugins that share that state; separate resource instances do not isolate their clients.

Keep imports, plugin factories, and project inspection free of initialization, credential reads, and initialization-dependent expressions. Construct images, reducers, and collections during resource acquisition. Lyra initializes resources only when execution requests them, both in workers and through `LocalRunner(resource_config=...)`.

Fake credential creation, initialization, acquisition, and cleanup in offline tests. Verify setup runs once per execution, only when requested. Check that imports and project inspection make no Earth Engine calls.

Set up the resource explicitly before calling a calculation directly. Ask about real credentials only when authorized live execution needs them. Ordinary authoring and offline validation require no real credentials.

`lyra.utils.ee.reduce_ee_image_over_gdf` reprojects a copy of the geometry to EPSG:4326 and uses `scale` as reduction resolution in metres. Preserve the workflow’s dataset, reducer, resolution, reprojection, and feature mapping. Establish missing-value handling from the workflow or ask if a decision is needed.

## Example: a documented existing calculation

The example wraps `workflow.calculate_metric`. The existing workflow establishes this contract:

- **Zones**: EPSG:4326 polygons or multipolygons with unique string IDs, a finite numeric `base_value`, and a string `category`
- **Parameters**: an integer multiplier, a finite float offset on the same scale as the score, and a list of selected categories
- **Output**: one finite, nonnullable, dimensionless `score` per zone

Supply all three parameters; the workflow specifies no further ranges.

The calculation preserves the input index and row order. It returns `base_value * parameter_1 + parameter_2` for selected categories and `0.0` for all others.

Start the adapter by importing the calculation and the types used in its declarations.

```python title="adapter.py"
import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
from pandera.engines.geopandas_engine import Geometry
from shapely.geometry import box
from pydantic import Field

from lyra.sdk import (
    LocationDependency,
    LocationProvider,
    MetricParameters,
    Origin,
    PluginDefinition,
    metric,
)

from workflow import calculate_metric
```

Add the parameter model to the same file:

```python title="adapter.py"
class Parameters(MetricParameters):
    parameter_1: int = Field(
        description="Multiplier applied to each zone's base value."
    )
    parameter_2: float = Field(
        description="Offset to add after multiplying the base value."
    )
    parameter_3: list[str] = Field(
        description="Category labels included in the calculation."
    )
```

Describe the formula and its input requirements so a caller can interpret the score.

```python title="adapter.py"
metric_description = (
    "Compute a synthetic property-based score for each input "
    "zone.\n\nFor categories selected by parameter_3, calculate "
    "base_value * parameter_1 + parameter_2; return zero for "
    "other categories. Inputs must be EPSG:4326 polygons or "
    "multipolygons with a finite numeric base_value and string "
    "category property."
)
```

Declare the score’s type and unit, and specify what one output row represents.

```python title="adapter.py"
metric_outputs = {
    "zone_score": pa.DataFrameSchema(
        {
            "score": pa.Column(
                "float64",
                description=(
                    "Synthetic score; zero can result from either the formula "
                    "or an unselected category."
                ),
                metadata={"unit": "dimensionless"},
                nullable=False,
            )
        },
        strict=True,
        metadata={
            "meaning": "Synthetic property-based zone score",
            "granularity": "One row per input zone",
        },
    )
}
```

Declare the provider’s ID and geometry columns separately from the metric’s output schema:

```python title="adapter.py"
location_columns = {
    "location_id": pa.Column(
        str,
        unique=True,
        checks=pa.Check.str_length(min_value=1),
        description="Zone ID",
        metadata={"unit": None},
    ),
    "geometry": pa.Column(
        Geometry(crs="EPSG:4326"),
        checks=pa.Check(
            lambda values: values.is_valid & ~values.is_empty,
            name="valid_geometry",
        ),
        description="Zone geometry",
        metadata={"unit": None},
    ),
}
```

Add every attribute the calculation reads, then create the strict provider schema:

```python title="adapter.py"
location_columns.update(
    {
        "base_value": pa.Column(
            float,
            description="Base value multiplied by parameter_1.",
            metadata={"unit": "dimensionless"},
        ),
        "category": pa.Column(
            str,
            description="Category tested against parameter_3.",
            metadata={"unit": None},
        ),
    },
)
location_schema = pa.DataFrameSchema(location_columns, strict=True)
```

The example provider returns three fixed synthetic zones from memory. For your workflow, resolve exact IDs from its actual source and declare the corresponding origins and code revision:

```python title="adapter.py"
class ZoneProvider(LocationProvider):
    id = "workflow_zones"
    code_revision = "1"
    origins = (Origin(id="workflow-zones-fixture", revision="1"),)
    schema = location_schema

    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
        frame = gpd.GeoDataFrame(
            {
                "location_id": ["zone-z", "zone-a", "zone-m"],
                "base_value": [2.0, 5.0, 7.0],
                "category": ["urban", "rural", "urban"],
            },
            geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1), box(2, 0, 3, 1)],
            crs="EPSG:4326",
        )
        return frame.loc[frame.location_id.isin(ids)].copy()
```

Register the handler with its provider input and metric output declaration:

```python title="adapter.py"
@metric(
    code_revision="1",
    inputs={"location": LocationDependency(provider=ZoneProvider.id)},
    name="zone_score",
    description=metric_description,
    outputs=metric_outputs,
)
def run(location: gpd.GeoDataFrame, parameters: Parameters) -> pd.DataFrame:
    return calculate_metric(
        location.set_index("location_id").rename_axis("id"),
        parameter_1=parameters.parameter_1,
        parameter_2=parameters.parameter_2,
        parameter_3=parameters.parameter_3,
    )


def create_plugin() -> PluginDefinition:
    return PluginDefinition(metrics=[run], resources={"zones": ZoneProvider})
```

Keep the calculation in its existing module. Add this adapter to the project’s package, and point its factory configuration at the actual module. A plugin may register several metrics explicitly; Lyra does not discover sibling modules.

## Metric dependency graphs

Use `@metric` for acquisition, transformations, and calculations, and register each function with `PluginDefinition(metrics=[...])`. Public metrics can run directly; both public and private metrics can supply named tables to other metrics. Set `private=True` for intermediates that should be excluded from public entry points. Separate acquisition and calculation when that improves reuse or testing.

A metric can use source clients or file readers to produce its output tables. Use `context.temp_dir` for scratch files during processing.

Concrete node names identify calculations and must be unique across enabled plugins. Public requests identify a registered public metric name. Output names identify whole tables and are local to each producer.

Specify the producer and output when declaring a dependency:

```python
inputs = {
    "records": Dependency(
        "example_selected_records",
        producer="example_selected_source",
        parameters={"year": Input("parameters.baseline_year")},
    )
}
```

The key `records` names the function argument that receives the table. You can reference a producer from another plugin without importing it.

Each node declares its own parameters. Dependencies do not add public request fields, and Lyra excludes context and dependency arguments from public request schemas.

Lyra supplies upstream ordinary fields in this order:

1. Explicit overrides
2. Matching effective consumer parameters, including defaults
3. Producer defaults

Use `parameters=` for parameter bindings and `.bind()` for location bindings. Parameter overrides replace whole fields, including nested objects. Lyra treats `None` as an explicit override and rejects missing required fields. Bindings cannot supply contexts or tables.

Graph validation checks explicit location bindings before matching names. To supply the producer’s `location` input from the consumer’s `lots` selection, declare:

```python
dependency = Dependency("records", producer="records").bind(
    location=Input("lots"),
)
```

Save the declaration returned by `.bind()`; the original stays unchanged. Chained calls replace only the names you supply and keep other explicit bindings.

Lyra copies nested lists and dictionaries into each new declaration. Later edits to the originals cannot change its bindings.

Declare both names as location inputs on their respective metrics. A consumer may declare an input solely to forward it to a dependency.

Location bindings accept `Input` references or nested `LocationDependency` declarations; they cannot supply literal values. If names differ, declare a binding even when both arguments receive GeoDataFrames.

Every `Input`, including nested references, uses the effective inputs of the metric or operation that owns the declaration. Explicit bindings take precedence over forwarding inputs with matching names. Lyra executes nested operations before invoking the receiving node.

For example, this declaration assumes registered producers with the named inputs and parameters shown:

```python
from lyra.sdk import Dependency, Input, LocationDependency

records = Dependency(
    producer="street_records",
    output="records",
    parameters={
        "filters": Input("parameters.filters"),
        "options": {"kind": "input", "path": "parameters.filters"},
    },
).bind(
    streets=LocationDependency(
        operation="streets_in_region",
        parameters={"radius": Input("parameters.search_radius")},
    ).bind(
        region=LocationDependency(operation="region_for_blocks").bind(
            blocks=Input("blocks"),
        ),
    ),
)
```

`filters` forwards the whole nested value. `options` remains literal JSON even though its keys resemble a reference. Only an `Input(...)` object establishes a reference.

Every reference in this declaration reads the consumer’s effective inputs, including references inside nested operations. Use the producer’s declared location input names as `.bind()` keywords. For an operation, use its declared source name.

Metrics with a fixed region can define their own sources and omit location arguments. To pass a boundary downstream, return it as a GeoDataFrame dependency. Graphs that use a client-selected region must declare the required spatial inputs along each dependency path.

Your plugin determines coverage checks and geographic calculations. Use `lyra.utils.geometry.calculate_feature_areas_m2` when it matches your area calculation.

Implement database access in a `BaseIO` subclass in the plugin. Import `IOConfig`, `ResourceConfig`, and `SecretReference` from `lyra.contracts.resources`. Lyra validates credential references without reading secrets; it reads secrets only during resource acquisition. Declare the configuration and register it as follows:

1. Define connection settings with `IOConfig` and required credentials with `secret_names`.
2. Register the class in `PluginDefinition(resources=...)`.
3. Request it with `Annotated[YourDatabaseIO, Resource("your_source")]`.
4. Configure it through `[resources.your_source.config]` and `[resources.your_source.secrets]`.

Create clients in `setup()` and release them in `close()`. Wrap complete queries with `operation()` and use bound SQL parameters. Keep imports and registration free of credential reads and service calls.

The plugin defines source names, queries, connection pools, timeouts, and client dependencies. Preserve the source CRS in spatial results, including empty results.

Registration and project inspection check local declarations and signatures without accessing sources or requiring dependency plugins. Loading enabled plugins checks references across the complete graph:

- Name collisions and cycles
- Parameter paths
- Required spatial forwarding

Execution validates actual arguments, Pandera output schemas, and public serialization.

When testing a handler directly, pass native GeoDataFrames and dependency tables to the handler.

To test a public metric and its dependencies, import `LocalRunner` from `lyra.runtime.local`. Pass your metric and provider definitions to `LocalRunner.from_definitions`. Use `resource_overrides` for fake provider subclasses and mock any calls that would connect to external services. Call `runner.run()` with the metric name and its ID selections.

A requested metric returns all its declared outputs; intermediate dependency outputs remain internal. Lyra executes dependencies sequentially on the selected worker. Identical producer invocations reuse native tables within that job, including all outputs of a multi-output producer.

Lyra supplies an independent frame for each handler argument. You can edit it without changing another named input, consumer, or retained result. Lyra copies producer outputs before validation and checks output owners against the original selections.

Lyra applies the same declared reuse policies across jobs, so a cache hit can skip acquisition. Failures stop execution and fail the requested job without partial results or automatic retries. Inspect the failing node and dependency path in diagnostics and worker logs.

## Native table ownership and encoding

Declare a nonempty `outputs` mapping for each metric. Return a DataFrame or GeoDataFrame for one output. For multiple outputs, return a dictionary with exactly the declared keys. `None` is not a valid output.

Describe each column in its Pandera schema with an explicit dtype, nonblank description, and `metadata["unit"]`. Before consumers run, Lyra copies the producer’s frames and validates:

- Exact column membership
- Dtypes and nullability
- Uniqueness and declared checks

Return columns in any order, and address them by name.

Choose stable identifiers for your result rows and publish them as columns. Row positions and indexes aren’t identities. Encode meaningful sequence in a column such as rank, and don’t assign request feature IDs to unrelated result rows.

Calculate area fractions and derived columns in your plugin. Each consumer performs the joins or aggregation its calculation requires.

Zero-row tables succeed and still flow to consumers. Preserve their columns and, for spatial outputs, active geometry and correct CRS. Return every declared table even when empty.

Public ordinary tables encode as record arrays (`[]` when empty), without indices. Spatial tables encode as WGS84 GeoJSON FeatureCollections with no index-derived feature IDs. Missing CRS fails serialization; conversion leaves the native graph table unchanged.

Missing cells encode as null; unsupported values fail validation. Empty spatial outputs have an empty `features` array.

Use the identifiers from successful status to retrieve saved outputs:

- **Native table**: `client.results.table(result_ref, output_name)`
- **Schema and provider ownership**: `client.results.describe(result_ref)`
- **Complete report**: `client.reports.get(execution_id)`

With an async client, await each method. While the job remains available, read JSON tables with `client.result_tables(job_id)[output_name]` or `client.results.tables(job_id)[output_name]`.

### Output units

Set `metadata["unit"]` on every Pandera output column or field. Use an expression from [Pint’s default registry](https://pint.readthedocs.io/en/stable/getting/tutorial.html#initializing-a-registry). For dwelling density, declare `"count/km**2"` and describe the column as “Dwellings per square kilometre.” Lyra writes `"count / kilometer ** 2"` in JSON and keeps the column’s values and data type.

Use `None` for a column such as a street ID where no unit applies. Lyra writes it as `null` in JSON. Use `"dimensionless"` for a known unitless quantity, such as a fractional share. Describe the scale of a unitless score and use column checks for any required bounds.

If a measurement’s unit is unknown, ask for it. Lyra rejects omitted units, blank strings, and invalid unit expressions. Progress reports accept separate human-readable unit labels.

## Package and inspect the plugin

Give the plugin its own static `[project].name` and `version` in `pyproject.toml`. Register the factory with an entry point so Lyra can discover it:

```toml
[project.entry-points."lyra.plugins"]
plugin = "your_plugin_module_here:create_plugin"
```

Replace `your_plugin_module_here` with the module that exports your factory.

Lyra requires Python 3.11 or later. Set `requires-python` to the versions your project and its dependencies support. Check that it includes the interpreter you use for testing.

Declare `lyra[sdk]` from an approved checkout or revision. List any other packages the workflow imports directly, such as GeoPandas for `import geopandas`. Add `lyra[runtime]` to the development dependencies for tests that run the plugin locally.

For a new package using Hatch, configure package discovery for your Python package and include any required local datasets. Preserve an existing build backend.

Inspect the project with `uv run lyra-plugin describe` and `uv run lyra-plugin diagram`.

Inspection imports the registered factory, so imports and registration must not contact external services or read datasets. Factories run synchronously, declare no parameters, and return `PluginDefinition`.

The API and workers load the same definitions from one immutable build. Both must be able to import every enabled plugin and its dependencies. Enable installed distributions through either interface:

- Local execution: `LocalRunner(plugins=["your_plugin_distribution_here"])`
- Deployment: `[[plugins.installed]]` entries with `distribution` and `enabled = true` in `lyra.toml`

## Local verification and requests

To test a decorated handler directly, supply its resolved inputs and any context it needs. Import `BoundPlugin` from `lyra.runtime.invocation` and create `binding = BoundPlugin(plugin)` from your plugin definition.

1. Build a native GeoDataFrame matching the provider schema, with `location_id`, active geometry, and the declared CRS. Include representative attributes, distinct values, and IDs in a nonlexical order. Use a fake provider through `LocalRunner` when testing automatic provider validation.
2. If the metric declares a parameter model, call `binding.prepare_parameters(metric_name, values)` to validate values and apply defaults. Invalid values raise `MetricInputError`. For a parameterless handler, skip this call and omit `parameters`. Calling it even with `{}` raises `PluginDefinitionError` for that handler.
3. Call the adapter and original calculation with the same inputs. Compare values, row identifiers, and column names.
4. Call `outputs = binding.normalize_result(metric_name, frame)` to validate output names, table types, Pandera constraints, and serialization requirements. Invalid output raises `MetricResultError`. To check the public encoding, pass the validated tables to `lyra.data.results.serialize_outputs(metric_name, outputs)`.
5. Exercise invalid parameters and result mismatches. Supply a strict fake context only if the handler uses it.

Run these checks locally with fixtures and fake services. Use the original workflow’s tests to verify its data sources and scientific assumptions separately.

If your adapter raises `MetricInputError`, supply the metric name, input path, and message. For example, use `MetricInputError("zone_score", "location", "Required zone attributes are missing")`.

Use separate mappings for HTTP, Model Context Protocol (MCP), and Python client submissions:

- `parameters`: calculation values
- `inputs`: named ID selections, each with `provider` and a list of nonempty string `ids`; the list may be empty

Include `parameters` when required fields need values. You can omit it when an empty object satisfies the schema; preparation applies declared defaults. Omit it for parameterless metrics.

Pass named caller selections in `inputs` and ordinary parameter values in `parameters`.

Choose a provider that supplies the required properties, such as `base_value` and `category` in this example. Supply every ID the calculation needs. Your application and plugin own relationships and completeness checks.

## Further reading

Use the documentation version corresponding to the target SDK; the following links are the development source and can change:

- [Plugin authoring](https://github.com/RodolfoFigueroa/lyra/blob/main/docs/src/content/docs/plugins/authoring.md)
- [Packaging and deployment](https://github.com/RodolfoFigueroa/lyra/blob/main/docs/src/content/docs/plugins/publish-and-debug.mdx)
- [SDK source](https://github.com/RodolfoFigueroa/lyra/tree/main/src/lyra/sdk)

For a fixed reference, replace `main` with the selected product release tag or commit. Installed copies of this skill do not update automatically.

## Inspect dependency diagrams

Export live definitions as Mermaid source:

```sh
uv run lyra-plugin diagram --project . --output graph.mmd
uv run lyra-plugin diagram --include-project ../dependency
```

Omit `--output` to print the diagram source. Repeat `--include-project` to include more plugin projects, using paths relative to your working directory. The command imports each project’s registered factory.

The diagram lists every declared output, including outputs with no consumers. Edge labels identify each output and the input that receives it. Add `--detailed` to show output columns, types, units, and geometry columns:

```sh
uv run lyra-plugin diagram --detailed --output graph.mmd
```

The diagram lists columns in declaration order. Brackets show the unit, as in `density: [count / kilometer ** 2]` or `weight: [dimensionless]`. For a column with `unit=None`, the diagram shows the data type, as in `code: string`.

Detailed metric nodes also list dependency inputs as `input_name: producer_node.output_name` beneath an **Inputs** heading. When the supplied manifests omit the producer, the diagram shows only the output name. Nodes without dependency inputs omit this section. This section describes declared dependency bindings, not request parameters or spatial inputs. The diagram omits descriptions and nullability.

Missing producers appear as dashed external-output nodes. Cycles and incompatible cross-node contracts remain inspectable: this view does not certify execution validity. Duplicate identities or ambiguous node/output ownership are errors.

Python callers can use `lyra.sdk.diagrams.render_mermaid(manifests)` with a sequence of `PluginManifest` objects; pass `detailed=True` for column metadata. To create an image, render the exported source separately with the [Mermaid CLI](https://github.com/mermaid-js/mermaid-cli), for example `mmdc -i graph.mmd -o graph.svg`.

## Define custom resources

Define an input/output (I/O) resource to share file or service access across nodes. Use these declarations:

- Use plain `BaseIO` or `LocationProvider` when you need no settings. The empty `IOConfig` rejects unexpected fields.
- For custom settings, declare `BaseIO[Config]` to type `self.config` and `config_model = Config` to select runtime validation. Subclasses inherit both unless you override them.
- Register the class in `PluginDefinition` through a mapping such as `resources={"shared_files": FileIO}`.
- Declare a node argument such as `files: Annotated[FileIO, Resource("shared_files")]` to request the resource.

If plugins share a resource name, register the same class under that name. Within each execution scope, Lyra shares one instance per registration across direct requests and dependencies. Different names create separate instances.

During managed execution, providers and metrics share these resource instances. Lyra initializes and closes resources within each run; separate runs use fresh instances.

Declare dependencies through annotated attributes, such as `files: Annotated[FileIO, Resource("shared_files")]`, on resources or providers. Subclasses inherit these attributes and may override their bindings. Register every dependency in `PluginDefinition.resources`.

Before constructing resources or resolving credentials, Lyra checks for:

- Missing registrations
- Incompatible resource types
- Dependency cycles

Constructors receive configuration and secrets only. Lyra initializes dependencies and injects them into the declared attributes before calling `setup()`.

Lyra closes each resource before its dependencies, including after partial setup failures. Don’t close injected dependencies yourself; they remain available throughout your resource’s `close()` method. If one resource fails to close, Lyra continues closing the others.

A cleanup error preserves any earlier setup or execution error; otherwise, it fails the run. Lyra uses shared resources sequentially. If you introduce concurrent access, synchronize it yourself. Resource sharing doesn’t promise a transaction spanning the run.

Create clients in `setup()` and release them in `close()`. Cleanup must also release clients created before a setup failure. Wrap each complete synchronous read or download in `with self.operation("read"):`; methods may return Python objects, including DataFrames.

Declare required `secret_names` and read resolved values from `self.secrets`. Deployment configuration supplies environment-variable or file references. Imports, registration, and project inspection must not resolve credentials or contact services.

Pass resource settings to `LocalRunner` through `resource_config`. For tests, pass fake subclasses of registered resource classes through `resource_overrides`. The runner manages setup and cleanup; mock all service access in tests.

## Pandera validation and generated metadata

Prefer `pandera.pandas.DataFrameModel` declarations; object `DataFrameSchema` declarations are equally supported. Lyra compiles independent validation and storage descriptions once, without database access. Set `strict=True` on schemas or the effective model `Config`.

Declare at least one column per output. Return the declared dtypes, fill required values before handoff, and write read-only checks. An unpartitioned output can contain just geometry. Units describe values without converting them. Columns support these types:

- Strings
- Signed 64-bit integers
- Finite 64-bit floats
- Booleans
- Geometry

Named deterministic custom checks must operate on supplied data. Lyra rejects these validation features:

- Warning-only checks and parsers
- Regex or optional columns
- Column filtering
- Missing-column insertion
- Row dropping

For spatial outputs, declare one geometry column with matching active geometry and a known CRS. Use a geometry dtype with CRS to constrain it. Empty outputs retain their columns, dtypes, and spatial metadata.

Encode categorical labels and temporal values as declared strings or numbers. Table cells cannot contain nested objects or lists; parameter models retain their separate JSON-container contract.

Lyra discards producer indexes before validation and rejects explicit Pandera index declarations. Declare identifiers and uniqueness constraints, including compound keys, in columns.

Manifests contain generated `OutputMetadata` and `ColumnMetadata` descriptions as JSON metadata. Python checks appear by stable name and description; metadata cannot reconstruct executable functions.

Public records and WGS84 GeoJSON encode declared scalar values and use null for missing values. Python result readers reconstruct declared logical dtypes and CRS. Infinity and unsupported nested or object values fail validation.

Use Pandera schema strategies with Hypothesis in isolated tests. Custom checks and geometry may need explicit strategies. Call `normalize_result(..., lazy=True)` for bounded diagnostic entries, and test invalid results as well as generated data.

## Register a location provider

Register each concrete `LocationProvider` subclass as a named resource:

```python
plugin = PluginDefinition(resources={"places": PlacesIO})
```

Each provider exposes one collection. Declare its attributes according to the [provider and origin contract](capabilities.md#provider-and-origin-identity). Every public provider ID must identify one resource registration across enabled plugins.

Resource keys select configuration and lifecycle; provider IDs identify records. A provider-only plugin may omit metrics and operations.

Use plain `LocationProvider` for empty configuration. Custom configuration requires both `LocationProvider[Config]` and `config_model = Config`. Providers can declare ordinary resource dependencies as annotated attributes, such as `database: Annotated[DatabaseIO, Resource("city_db")]`. Register dependencies through `PluginDefinition.resources` too.

### Declare schemas and origins

Set `strict=True` and declare these required columns:

- `location_id`: unique, non-null, nonempty strings
- `geometry`: active geometry with the expected CRS and named validity checks
- Every attribute the provider returns

Include descriptions and units for all columns; use `None` when no unit applies. Nullable attributes must still have columns. Reserve `location_id`, `source_location_id`, `row_key`, and `geometry` for their structural roles.

Schemas cannot use these features:

- Optional columns or regex column names
- Filtering or row dropping
- Automatic insertion of missing columns

Import `Origin` from `lyra.sdk`. Declare each direct external dependency as `Origin(id="dataset", revision="release", description="…")`. Identity is `(id, revision)`; descriptions, order, and repeated equivalent origins do not affect identity. Empty `origins` promises no direct acquisition. Metrics acquiring data directly declare their own `origins`; consuming it only through a provider or upstream table needs no repeated declaration.

A provider scope combines its public ID, code revision, and origin identities. That scope fixes each record’s contents and the dataset’s complete membership. Equal IDs across providers establish no correspondence.

Update origin identity when external contents change. Update the provider code revision or ID when changes affect returned records or dataset membership. Omitted revisions have stable `None` values, so keep contents unchanged while those identities remain unchanged.

Resource aliases and descriptive text do not establish identity. If configuration affects output, represent that difference in the provider or origin identity.

### Resolve exact IDs

For a custom `LocationProvider`, implement synchronous `resolve(self, ids: list[str]) -> gpd.GeoDataFrame`. Lyra passes nonempty, deduplicated IDs and requires exactly one complete record for each. Preserve ID spelling and return every schema column in the declared CRS.

Lyra associates records by provider scope and ID, and skips resolution for empty selections. Don’t rely on record order. Return the same record for an ID regardless of the other requested IDs.

Lyra validates returned frames automatically against the registered provider schema, including fake resource overrides. Missing, extra, or duplicate records and columns, invalid geometry, and incorrect CRS fail validation. Convert source columns to their declared types before returning records. Provider records and intervention additions accept either pandas string storage backend, and a nullable `bool` declaration accepts pandas `boolean`. Rename external columns and reproject before returning.

Raise `UnresolvedLocationIDsError(self.id, "location", missing_ids)` with every unknown ID, including total misses. Raise `LocationAcquisitionError` for service failures, chaining the cause without exposing credentials. Runtime diagnostics add the node and input.

Create clients in `setup()`, release them in `close()`, and wrap acquisition in `self.operation("resolve")`. Dependencies initialize first and remain usable during cleanup; borrowers do not close them. Preparation and inspection read declarations without acquiring resources. Prepared scopes must still match before acquisition; declarations do not archive historical data.

### Support intervention sets

To accept interventions on a custom provider, implement the synchronous method `existing_ids(self, ids: list[str]) -> set[str]`. Check the entire base dataset under the same provider scope as `resolve()`. Return only the requested IDs present there and propagate acquisition errors. Parquet and PostGIS providers already implement this method.

Before acquisition, Lyra checks each target for membership support and validates additions against its schema. It then checks all added and removed IDs in one lookup per target, even for empty selections or cached computations. Lyra can reuse a successful lookup for the same base scope and set identity. These rules also apply to catalogs the metric never reads.

Use the location frames Lyra passes to your metric to calculate results for an intervention. Direct resource reads return the original source data. In a selector, filter the supplied target relation. The backend applies your predicate to the remaining base rows and the additions together.

## Select locations in operations

Decorate a synchronous function with `@location_operation`. Declare its name, description, one provider-form source, and target provider; `code_revision` is optional:


```python
@location_operation(
    name="select_streets",
    code_revision="1",
    description="Select complete street records from the selected blocks.",
    inputs={"blocks": LocationDependency(provider="city_blocks")},
    target_provider="city_streets",
)
def select_streets(records: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
    return {
        row.location_id: tuple(json.loads(row.street_ids))
        for row in records.itertuples(index=False)
    }
```

Import `json` and declare `street_ids` as a JSON string attribute in the block schema. The callback receives its source positionally, while binding keys use the declared name `blocks`.

The fixed target provider supplies complete records and their scope. If you map IDs to derived geometries, such as buffers, register those records through a provider first.

Write an ID-only operation callback with one positional source GeoDataFrame. For parameters, add keyword-only `parameters` annotated with a `MetricParameters` subclass and define defaults on that model. For query-backed candidates, follow [managed target selection](#managed-target-selection).

Operations compute from their source, optional selected candidates, and parameters. They cannot declare:

- Table dependencies or extra location inputs
- Resource bindings
- Direct origins

Register operations in `PluginDefinition(location_operations=[...])`. Set `canonical=True` to offer an operation for [automatic routing](capabilities.md#public-selection-and-calculation-methodology) at public inputs and dependency inputs. Metric handlers may declare `context: RunContext` for reporting. Direct function calls don’t orchestrate the graph or validate results through the runtime.

A block metric can supply derived streets internally:

```python
from lyra.sdk import Dependency, Input, LocationDependency

selected = LocationDependency(operation="select_streets").bind(
    blocks=Input("blocks"),
)
inputs = {
    "blocks": LocationDependency(provider="city_blocks"),
    "scores": Dependency("street_scores", producer="street_score").bind(
        streets=selected,
        surroundings=selected,
    ),
    "members": Dependency("membership", producer="street_membership"),
}
```

The owning metric declares `blocks: gpd.GeoDataFrame`; its public request supplies only `blocks`. The street metric declares its own provider inputs for `streets` and `surroundings`. Broader context must not silently expand output IDs. Membership and weights remain ordinary metric-table dependencies.

Every nested `Input` reads the effective inputs of the caller that declares the binding. The binding selects that caller’s source, while the dependency input declares its required provider. Lyra chooses the shortest canonical route between them and uses declared preferences to resolve ties.

When you bind an operation explicitly, Lyra resolves its results through its declared target provider. If the next input requires a different provider, Lyra routes the results to that provider. Before reading data, Lyra rejects missing or ambiguous routes, missing bindings, and cycles in the execution plan.

Supply provider selections in requests, such as `{"provider": "city_blocks", "ids": ["b1"]}`. Omit inputs declared with `LocationDependency(operation=...)`; Lyra computes those inputs.

Empty ID lists and empty operation results are valid. Preserve their declared schemas and make handlers support them. The declared cache policy determines which locations the handler receives.

### Preserve native records and scope

Return a mapping from source IDs to tuples of target IDs. The runtime resolves target records and retains their declared schema and scope even for empty groups. Declarations establish scope; `DataFrame.attrs` does not.

Copy a frame before editing or reprojection. Plugins own geometry repair, topology, relationships, and deterministic selection algorithms. Acquisition belongs to provider resources; provider preparation does not execute graphs.

## Choose a cache policy

Choose a cache policy on each declaration. Lyra accepts these settings and validates them before acquiring resources:

| Declaration | Accepted `cache` values | Default |
| --- | --- | --- |
| Metric with a primary location input | `"off"`, `"full"`, `"per_location"` | `"off"` |
| Metric without a primary input | `"off"`, `"full"` | `"off"` |
| Operation | `"off"`, `"full"`, `"per_location"` | `"off"` |
| Provider | `False`, `True` | `False` |

Mark exactly one metric location input `primary=True` to assign output rows to its locations. The marker doesn’t enable caching or promise independent computation. An operation’s sole source is automatically primary.

Each primary partition contains the output rows for one primary location. Choose `per_location` only if adding or removing other primary locations cannot change that partition’s result. With `cache="per_location"`, the handler may receive only locations with cache misses. That batch must pass every check independently, and combining its rows with retained partitions must also satisfy the checks. Use `cache="full"` for constraints such as uniqueness of a code across locations. This promise includes dependencies and complete shared context. Lyra trusts your declaration without running independence proofs.

Set a provider’s `cache = True` to reuse immutable records by exact ID. Providers reject strings and integers, and metrics and operations reject booleans.

Local and remote execution use the same declared policies, including `run_development()`. Supply selections and calculation parameters in your requests. Update [implementation revisions](#assign-implementation-revisions) and [provider and origin identities](capabilities.md#provider-and-origin-identity) whenever outputs or collection membership change.

For an operation with a target selector, the independence promise covers selection and mapping together. A [cache hit](#reuse-validated-invocation-results) skips both callbacks and the candidate query, including when the saved mapping is empty. On a miss, Lyra queries the complete matching set, even when Lyra has cached individual records. Cached records cannot establish which records match a predicate.

## Reuse validated invocation results

Lyra determines computation keys from declared identities before acquiring inputs. When every requested key has a saved value, Lyra can reuse the complete result: a cache hit.

A complete metric hit skips its handler and upstream work that the result no longer needs. An operation hit skips its selector, candidate query, and mapping handler. Forwarding the selected targets can still require provider records.

Inspect execution observations to check whether a handler ran. Computation keys include:

- Value kind and implementation identity
- Output and computation scopes
- Complete effective parameters
- All named input identities
- Direct origins for metrics, or target provider scope for operations
- Primary-input name for location-scoped metrics

Provider inputs include the provider scope and selected IDs, even for empty selections. [Provider scope](capabilities.md#provider-and-origin-identity) includes the provider ID, code revision, and unordered, deduplicated origin identities. Operation-derived inputs retain upstream operation keys and correspondence groups. Table dependencies retain upstream computation keys and named outputs.

Parameter identity includes defaults and nulls. Omitting a parameter matches supplying its default, while parameter list order affects identity. Dictionary insertion order, location order, and origin order don’t affect identity.

Operational settings don’t contribute to stored-value keys:

- Resource aliases and credentials
- Job IDs and deployment build revisions

`off` and `full` identify complete initially bound selections. Selecting a subset of a full upstream result still references that complete computation.

`per_location` identifies each primary partition by its provider scope, applicable inputs, and complete shared context. Running several partitions in one handler call doesn’t change their keys. Lyra computes only missing partitions and combines them with retained values.

Related dependencies supply groups for the missing primary locations. Independent reference inputs and unpartitioned outputs remain complete. An upstream producer with `off` or `full` caching keeps its complete original selection.

Caching defaults to `off`, and an invoked off node executes even if its value is retained. Managed execution always saves required final outputs and descriptions. Lyra writes intermediates, provider records, and operation mappings when caching or ordinary retrieval needs them. It retains everything written indefinitely, including after cache settings or identities change.

SQLite stores typed rows using compiled Pandera declarations and keeps operation pairs separate from provider records. Each successful computation saves all outputs and applicable partition completion in one transaction. Completed empty values preserve schemas and ownership; failed or unprocessed work never establishes empty completion. Repeated writes retain the existing immutable value for the same key.

Readers reconstruct declared logical types:

| Column | Native type |
| --- | --- |
| String | pandas string |
| Signed 64-bit integer | Nullable `Int64` |
| Finite 64-bit float | Nullable `Float64` |
| Boolean | Nullable boolean |
| Geometry | Shapely/GeoPandas with the declared CRS, decoded from well-known binary (WKB) |

SQLite stores missing values as nulls where declarations allow them. Readers use declared types for empty and all-null columns without inferring types from rows. Storage preserves logical values without retaining arbitrary dtype widths or backing encodings.

Cache reads reconstruct declared types and check that required data and relationships are complete. Reused values have already passed producer validation, so reuse doesn’t repeat the producer’s Pandera checks. Damaged required data fails retrieval; lookup doesn’t rerun handlers to repair it. Use in-process fakes to test these behaviors:

- Input binding and empty results
- Isolation from changes to returned tables and metadata
- Cache reuse skipping handlers

Mock database and service acquisition without bypassing the resource guard. Keep declarations accurate as implementations and data change.

## Overlapping selections

Overlapping requests can reuse the partitions they share. Consider a registered `block_area` metric with a primary `blocks` input and `cache="per_location"`. Starting with an empty store, request A/B, then B/C twice:

```python
first = runner.run(
    "block_area",
    inputs={"blocks": {"provider": "city_blocks", "ids": ["A", "B"]}},
)
selection = {"blocks": {"provider": "city_blocks", "ids": ["B", "C"]}}
second = runner.run("block_area", inputs=selection)
third = runner.run_development("block_area", inputs=selection)
```

Lyra combines the handler’s output with retained partitions to return each complete selection:

| Run | Locations supplied to the handler | Locations in the returned result |
| --- | --- | --- |
| `first` | A and B | A and B |
| `second` | C | B and C |
| `third` | Handler skipped | B and C |

The table describes sets, without promising row order or invocation counts. Each run receives a distinct result reference.

With `cache="full"` and an initially empty store, the second call computes the complete B/C selection; the third reuses it. With `cache="off"`, each invoked computation receives its complete bound selection.

A partial comparison may need only C’s records and related area, but independent reference inputs remain complete. If the references select X/Y, both reference locations and their areas remain available. Changing those references to X/Z changes the comparison keys.

For an upstream producer with an original A/B/C selection, the policy determines how much work it can skip:

| Upstream policy | Work for a consumer that needs only C |
| --- | --- |
| `off` | Compute A/B/C |
| `full` | Reuse the complete A/B/C computation, or compute A/B/C on a miss |
| `per_location` | Reuse C, or compute C on a miss |

The consumer receives C’s related groups under every policy. Independent references and unpartitioned dependencies still supply their complete context.

## Persist and reopen local results

Both `LocalRunner(...)` and `LocalRunner.from_definitions(...)` default to `.lyra/results.sqlite3` under the working directory. Pass `result_store_path=Path("analysis/results.sqlite3")` to choose another file. The constructors resolve default and explicit relative paths when you create the runner.

Lyra creates the parent directory and initializes the store when needed. Give it write access to that directory, including permission to create SQLite journal files. Both `run()` and `run_development()` save required final values before returning success. They publish the retrieval description and complete execution report together in one transaction.

Keep these fields from `result.metadata` together:

- `store_path`: SQLite file path
- `execution_id`: identifier for the complete report
- `result_ref`: identifier for the saved final result

Every successful execution gets both identifiers, including private development runs and empty or cached results. Several results can select the same immutable stored values.

Import `ExecutionPersistenceError` from `lyra.contracts.errors` to handle required storage or publication failures. The exception identifies the attempted `execution_id` and chains the underlying storage error. The run returns no `RunResult`, and the ID doesn’t guarantee a saved report. Complete cache values saved earlier can remain reusable.

Remote publication failures fail the job. Job-handle result and wait methods raise `MetricRunError` for that failed job.

Use `ResultStore` to read the saved result without loading plugins, acquiring providers, or executing handlers. Replace `your_output_name_here` with a declared output name:

```python {5-7}
from lyra.runtime.results import ResultStore

result_ref = result.metadata.result_ref
with ResultStore.open(result.metadata.store_path) as store:
    description = store.describe(result_ref)
    report = store.read_report(result.metadata.execution_id)
    restored = store.read_result(result_ref)
    table = store.read_table(result_ref, "your_output_name_here")
assert restored.report == report
assert report.result_ref == result_ref
```

`describe()` returns the complete description without loading tables or the report body. `read_report()` returns the complete report without loading output tables. `read_result()` returns native tables, metadata, and a report attached as `restored.report`. You own these returned values independently of the saved data.

You can still use returned values after the context closes; nested edits don’t change stored values or later reads. Field access doesn’t make further copies. Reading through a closed reader raises `RuntimeError`, and opening a missing file raises `FileNotFoundError` without creating it.

Lookup errors from `lyra.contracts.errors` share the `ResultAccessError` base:

- `InvalidResultReferenceError`: malformed reference
- `ResultNotFoundError`: well-formed reference without a result, or an undeclared output name
- `ResultIntegrityError`: damaged required data or relationships

These exceptions expose `result_ref` and `output_name`; whole-result reads set `output_name=None`. Report lookup exceptions share the same `ResultAccessError` base and expose `execution_id`:

- `InvalidExecutionIDError`: malformed execution ID
- `ReportNotFoundError`: valid ID without a saved report
- `ReportIntegrityError`: damaged report or required relationships

A missing required report during a result read raises `ResultIntegrityError`. Lookup never reacquires data or reruns handlers. Reports can describe intermediates that execution didn’t need to store.

Lyra retains values, final descriptions, and reports indefinitely while the store’s schema remains compatible with the current version. Changed keys, cache policies, and job expiry don’t delete them. Closed alpha permits an explicit development-store reset for incompatible schemas without promising migrations or historical readers.

Successful remote status, results, and inspections include matching `execution_id` and `result_ref` values. Keep both identifiers with the deployment address. You can use the [remote retrieval methods](#native-table-ownership-and-encoding) after job expiry, or reopen `ResultStore` with filesystem access.

Both Python clients automatically download descriptions that exceed inspection limits. For reports, use `reports.inspect(execution_id)` for a bounded inspection or `reports.get(execution_id)` for the complete model. Save the complete JSON with `reports.download(execution_id, path)`.

Use `results.rows(result_ref, output_name)` for bounded JSON pages. Pass each `next_cursor` unchanged until it is `None`, including after short pages. Interpret cells using these encodings:

- **Integers**: decimal strings
- **Geometry**: full-precision well-known text (WKT) in the declared CRS
- **Missing cells**: `null`

If a row exceeds the page limit, `rows()` raises `RowTooLargeError` without skipping it. Download the complete table with `results.download_table(result_ref, output_name, path)`. The file contains an Arrow interprocess communication (IPC) stream, regardless of its filename extension.

Read saved streams with PyArrow or `GeoDataFrame.from_arrow()` to restore declared scalar types and GeoArrow WKB/CRS metadata. Both file-download methods replace destinations only after success. On failure or cancellation, they preserve the destination and remove temporary files.

## Inspect native location frames

Handlers receive ordinary GeoDataFrames with fixed `location_id` and active `geometry` columns. Read the frame through its native interface:

- `len(frame)` for the row count
- `frame.empty` to check for an empty table
- `frame.location_id` for IDs
- `frame.crs` for the CRS

Read declared attributes as ordinary columns.

Use `frame.copy()` before edits and `frame.to_crs(...)` when the calculation requires reprojection. Providers and metrics preserve all declared columns and dtypes, including zero-row frames. Use native tables or the retained-result reader for declared logical types; public JSON encodes missing values as nulls.

Provider identity remains in runtime bindings rather than mutable frame metadata. Result readers verify stored structure, types, and integrity without loading a producer plugin; arbitrary producer checks require the registered plugin.

### Inspect the generated declarations

Call `description = plugin.describe(name)` to inspect a node’s declaration and handler signature. The `manifest` field contains an independent description.

Read `description.manifest.request_schema` for the complete request schema. Lyra derives this schema from the live parameter model and named location declarations. Each location input records its contract, description, and provider or operation dependency.

Check `description.manifest.kind` to interpret the output. A metric declares named table outputs. A location operation declares its fixed `target_provider` and routing status in `canonical`. The target provider’s executable schema validates acquired target records.

## Prepare complete effective parameters

Pydantic validates parameter values and applies their defaults. This model combines nested defaults with a validator that removes surrounding whitespace from labels:

```python title="effective_parameters.py"
from pydantic import Field, JsonValue, field_validator
from lyra.sdk import MetricParameters


class Filters(MetricParameters):
    tags: list[str] = Field(default_factory=list, description="Required tags.")


class Parameters(MetricParameters):
    radius: float = Field(100.0, gt=0, description="Search radius.")
    label: str | None = Field(None, description="Optional label.")
    filters: Filters = Field(
        default_factory=Filters,
        description="Search filters.",
    )
    options: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="Options.",
    )

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None
```

Serialization includes every effective field, including defaults and nulls:

```python title="effective_parameters.py"
values = Parameters(options={"enabled": True}).model_dump(mode="json")
assert values == {
    "radius": 100.0,
    "label": None,
    "filters": {"tags": []},
    "options": {"enabled": True},
}
assert Parameters(label=" north ").label == "north"
```

Use deterministic, idempotent field validators for normalization. Return fresh containers when transforming lists or dictionaries. Keep model-level cross-field checks read-only and run all validators without external I/O.

Workers receive defaults and nulls explicitly. They revalidate the complete values for every selected node use, so validators must preserve values after JSON transport.

Lyra copies nested request values at acceptance and declaration values at registration. It gives handlers independent parameter models and returns independent descriptions through inspection. Changing those objects cannot alter retained bindings, effective values, or computation identity. Internal field reads use the retained values without copying them.

## Declare ownership and complete dependencies

Mark one metric location input `primary=True` to assign output rows to that input’s locations. The input’s [provider scope](capabilities.md#provider-and-origin-identity) identifies the owning dataset and revision. Without a primary input, outputs are unpartitioned: they describe the complete selection without assigning rows to locations.

### Choose stable output keys

For location-scoped outputs, declare non-null string `location_id` and `row_key` columns. Add `unique=["location_id", "row_key"]` to each output schema. Choose row keys from the calculation’s meaning, such as `area` for one measurement per location, rather than row positions.

### Select records or relationships

Operation-derived inputs preserve every source/target pair. Deduplicate `location_id` when calculating once per target record. When calculating relationships, keep each pair and use a stable relationship key, such as the source ID.

Keep independent reference inputs complete, even when their IDs or providers overlap the primary input. Unpartitioned dependencies retain their complete input context. Selections, correspondence groups, and result rows are unordered; sort explicitly for presentation.

### Bind parameters and handle empty selections

Mapping callbacks receive the source positionally and typed parameters by keyword. Bind using the source declaration’s name. Use `Input("parameters.field")` to forward a field into a different operation model; omitted values use that model’s defaults.

Empty groups complete successfully with no matches. For an empty source selection, Lyra skips the operation callback and retains the target schema and scope.

### Share resources and declare data sources

Providers, metrics, and target acquisition share one resource scope for each execution. Consumers of the same registration receive the same instance, which stays open until they finish. Separate runs receive separate instances.

Declare direct origins on the acquiring provider or metric, even with caching off. Operation callbacks don’t acquire external data; target providers acquire their candidate records. You remain responsible for these properties, which structural validation cannot prove:

- Deterministic, semantically correct calculations
- Stable row keys
- Complete dependencies and origins
- Any declared promise that locations can be calculated independently

### Read tables and metadata from one run

Local and development calls return a `RunResult` for each successful execution. Read its fields according to what you need:

- `result.tables[name]`: named native table
- `result.metadata`: execution and output descriptions
- `result.report`: complete report, attached before the run returns

Reading the attached report performs no storage lookup. Later runs don’t replace these values.

The metadata describes:

- The concrete metric name
- Effective parameters, including defaults and nulls
- Each output’s `scope`, `table_schema`, and `owner_scope`
- The `execution_id`, `result_ref`, and resolved `store_path`

Unpartitioned outputs have `owner_scope=None`. Empty location-scoped outputs retain their owning provider scope. For a run that declares an output named `area`, inspect its table and description together:

```python
area = result.tables["area"]
output = result.metadata.outputs["area"]
print(result.metadata.metric_name, result.metadata.parameters)
print(output.scope, output.owner_scope)
```

Return ordinary tables or operation mappings with their declared schemas. Lyra constructs reports from declarations, resolved bindings, and observed work. You need no reporting wrapper, reporting columns, or extra metadata acquisition.

Keep normalization deterministic and preserve parameter values under repeated validation without I/O. Declare direct origins and implementation revisions explicitly, and follow the resource and cache contracts.

Read the [report interpretation contract](capabilities.md#run-and-inspect-the-contract) to follow requests, resolved uses, and named parameter forwarding. It explains outcomes and operation stages, including declarations for reused and skipped work. Direct handler calls don’t create managed reports.

## Managed target selection

Use managed providers to acquire target candidates through Ibis, then map those candidates in a computational callback. Import the provider classes and `PostgresIO` from `lyra.sdk`. Managed providers resolve exact IDs and execute filters without custom resolvers.

### Declare managed collections

Declare each provider’s identity and complete [native record schema](#declare-schemas-and-origins). The schema must include:

- Unique, nonempty string `location_id` values
- Valid geometry with a declared CRS
- Every attribute, with its description and unit metadata

Register providers and database resources in `PluginDefinition.resources`. Registration exposes query capability and schema without accessing external sources.

For `PostGISLocationProvider`, declare `database: Annotated[PostgresIO, Resource("city_db")]`. Set `db_schema` and `table` to an existing table, view, or materialized view. Configure the database registration with its connection settings and a password secret reference.

Providers borrow the initialized `.backend`; `PostgresIO` initializes it when needed and owns cleanup, including partial setup failures. Compatible subclasses can add domain query methods. Managed access runs sequentially, so synchronize concurrent access you introduce.

Sharing a connection supplies neither origin declarations nor a transaction spanning the run. Each acquiring provider or metric declares its own origins, including reads delegated through resources.

For `ParquetLocationProvider`, configure a string `path` naming one local file. Relative paths resolve from the executing process’s working directory. The provider checks availability at acquisition and owns DuckDB setup and cleanup.

The file path cannot identify:

- A remote URL
- A glob pattern
- A directory
- A collection of files

GeoParquet metadata must agree with the declared CRS. Plain Parquet with WKB geometry uses the schema CRS. Both managed adapters preserve registered types for empty results and reject invalid geometry encodings or CRS metadata. Prepare column names and computed attributes upstream, and reproject geometry before acquisition.

### Run selectors against proposed changes

On the first predicate query for a catalog with interventions, DuckDB and PostGIS stage its additions and removal IDs. Later queries for that catalog reuse the staged data within the same run.

For PostGIS, grant read access to the base relation and the database’s `TEMPORARY` privilege for staging. Base membership checks and exact-ID reads need only read access. Lyra drops the run’s temporary tables after success, failure, or cancellation, including tables from partial setup. The shared database resource retains ownership of its connection.

### Declare callback signatures

Set `target_selector=callback` on an operation whose fixed `target_provider` supports queries. Lyra invokes the callbacks by position:

| Callback | First argument | Second argument |
| --- | --- | --- |
| Selector | Source GeoDataFrame | Unbound Ibis table |
| Mapping handler | Source GeoDataFrame | Candidate GeoDataFrame |

Argument names may differ between callbacks and from the sole source declaration. Both callbacks either omit `parameters` or accept it as a keyword-only argument with the same `MetricParameters` subclass. Lyra infers that model from the mapping handler.

Change the operation ID or its optional `code_revision` when selection or mapping behavior changes. The same revision covers both callbacks.

### Return supported filters

Return the target table unchanged to select all records, or apply ordinary Ibis filters such as `targets.filter(targets.height > 5)`. Filters preserve every column and value. Only true predicates select rows under the backend’s null semantics.

Supported predicates include:

- Comparisons and Boolean composition
- Null tests and membership in literal collections
- Row-wise spatial predicates supported by the backend

Chained filters are valid. The following expression forms are unsupported:

- Other relations, joins, and subqueries
- Aggregates and windows
- Projections and computed columns
- Unions, sorting, limits, offsets, and sampling
- Raw SQL expressions and user-defined functions

Don’t materialize the symbolic table or perform direct external acquisition in either callback. This restriction includes resource acquisition and direct file, database, or network access. You’re responsible for callback behavior; Lyra doesn’t sandbox Python code. Metrics and ordinary resources retain their direct-acquisition contract.

### Map candidates to sources

Write a filter that includes every candidate your mapping needs. Lyra acquires those candidates, then the handler chooses each source’s correspondences. Each source location owns its correspondence group, called a result partition. Results must not depend on row order.

A per-location independence promise covers both callbacks. Adding or removing other sources from a batch must not change a source’s result, even when the shared candidate pool grows.

Mapping keys must belong to the source selection, and target IDs must belong to this invocation’s original validated candidates. Each target may appear under several sources, but only once per source. Lyra reuses validated candidate records when forwarding results. ID-only operations take only the source and resolve returned IDs through the target provider.

Operation-derived inputs can repeat a represented source location under different incoming sources. Deduplicate when your calculation depends only on each location’s record. Retain correspondence rows when their relationships affect the calculation.

### Preserve CRS and working-copy boundaries

Each callback receives independent frames and parameter instances, including detached nested values. Selector mutations cannot affect mapping inputs or retained identity. Mapping mutations cannot change the allowed target IDs or authoritative records that Lyra forwards.

Reproject source geometry in both callbacks when source and target CRSs differ. Geometry literals need coordinates and a SRID matching the target CRS. Assigning an SRID alone does not transform coordinates; Lyra performs no implicit reprojection or distance-unit conversion.

### Handle empty selections

Empty sources skip both callbacks and candidate acquisition. For nonempty sources, Lyra still invokes the mapping handler when selection returns no candidates. The empty candidate frame retains its registered schema and CRS.

Return empty tuples or omit source keys to accept no matches. Raise a domain error if your operation requires a match. Acquisition and validation errors fail the operation instead of producing an empty success.

### Keep collection identities accurate

Provider ID, code revision, and deduplicated origin identities fix membership and record contents. Configuration locates that collection. Change origin identities when external contents change, and provider revisions when acquisition changes affect output.

Unversioned identities also promise fixed contents. Revision labels identify content but do not archive it. Credentials and aliases do not change provider scope; neither do candidate IDs or expressions.

### Inspect retained location inputs

`result.metadata.location_inputs` describes every named root `LocationDependency`.
`ResultStore.describe_result(result_ref)` returns complete metadata without table or
report reads. Use `read_location_input(result_ref, input_name)` for an independent
GeoDataFrame; `RunResult.tables` remains the named metric outputs.

Inputs retain complete logical selections across cached and fresh partitions and
complete shared references. Correspondences retain immediate source/target pairs
with target geometry, qualified by the captured provider scopes. They have no metric
`row_key`. Known empty inputs are available; skipped unresolved inputs are
`not_materialized` and raise `LocationInputUnavailableError` on read. Availability
is fixed at publication. Damaged available inputs raise `ResultIntegrityError`
without acquisition fallback. Retaining records does not change declared cache
policies. Required input persistence failures prevent successful publication.

## Export retained spatial tables

Publish native tables with their declared schema and location scope. Consumers can export retained spatial selections through `ResultStore` or `client.results`:

- `export_geoparquet(result_ref, output_name, path)` for a named spatial output
- `export_location_input_geoparquet(result_ref, input_name, path)` for a direct provider or correspondence input

Use `await` with `AsyncLyraClient`. Each method accepts a local `str` or `Path` destination and returns `None`. Install the dependencies for the chosen access method:

- `lyra[runtime]` for local store access
- `lyra[client,data]` for remote native exports
- `lyra[client]` for ordinary JSON client access

Each export reads retained originals and complete descriptions independently of edits to previously retrieved values. Export doesn’t acquire data or run handlers. Empty spatial selections keep their declared schema and CRS. Nonspatial outputs raise `ValueError`; inputs with `availability="not_materialized"` raise `LocationInputUnavailableError`.

After GeoPandas readback, inspect the JSON-compatible dictionary at `attrs["lyra"]`:

- `kind`: `metric_output` or `location_input`
- `result_ref` and `execution_id`: originating retained result and execution
- `output_name` or `input_name`: selected name
- `description`: complete selected description serialized with `model_dump(mode="json")`, including null scopes

Direct inputs retain provider scope. Correspondence inputs retain immediate source and target scopes, with target geometry and attributes. Provider scope plus `location_id` identifies a provider record independently of the originating result and execution.

A computed output retains its own result and named-table identity; its inputs don’t establish ownership. Structural IDs remain declared columns. Geometry and CRS use standard GeoParquet representation, and export omits pandas indexes.

Create the destination’s parent directory first. Each export closes resources before replacing the destination. Failure or cancellation removes temporary files and preserves an existing destination, or leaves it absent if none existed. Async cancellation may not immediately interrupt synchronous conversion or writing.

Reports remain in the retained store. Supply that store or deployment to follow references; standalone attributes don’t register providers or bind execution.

Use GeoPandas `to_parquet(index=False)` to save your edited frames and `to_file()` for other GIS formats, including GeoPackage and Shapefile. The library and driver determine conversions and preservation. Third-party rewrites may discard metadata. Neither arbitrary dtype widths, pandas indexes, nor file checksums establish record identity.
