# Lyra authoring contract

Use this reference to adapt an external workflow to the `lyra-authoring` contract without a Lyra checkout. Run the bundled native authoring helper in your development environment to check the contract before implementing your adapter.

Use [capability contracts](capabilities.md) to choose source identities and metric registrations. That reference also covers routing, private dependencies, and revision coverage.

Passing checks do not certify scientific correctness or deployment readiness. The helper also cannot verify plugin data-source credentials or connectivity. Validate those responsibilities separately.

## Assign implementation revisions

Declare `code_revision="1"` on every metric and location operation. The registered name and your revision identify the implementation.

Choose a revision that meets these rules:

- Use 1 to 256 characters.
- Start with a letter from `A` to `Z` or `a` to `z`, or a digit.
- Use only those letters, digits, and `._:+-` afterward.

Change your revision when you change computation behavior, including behavior from helpers or dependencies. Package versions and deployment revisions do not substitute for node revisions.

## Parameters and handlers

Decorate each synchronous metric function with `lyra.sdk.metric`. The runtime calls the function by keyword in both local and worker execution, using the declared parameter, location, context, resource, and dependency arguments:

- `parameters` is a concrete Pydantic model. `MetricParameters` supplies `extra="forbid"` and `validate_default=True`; nested models need those settings too. Omit this argument for a metric without ordinary inputs.
- Lyra infers each operation’s optional model from its keyword-only `parameters` annotation.
- Root parameter fields require descriptions. Preserve evidenced defaults and constraints using `Field`; omission and accepting `None` are different choices.
- Supported scalars include strings, booleans, integers, finite floats, null, and homogeneous scalar `Literal` enums. Lists, string-keyed dictionaries, unions, and nested models must contain supported values.
- Use Pydantic `JsonValue` for recursive JSON values containing nested lists and objects.
- Use `Field(default_factory=...)` for containers and nested models. Lyra serializes all effective parameter fields, including defaults and nulls.
- Factories and validators must return the same values for the same inputs. They must not perform external input/output (I/O), such as accessing files or services.
- Validators must preserve normalized values when run again, including after JSON serialization and parsing.
- Do not use aliases, `Any`, sets, tuples, datetimes, recursive models, field exclusions, custom serializers, computed fields, or custom schema overrides. Consult version-matched documentation for other types.
- Named `gpd.GeoDataFrame` arguments receive native records from inputs declared with `LocationDependency(provider="provider_id")`. Each frame includes `location_id`, active `geometry`, the provider CRS, and every declared attribute. Metrics may declare zero or more provider inputs.
- Declare `context: RunContext` to identify the job and node, log diagnostics, manage scratch files, and report progress.

Describe each location argument with `Annotated[gpd.GeoDataFrame, Field(description="…")]`. Import `Annotated` from `typing` and `Field` from `pydantic`. Explain the expected units, geometry kinds, and required properties.

Lyra includes these descriptions in generated request schemas and inspection results. Use prose to explain scientific requirements; location contracts and schemas enforce structural and source compatibility.

Provider declarations establish record scope independently of mutable frame metadata. Copy input frames before transforming them.

Lyra rejects positional-only arguments, variadics, handler argument defaults, asynchronous handlers, and generators. Spatial arguments cannot be nullable or defaulted.

Native inputs preserve all provider attributes and ordered `location_id` values in the declared CRS. Check that the provider supplies every property your calculation requires; reproject explicitly when needed.

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
- **Parameters**: an integer multiplier, a finite float offset in score units, and a list of selected categories
- **Output**: one finite, nonnullable `score` per zone, in score units

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

from lyra.contracts.units import Unit
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
    parameter_2: float = Field(description="Additive offset in score units.")
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
                metadata={"unit": Unit.SCORE},
                nullable=False,
            )
        },
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
            metadata={"unit": Unit.SCORE},
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

Treat dependency tables as read-only and copy them before changing them. Validation may copy or coerce a producer’s table; repeated references share its validated result. Lyra does not enforce immutability.

Separate jobs acquire their inputs independently. Failures stop execution and fail the requested job without partial results or automatic retries. Inspect the failing node and dependency path in diagnostics and worker logs.

## Native table ownership and encoding

Declare a nonempty `outputs` mapping for each metric. Return a DataFrame or GeoDataFrame for one output. For multiple outputs, return a dictionary with exactly the declared keys. `None` is not a valid output.

