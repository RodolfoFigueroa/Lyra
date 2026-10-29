# Lyra authoring contract

Use this reference to adapt an external workflow to the `lyra-authoring` contract without a Lyra checkout. Run the bundled compatibility helper in your target environment to check spatial and authoring behavior. Its software development kit (SDK) version report identifies the installed version; it does not restrict supported releases.

Passing checks do not certify scientific correctness or deployment readiness. The helper also cannot verify the application’s Earth Engine initialization. Validate those responsibilities separately.

## Parameters and handlers

Decorate each synchronous metric function with `lyra.sdk.metric`. The worker calls the function by keyword, using these reserved arguments and any declared dependency inputs:

- `parameters` is a concrete Pydantic model. `MetricParameters` supplies `extra="forbid"` and `validate_default=True`; nested models need those settings too. Omit this argument for a metric without ordinary inputs.
- Root parameter fields require descriptions. Preserve evidenced defaults and constraints using `Field`; omission and accepting `None` are different choices.
- Supported parameter values include strings, booleans, integers, finite floats, null, homogeneous scalar Literal enums, lists, string-keyed dictionaries, unions, and nested models. Do not use aliases, `Any`, sets, tuples, datetimes, recursive models, default factories, custom serializers, computed fields, or custom schema overrides. Consult version-matched documentation for other types.
- `location: LocationInput` receives one or more Polygon/MultiPolygon GeoJSON features, with string identifiers and a declared coordinate reference system (CRS). `bounds: BoundsInput` receives exactly one Polygon feature. Declare spatial inputs only when needed. Bounds need not be rectangular.
- Declare `context: RunContext` to identify the job and node, log diagnostics, manage scratch files, and report progress.

Lyra rejects positional-only arguments, variadics, handler argument defaults, asynchronous handlers, and generators. Spatial arguments cannot be nullable or defaulted.

`convert_geojson_to_gdf` from `lyra.utils.geometry` preserves the declared CRS, feature properties, order, and feature IDs as the GeoDataFrame index. It does not reproject or create workflow-specific attributes. Resolved administrative zones are not guaranteed to contain arbitrary attributes required by a calculation. Inputs are not universally EPSG:4326. Preserve reprojection already performed by the calculation; do not restrict the incoming CRS to its internal working CRS. The platform’s region selection does not establish a dataset’s geographic coverage or a metric’s scientific applicability. Distinguish region size or administrative level from raster reduction resolution. Do not infer unlimited validity or invent restrictions from missing documentation. Report incomplete validation in the handoff rather than adding generic metadata caveats.

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

Workers initialize Earth Engine before importing plugin factories when the deployment configuration includes `[earth_engine]`. Omitting the section disables initialization for every worker using that document.

Deployment configuration supplies `earth_engine.project` and `earth_engine.service_account_file`. Lyra reads the service-account credentials and calls `ee.Initialize` with that project. Keep credential sources and project IDs in deployment configuration.

Use the initialized `ee` environment in metric handlers. Keep authentication and initialization out of handlers and factories. Keep credentials, project parameters, and imports from `lyra_app` out of metric code. Your deployment also needs permission to access each private asset.

Manifest generation imports plugins without starting Lyra. You can import `ee`, but construct initialization-dependent objects during metric execution. Keep these expressions out of module scope, factories, and function or parameter defaults.

Direct Python calls, SDK helpers, and standalone scripts do not initialize Earth Engine. Offline tests should fake or mock the workflow’s Earth Engine boundary; make authentication and initialization fail if called during import or manifest generation. An authorized live test or standalone script needs a separate setup harness using its configured environment. Ask about local credentials only when such a live execution is requested and the setup is unknown; ordinary plugin authoring and offline validation do not require that question.

`lyra.utils.ee.reduce_ee_image_over_gdf` already reprojects a copy of the geometry to EPSG:4326 and passes its `scale` argument as reduction resolution in metres. Preserve an existing workflow’s dataset, reducer, resolution, reprojection, and feature mapping. These choices are evidence from the calculation, not new platform defaults. Real computation may return unavailable values or require asset access; establish handling from the workflow or ask if a decision is needed.

## Example: a documented existing calculation