Metadata describes attribute columns through Pandera schemas, with explicit dtypes, nonblank descriptions, and `metadata["unit"]`. Lyra validates exact column membership, dtypes, nullability, uniqueness, and declared checks before consumers run. Returned validated tables preserve row order and use a fresh RangeIndex without mutating the handler’s frame. Columns are normalized to declaration order before checks.

Producers own row counts, order and identifiers. Publish identifiers as ordinary columns. Do not assign request feature IDs to unrelated result rows. Plugins calculate area fractions and derived columns explicitly. Consumers own any joins, aggregation or alignment they require.

Zero-row tables succeed and still flow to consumers. Preserve their columns and, for spatial outputs, active geometry and correct CRS. Return every declared table even when empty.

Public ordinary tables encode as record arrays (`[]` when empty), without indices. Spatial tables encode as WGS84 GeoJSON FeatureCollections with no index-derived feature IDs. Missing CRS fails serialization; conversion leaves the native graph table unchanged.

Missing cells encode as null and dates/timestamps as ISO strings; unsupported values fail validation. Empty spatial outputs have an empty `features` array.

Retrieve the complete JSON result and select a named table with `client.result_tables(job_id)["output_name"]` or `client.results.tables(job_id)["output_name"]`; async clients await those methods.

### Output units

Import `Unit` from `lyra.contracts.units` and declare, for example, `metadata={"unit": Unit.SQUARE_METRE}` on a Pandera column or field. Every column must explicitly supply `metadata["unit"]`: a `Unit` member, an exact canonical string, or `None` when no unit applies. JSON uses canonical strings or `null`. Labels, identifiers, and booleans normally use `None`; numeric identifiers can also have no applicable unit. Omission, unknown values, aliases, and incorrect casing are rejected.

| Enum member | Value | Meaning |
| --- | --- | --- |
| `MILLIMETRE` | `mm` | Millimetres of length. |
| `METRE` | `m` | Metres of length. |
| `KILOMETRE` | `km` | Kilometres of length. |
| `SQUARE_METRE` | `m2` | Square metres of area. |
| `SQUARE_KILOMETRE` | `km2` | Square kilometres of area. |
| `HECTARE` | `ha` | Hectares of area. |
| `DEGREE_CELSIUS` | `degC` | Temperature in degrees Celsius. |
| `KELVIN` | `K` | Temperature in kelvins. |
| `SECOND` | `s` | Duration in seconds. |
| `DAY` | `day` | Duration in days. |
| `YEAR` | `year` | Duration in years; document the workflow’s year convention. |
| `CALENDAR_YEAR` | `calendar_year` | Calendar-year value, not an elapsed duration. |
| `COUNT_PER_SQUARE_KILOMETRE` | `count/km2` | Count per square kilometre; describe what is counted. |
| `COUNT_PER_HECTARE` | `count/ha` | Count per hectare; describe what is counted. |
| `COUNT_PER_THOUSAND_INHABITANTS` | `count/1000inh` | Count per thousand inhabitants; describe what is counted. |
| `COUNT` | `count` | Number of entities; describe what is counted. |
| `RATIO` | `ratio` | Quotient; describe its numerator and denominator. |
| `PERCENT` | `percent` | Value expressed per hundred (50 means 50%). |
| `SCORE` | `score` | Workflow-defined scale; describe its meaning and interpretation. |
| `DIMENSIONLESS` | `dimensionless` | Known unitless quantity without a more specific applicable unit. |

Units describe what values mean. Declare data types and numeric bounds separately, including a range of 0 to 1 when it applies to a ratio. Progress reports use separate human-readable unit labels.

Choose a unit based on the workflow’s evidence. Use `None` only when no unit applies. If the unit is unknown, ask the user to identify it. If the vocabulary lacks that unit, discuss adding it before you change the declaration or convert any values.

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

Columns appear as bulleted items beneath each output and retain their declared order. Columns with units show only the bracketed unit after their name (for example `density: [count/km2]`). Columns whose unit is `None` show their type instead (for example `code: string`). Explicit `dimensionless` units remain visible.

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

Prefer `pandera.pandas.DataFrameModel` declarations; object `DataFrameSchema` declarations are equally supported. Models compile once into independent registered snapshots. Units remain descriptive. Declare nullable dtypes explicitly and use `coerce=True` only when conversion is intended. Named deterministic custom checks must operate on supplied data. Warning-only checks, parsers, regex/optional columns, filtering, missing-column insertion, and row dropping are unsupported.

For spatial outputs, declare one geometry column with matching active geometry and a known CRS. Use a geometry dtype with CRS to constrain it. Empty outputs retain their columns, dtypes, and spatial metadata.

Use compatible scalar types within each set of category values. Mixing strings with numbers fails validation. Lyra checks unused categories and empty outputs too.

Lyra discards producer indexes before validation and rejects explicit Pandera index declarations. Declare identifiers and uniqueness constraints, including compound keys, in columns.

Manifests contain generated `OutputMetadata` and `ColumnMetadata` descriptions. These are JSON metadata, not author declarations or executable validation. Python checks appear by stable name and description; metadata cannot reconstruct Python functions. Public records and WGS84 GeoJSON contain scalar categorical values, ISO dates/timestamps, and null missing values, without restoring native dtypes on decoding. Infinity and unsupported nested/object values fail validation.

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

Implement synchronous `resolve(self, ids: list[str]) -> gpd.GeoDataFrame`. Lyra passes nonempty, deduplicated IDs and requires exactly one complete record for each. Preserve ID spelling and return every schema column in the declared CRS.

Lyra restores request order and skips resolution for empty selections. Records must be independent of other requested IDs.

Lyra validates returned frames automatically against the registered provider schema, including fake resource overrides. Missing, extra, or duplicate records and columns, invalid geometry, and incorrect CRS fail validation. Declared type coercion runs before handlers receive the native frame. Rename external columns and reproject before returning.

Raise `UnresolvedLocationIDsError(self.id, "location", missing_ids)` with every unknown ID, including total misses. Raise `LocationAcquisitionError` for service failures, chaining the cause without exposing credentials. Runtime diagnostics add the node and input.

Create clients in `setup()`, release them in `close()`, and wrap acquisition in `self.operation("resolve")`. Dependencies initialize first and remain usable during cleanup; borrowers do not close them. Preparation and inspection read declarations without acquiring resources. Prepared scopes must still match before acquisition; declarations do not archive historical data.

## Select locations in operations

Decorate a synchronous function with `@location_operation`, supplying `name`, `code_revision`, `description`, exactly one provider-form source in `inputs`, and the target provider ID:

```python
@location_operation(
    name="select_streets",
    code_revision="1",
    description="Select complete street records from the selected blocks.",
    inputs={"blocks": LocationDependency(provider="city_blocks")},
    target="city_streets",
)
def select_streets(blocks: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    return selected_street_records(blocks)
```

This example assumes the block schema includes complete street records and `selected_street_records` returns their union in a consistent order. The target provider determines the output schema and record scope. Return complete records for that target, including every declared attribute and geometry. Derived records, such as buffers, need their own target provider declaration and matching records.

Operations compute from their one source and optional typed parameters. They have no table dependencies, extra location inputs, resource bindings, direct origins, or acquisition context. An optional `context: RunContext` supports reporting. Register operations in `PluginDefinition(location_operations=[...])`; `canonical=True` additionally offers the operation for public routing. Direct function calls perform no graph orchestration or managed result validation.

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

Every nested `Input` reads the effective inputs of the metric or operation that declares it. Graph validation checks required bindings and cycles. Requests supply provider selections, such as `{"provider": "city_blocks", "ids": ["b1"]}`. Omit inputs declared with `LocationDependency(operation=...)`; Lyra computes those inputs.

Empty ID lists and empty operation results are valid. Preserve their declared schemas and make handlers support them. Handlers execute once per whole set.

### Preserve native records and scope

Return the target schema’s `location_id`, `geometry`, and attribute columns with declared types and CRS, including for empty frames. Lyra validates operation outputs against the target provider schema. Scope comes from declarations, never `DataFrame.attrs`.

Copy a frame before editing or reprojection. Plugins own geometry repair, topology, relationships, and deterministic selection algorithms. Acquisition belongs to provider resources; provider preparation does not execute graphs.

### Reuse validated invocation results

Local and worker execution use managed resolution and resource lifecycles. Each invocation prepares parameters and location bindings before checking reuse. Preparation may execute operations and their dependencies. A cache hit skips the handler and remaining table dependencies.