This example assumes an existing `workflow.calculate_metric` whose documented contract is: Polygon/MultiPolygon zones in EPSG:4326, numeric `base_value` and string `category` properties, unique string index preserved in order; required integer multiplier, float offset in score units, and list of selected categories; one finite, nonnullable `score` column in score units. Selected rows compute `base_value * parameter_1 + parameter_2`, others return `0.0`. No defaults or additional parameter ranges are specified. These are example facts, not defaults to apply to a user’s calculation.

```python title="adapter.py"
import pandas as pd

import pandera.pandas as pa

from lyra.sdk import (
    LocationInput,
    MetricParameters,
    PluginDefinition,
    Unit,
    metric,
)

from lyra.utils.geometry import convert_geojson_to_gdf

from pydantic import Field

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

Describe the calculation and declare its output:

```python title="adapter.py"
metric_description = (
    "Compute a synthetic property-based score for each input "
    "zone.\n\nFor categories selected by parameter_3, calculate "
    "base_value * parameter_1 + parameter_2; return zero for "
    "other categories. Inputs must be EPSG:4326 polygons or "
    "multipolygons with a finite numeric base_value and string "
    "category property."
)

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
        }
    )
}
```

Register the handler with that contract:

```python title="adapter.py"
@metric(
    name="zone_score",
    description=metric_description,
    outputs=metric_outputs,
)
def run(location: LocationInput, parameters: Parameters) -> pd.DataFrame:
    return calculate_metric(
        convert_geojson_to_gdf(location),
        parameter_1=parameters.parameter_1,
        parameter_2=parameters.parameter_2,
        parameter_3=parameters.parameter_3,
    )


def create_plugin() -> PluginDefinition:
    return PluginDefinition(nodes=[run])