Reuse identity includes concrete node kind and name, effective parameters, resolved location contracts and content under receiving argument names, native and transitive calculation revisions, computation namespace, and run-wide cache revision. List order matters; caller identity and execution paths do not. Equivalent invocations share complete results within a run and optionally across runs.

Persistent metric and location results use uncompressed Arrow IPC storage. Tables passed between metrics and frames restored from cache retain declared data types, including empty and all-null columns.

On a cache miss, Lyra finishes dependencies before acquiring the consumer lock. It then rechecks the cache, runs the handler, validates the results, and publishes them. Locks do not nest.

The runtime validates cached results against their producer schemas and applies declared type conversions. If cache access or validation fails, the runtime can recompute the result. A server job must save its final output artifact to succeed.

An invalid handler result fails the invocation. Lyra caches only successful invocations, including dependencies that finished before a later failure.

Persistent reuse requires accurate revision coverage, deterministic handlers, and equivalent implementations and resource configuration. Selection-only revisions stop at the native-input boundary; native and explicit calculation revisions remain in the metric identity. A hit can still require acquisition and intermediate reads. Manage uncovered data changes and cleanup explicitly; see [revision and pinning limits](capabilities.md#revisions-cache-reuse-and-pinning). Test operations, bindings, empty results, and reuse with in-process fakes; do not connect to services or bypass the test resource guard.

### Set caller cache revisions

Use origin identities and provider code revisions for covered dataset changes. Change `cache_revision` for external changes outside that coverage or unversioned implementation changes; output validation cannot detect either change. Choose a string of at most 256 characters containing at least one non-whitespace character. Lyra preserves the exact value, including whitespace.

Pass the revision separately from metric inputs when running locally:

```python
runner.run("metric_name", inputs=inputs, cache_revision="dataset-2026-10")
```

With the Python client, supply the revision when submitting a job:

```python
client.jobs.submit(
    "metric_name",
    inputs=inputs,
    cache_revision="dataset-2026-10",
)
```

For HTTP and MCP requests, add `cache_revision` at the same level as `metric`, `inputs`, and `parameters`. Lyra records the revision in job provenance without changing handler parameters or location content fingerprints. Content fingerprints include declared column types but exclude descriptions and validation functions.

Omitting the revision or repeating a value permits reuse when the other cache inputs match. Changing the revision adds a run-wide identity component alongside location revisions and computation identity and keeps older artifacts available. Return to a previous revision to reuse its valid cached results.

## Inspect native location frames

Handlers receive ordinary GeoDataFrames with fixed `location_id` and active `geometry` columns. Use `len(frame)` and `frame.empty` for counts, `frame.location_id` for ordered IDs, and `frame.crs` for the coordinate reference system. Read declared attributes as ordinary columns.

Use `frame.copy()` before edits and `frame.to_crs(...)` when the calculation requires reprojection. Preserve all target schema columns and dtypes in operation results, including zero-row frames. Use native tables or Arrow artifacts when preserving declared types; public JSON converts dates and timestamps to strings and missing values to nulls.

Provider identity remains in runtime bindings rather than mutable frame metadata. Artifact readers verify stored structure, types, and integrity without loading a producer plugin; arbitrary producer checks require the registered plugin.

### Inspect the generated declarations

Call `description = plugin.describe(name)` to inspect a node’s declaration and handler signature. The `manifest` field contains an independent description.

Read `description.manifest.request_schema` for the complete request schema. Lyra derives this schema from the live parameter model and named location declarations. Each location input records its contract, description, and provider or operation dependency.

Check `description.manifest.kind` to interpret the output. A metric declares named table outputs. A location operation declares its output structure and scope in `output_contract.family` and its routing status in `canonical`. The target provider’s executable schema supplies type coercion and custom checks.

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

Lyra supports ordinary field and model validators. Your validators must produce deterministic results without external I/O and preserve normalized values on repeated validation. Lyra trusts this author contract; it does not prove validator purity.

Workers receive defaults and nulls explicitly. They revalidate the complete values for every selected node use, so validators must preserve values after JSON transport.

Lyra copies nested request values at acceptance and declaration values at registration. It gives handlers independent parameter models and returns independent descriptions through inspection. Changing those objects cannot alter retained bindings, effective values, or computation identity. Internal field reads use the retained values without copying them.