```

Keep the calculation in its existing module. Add this adapter to the project’s package, and point its factory configuration at the actual module. A plugin may register several metrics explicitly; Lyra does not discover sibling modules.

## Metric dependency graphs

Use `@metric` for acquisition, transformations, and calculations, and register each function with `PluginDefinition(nodes=[...])`. Every registered metric can run directly or supply named tables to other metrics. Separate acquisition and calculation when that improves reuse or testing.

A metric can use source clients or file readers to produce its output tables. Use `context.temp_dir` for scratch files during processing.

Node names identify calculations and public requests. Output names identify whole tables, not columns. Both namespaces are globally unique across enabled plugins; an output may share only its own producer’s node name. For example:

```python
inputs = {
    "records": Dependency(
        "example_selected_records",
        arguments={"parameters": {"year": Input("parameters.baseline_year")}},
    )
}
```

The key `records` is a local function argument; the referenced name is an explicit output, with no fallback to a node name. `Input` refers to a directly declared field of the current node, not the root request. References can cross plugins without importing the producer. Each node declares its own parameters; dependencies do not add public request fields. Context and dependency arguments are excluded from public request schemas.

Upstream ordinary fields resolve from explicit overrides, then matching effective consumer parameters (including defaults), then producer defaults; otherwise resolution fails. Explicit `None` is a value. Overrides replace whole fields, including nested objects. `arguments` accepts only `parameters`; context, tables, and spatial arguments are not bindings. Required `location` and `bounds` forward unchanged by name and kind. Every consuming node must declare the spatial inputs needed by its dependencies even when its calculation only uses their tables.

Metrics with a fixed region can define their own sources and omit both spatial arguments. To pass a boundary downstream, return it as a GeoDataFrame dependency. Graphs that use a client-selected region must declare the required spatial inputs along each dependency path.

Your plugin determines coverage checks and geographic calculations. Use `lyra.utils.geometry.calculate_feature_areas_m2` when it matches your area calculation.

Request the database resource through a `database: Annotated[DatabaseIO, Resource("database")]` argument. Import `Annotated` from `typing`, and import `DatabaseIO` and `Resource` from `lyra.sdk`. Use bound parameters for query values:

```python
frame = database.query_dataframe(sql, params={"year": 2020})
spatial = database.query_geodataframe(
    sql, params={"year": 2020}, geom_col="geometry", crs="EPSG:6372"
)
```

Workers initialize the database resource when invoking a node that requests it. Ask your deployment operator to configure PostgreSQL before using it. Without database configuration, that access fails the job with `database_not_configured` and `retryable=false`. Other context services work without PostgreSQL.

Use bound SQL parameters. Source names and query logic belong to the plugin; Lyra manages connections. Spatial queries normally infer CRS from returned SRIDs. An explicit `crs` labels coordinates, without reprojecting them, and can supply metadata for empty or all-null geometry results. Supply the correct source CRS.

Registration and manifest generation check local declarations and signatures. They work without installing referenced implementations or accessing sources. Loading enabled manifests resolves references and checks name collisions, cycles, parameter paths and required spatial forwarding for the whole graph. Execution validates actual arguments, Pandera output schemas and public serialization.

Direct callable invocations supply dependency tables explicitly; they do not execute the graph. For local graph tests, build manifests with `PluginDefinition.manifest(...)`, pass them to `lyra.sdk.graph.build_graph`, and create `lyra.sdk.execution.GraphExecutor(graph, plugins, context_factory)`. Execute `plugin.prepare_invocation(metric_name, resolved_input)` with fake source clients. The plugins mapping uses the same distribution keys as the graph.

A requested metric returns all its declared outputs; intermediate dependency outputs remain internal. It executes dependencies sequentially on the selected worker. Identical producer invocations reuse native tables within that job, including all outputs of a multi-output producer. Consumers treat dependencies as read-only and make an appropriate explicit copy before mutation. Validation may copy or coerce a producer’s table; repeated references share its validated result. Lyra does not enforce immutability. Separate jobs acquire their inputs independently. Failures stop execution and fail the requested job without partial results or automatic retries. Inspect the failing node and dependency path in diagnostics and worker logs.

## Native table ownership and encoding

Every node declares a nonempty `outputs` mapping. One output may return its native DataFrame or GeoDataFrame directly; multiple outputs return a dictionary with exactly the declared keys. `None` is not an output. Metadata describes attribute columns through Pandera schemas, with explicit dtypes, nonblank descriptions, and `metadata["unit"]`. Lyra validates exact column membership, dtypes, nullability, uniqueness, and declared checks before consumers run. Returned validated tables preserve row order and indices; identity with the handler’s object is not promised.

Producers own row counts, order, indices and identifiers. Publish identifiers as ordinary columns. Do not assign request feature IDs to unrelated result rows. Plugins calculate area fractions and derived columns explicitly. Consumers own any joins, aggregation or alignment they require.

Zero-row tables succeed and still flow to consumers. Preserve their columns and, for spatial outputs, active geometry and correct CRS. Return every declared table even when empty. Public ordinary tables encode as record arrays (`[]` when empty), without indices. Spatial tables encode as WGS84 GeoJSON FeatureCollections with no index-derived feature IDs. Missing CRS fails serialization; conversion leaves the native graph table unchanged. Missing cells encode as null, dates/timestamps as ISO strings; unsupported values fail clearly. Empty spatial outputs have an empty `features` array. Clients retrieve the complete JSON result and select a named table with `client.result_tables(job_id)["output_name"]` or `client.results.tables(job_id)["output_name"]`; async clients await those methods.

### Output units

Import `Unit` from `lyra.sdk` and declare, for example, `metadata={"unit": Unit.SQUARE_METRE}` on a Pandera column or field. Every column must explicitly supply `metadata["unit"]`: a `Unit` member, an exact canonical string, or `None` when no unit applies. JSON uses canonical strings or `null`. Labels, identifiers, and booleans normally use `None`; numeric identifiers can also have no applicable unit. Omission, unknown values, aliases, and incorrect casing are rejected.

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
| `COUNT` | `count` | Number of entities; describe what is counted. |
| `RATIO` | `ratio` | Quotient; describe its numerator and denominator. |
| `PERCENT` | `percent` | Value expressed per hundred (50 means 50%). |
| `SCORE` | `score` | Workflow-defined scale; describe its meaning and interpretation. |
| `DIMENSIONLESS` | `dimensionless` | Known unitless quantity without a more specific applicable unit. |

Units declare meaning; they do not convert values or impose numeric ranges or column-type restrictions. A ratio is not automatically restricted to 0–1. `None` means not applicable, never unknown. Establish the unit from the workflow before selecting an identifier. Do not substitute dimensionless, score, or null for missing scientific information. If the required unit is absent from this vocabulary, ask the user about adding it to the SDK; do not invent an identifier or silently convert the calculation. Progress-reporting units are separate human-readable labels.

## Package and generate

The project needs static `[project].name` and `version`, direct dependencies, and `[tool.lyra].factory = "your_package.metrics:create_plugin"` in `pyproject.toml`. Use actual package and module names. Derive `requires-python` from the existing project policy and dependency requirements, and verify that it includes the interpreter used for testing. For this SDK baseline the minimum is Python 3.11; other workflow dependencies or project policy may require a later version. Do not choose a newer minimum arbitrarily or claim validation on an excluded version. Declare `lyra-sdk`, and any directly imported packages such as pandas, pydantic, or `lyra-utils`. GeoPandas belongs in dependencies when imported by the existing workflow. Use approved dependency sources; these instructions do not assume Lyra packages have been published to PyPI.

For a new package using Hatch, configure package discovery and include the manifest as shared data and in source archives. For example, with package `your_package` and distribution `your-plugin`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["your_package"]

[tool.hatch.build.targets.wheel.shared-data]
"lyra.plugin.json" = "share/lyra/plugins/your-plugin/lyra.plugin.json"

[tool.hatch.build.targets.sdist]
include = ["your_package", "lyra.plugin.json", "pyproject.toml"]
```

The shared-data directory uses the normalized distribution name: lowercase, with runs of dots, underscores, and hyphens replaced by a hyphen. The manifest’s name and version must agree with package metadata. Preserve an existing build backend and use its equivalent configuration rather than replacing it.

Run in the plugin environment:

```sh
uv run lyra-plugin build-manifest
uv run lyra-plugin describe
uv run lyra-plugin check-manifest
uv run pytest
```

The generator imports the factory to derive the manifest. Avoid import-time calculation, data downloads, or service connections. Include the generated root `lyra.plugin.json` in the deliverable, never edit it by hand. The API reads this artifact; workers import the factory and reject stale contracts.

## Local verification and requests

Decorated functions remain callable. Direct calls do not prepare parameters, resolve spatial references, inject context, or normalize results. In tests:

1. Construct resolved `LocationInput` geometry with representative properties, distinct values, and IDs in a nonlexical order.
2. Call `plugin.prepare_parameters(metric_name, values)` to apply parameter validation and defaults when the metric declares a parameter model. It raises `MetricInputError` for invalid values. Skip preparation and omit `parameters` for a parameterless handler: calling this helper then raises `PluginDefinitionError`, even with an empty dictionary.
3. Call the adapter and original calculation with the same inputs, comparing values and row/column identities, not only shapes.
4. Call `plugin.normalize_result(metric_name, frame)` to check output names and native table types, Pandera constraints, and serialization requirements. It returns validated tables and raises `MetricResultError` for invalid output. Check public encoding separately with `lyra.sdk.results.serialize_outputs(metric_name, outputs)` using that mapping.
5. Exercise invalid parameters and result mismatches. Supply a strict fake context only if the handler uses it.

These SDK helpers require no API, Redis, RQ, PostGIS, or Earth Engine connection. They cannot prove that the calculation’s data sources or scientific assumptions are valid. Validate the existing workflow independently using its own tests. If an adapter explicitly raises `MetricInputError`, its constructor takes `(metric, path, message)`, for example `MetricInputError("zone_score", "location", "Required zone attributes are missing")`; it is not a single-message exception.

Requests nest ordinary values under `input.parameters` and a supported spatial reference under `input.location` (and/or `input.bounds`). Even all-default parameter models require a `parameters` object; parameterless metrics omit it. For the example, explicit GeoJSON must include its required `base_value` and `category` properties. Do not suggest an administrative-zone reference unless those attributes can actually be supplied by that workflow.

## Further reading

Use the documentation version corresponding to the target SDK; the following links are the development source and can change:

- [Plugin authoring](https://github.com/RodolfoFigueroa/lyra/blob/main/docs/src/content/docs/plugins/authoring.md)
- [Packaging and deployment](https://github.com/RodolfoFigueroa/lyra/blob/main/docs/src/content/docs/plugins/publish-and-debug.mdx)
- [SDK source](https://github.com/RodolfoFigueroa/lyra/tree/main/packages/lyra_sdk)

For a fixed reference, replace `main` with the selected product release tag or commit. Installed copies of this skill do not update automatically.

## Inspect dependency diagrams

Export the saved manifest as Mermaid source:

```sh
uv run lyra-plugin diagram --project . --output graph.mmd
uv run lyra-plugin diagram --include-manifest ../dependency/lyra.plugin.json
```

Omit `--output` to write to stdout. Repeat `--include-manifest` to include more plugins. Paths are relative to the working directory. Regenerate the manifest with `build-manifest` after changing declarations; diagram export reads saved contracts without importing factories or checking freshness.

The diagram lists every declared output, including outputs with no consumers. Edge labels identify each output and the input that receives it. Add `--detailed` to show output columns, types, units, and geometry columns:

```sh
uv run lyra-plugin diagram --detailed --output graph.mmd
```

Columns appear as bulleted items beneath each output and retain their declared order. Columns with units show only the bracketed unit after their name (for example `density: [count/km2]`). Columns whose unit is `None` show their type instead (for example `code: string`). Explicit `dimensionless` units remain visible. Detailed metric nodes also list dependency inputs as `input_name: producer_node.output_name` beneath an **Inputs** heading. When the producer is absent from the supplied manifests, only the output name is shown. Nodes without dependency inputs omit this section. This section describes declared dependency bindings, not request parameters or spatial inputs. Descriptions and nullability are omitted. Missing producers appear as dashed external-output nodes. Cycles and incompatible cross-node contracts remain inspectable: this view does not certify execution validity. Duplicate identities or ambiguous node/output ownership are errors.

Python callers can use `lyra.sdk.diagrams.render_mermaid(manifests)` with a sequence of `PluginManifest` objects; pass `detailed=True` for column metadata. To create an image, render the exported source separately with the [Mermaid CLI](https://github.com/mermaid-js/mermaid-cli), for example `mmdc -i graph.mmd -o graph.svg`.

## Define custom resources

Define an input/output (I/O) resource to share file or service access across nodes. Use these declarations:

- Extend `BaseIO[Config]`, where `Config` is your Pydantic configuration model, and set `config_model = Config`.
- Register the class in `PluginDefinition` through a mapping such as `resources={"shared_files": FileIO}`.
- Declare a node argument such as `files: Annotated[FileIO, Resource("shared_files")]` to request the resource.

Names apply across the deployment. Plugins sharing a name must register the same class; they share one instance per job. Lyra initializes requested resources before invoking their nodes and closes them in reverse initialization order when the job ends.

Create clients in `setup()` and release them in `close()`. Cleanup must also release clients created before a setup failure. Wrap each complete synchronous read or download in `with self.operation("read"):`; methods may return Python objects, including DataFrames.

Declare required `secret_names` and read resolved values from `self.secrets`. Deployment configuration supplies environment-variable or file references. Imports, registration, and manifest generation must not resolve credentials or contact services.

Pass resource settings to `LocalRunner` through `resource_config`. For tests, pass fake subclasses of registered resource classes through `resource_overrides`. The runner manages setup and cleanup; mock all service access in tests.

## Pandera validation and generated metadata

Prefer `pandera.pandas.DataFrameModel` declarations; object `DataFrameSchema` declarations are equally supported. Models compile once into independent registered snapshots. Units remain descriptive. Declare nullable dtypes explicitly and use `coerce=True` only when conversion is intended. Named deterministic custom checks must operate on supplied data. Warning-only checks, parsers, regex/optional columns, filtering, missing-column insertion, and row dropping are unsupported.

For spatial outputs, declare one geometry column with matching active geometry and a known CRS. Use a geometry dtype with CRS to constrain it. Empty outputs retain their columns, dtypes, and spatial metadata.

Use compatible scalar types within each index level and within each set of category values. Mixing strings with numbers fails validation, as do arbitrary index objects. Lyra checks unused categories and empty outputs too.

Native indices stay internal. Lyra validates every `MultiIndex` level, even without an index schema. Add an optional Pandera index schema for constraints such as uniqueness.

Manifests contain generated `OutputMetadata` and `ColumnMetadata` descriptions. These are JSON metadata, not author declarations or executable validation. Python checks appear by stable name and description; metadata cannot reconstruct Python functions. Public records and WGS84 GeoJSON contain scalar categorical values, ISO dates/timestamps, and null missing values, without restoring native dtypes on decoding. Infinity and unsupported nested/object values fail validation.

Use Pandera schema strategies with Hypothesis in isolated tests. Custom checks and geometry may need explicit strategies. Call `normalize_result(..., lazy=True)` for bounded diagnostic entries, and test invalid results as well as generated data.
