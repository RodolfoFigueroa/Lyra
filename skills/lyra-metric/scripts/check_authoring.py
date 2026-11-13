"""Exercise native provider authoring in the project environment."""

from __future__ import annotations

import importlib
import sys
from importlib import metadata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

    import geopandas as gpd

CONTRACT_REVISION = "lyra-authoring"
REQUIRED_IMPORTS = {
    "LocationSelection": "lyra.contracts.locations",
    "LocationProvider": "lyra.sdk",
    "Origin": "lyra.sdk",
    "UnresolvedLocationIDsError": "lyra.contracts.errors",
    "LocationAcquisitionError": "lyra.contracts.errors",
    "MetricParameters": "lyra.sdk",
    "LocalRunner": "lyra.runtime.local",
    "LocationDependency": "lyra.sdk",
    "location_operation": "lyra.sdk",
    "IOConfig": "lyra.sdk",
    "ResourceConfig": "lyra.sdk",
    "SecretReference": "lyra.sdk",
    "NodeDescription": "lyra.sdk",
    "NodeImplementation": "lyra.sdk",
    "BoundPlugin": "lyra.runtime.invocation",
    "PluginDefinition": "lyra.sdk",
    "Unit": "lyra.contracts.units",
    "Dependency": "lyra.sdk",
    "Input": "lyra.sdk",
    "RunContext": "lyra.sdk",
    "MetricInputError": "lyra.contracts.errors",
    "MetricResultError": "lyra.contracts.errors",
    "PluginDefinitionError": "lyra.contracts.errors",
    "metric": "lyra.sdk",
    "BaseIO": "lyra.sdk",
    "Resource": "lyra.sdk",
}


class ContractMismatchError(Exception):
    """The installed SDK differs from the checked authoring baseline."""


def require(condition: object, detail: str) -> None:
    """Raise a concise mismatch when an expected behavior is absent.

    Raises:
        ContractMismatchError: The condition is false.
    """
    if not condition:
        raise ContractMismatchError(detail)


def native_checks(modules: dict[str, ModuleType]) -> None:
    """Run native providers and a metric, including invalid producer results.

    Raises:
        ContractMismatchError: Execution or boundary validation differs.
    """
    gpd = importlib.import_module("geopandas")
    pd = importlib.import_module("pandas")
    pa = importlib.import_module("pandera.pandas")
    point = importlib.import_module("shapely.geometry").Point
    sdk = modules["lyra.sdk"]
    schema = pa.DataFrameSchema(
        {
            "location_id": pa.Column(
                str,
                unique=True,
                checks=pa.Check(lambda values: values.str.len().gt(0), name="nonempty"),
                description="Record ID",
                metadata={"unit": None},
            ),
            "geometry": pa.Column(
                importlib.import_module("pandera.engines.geopandas_engine").Geometry(
                    crs="EPSG:4326"
                ),
                checks=pa.Check(
                    lambda values: values.is_valid & ~values.is_empty,
                    name="valid_geometry",
                ),
                description="Point",
                metadata={"unit": None},
            ),
            "value": pa.Column(int, description="Value", metadata={"unit": None}),
        },
        strict=True,
    )
    frame = gpd.GeoDataFrame(
        {"location_id": ["zone-z", "zone-a"], "value": [3, 7]},
        geometry=[point(0, 0), point(1, 1)],
        crs=4326,
    )
    events = []
    producer = {"frame": frame}

    def resolve(_self: object, ids: list[str]) -> object:
        events.append(("resolve", ids))
        return producer["frame"].copy()

    provider = type(
        "ProbeProvider",
        (sdk.LocationProvider,),
        {
            "id": "probe_places",
            "code_revision": "1",
            "schema": schema,
            "origins": (sdk.Origin(id="probe-fixture", revision="1"),),
            "setup": lambda _: events.append("setup"),
            "close": lambda _: events.append("close"),
            "resolve": resolve,
        },
    )

    def calculate(places: gpd.GeoDataFrame) -> object:
        events.append("calculate")
        return pd.DataFrame({"value": places["value"] * 2})

    calculate.__annotations__ = {"places": gpd.GeoDataFrame, "return": pd.DataFrame}
    handler = sdk.metric(
        name="native_probe",
        code_revision="1",
        description="Double fixture values",
        inputs={"places": sdk.LocationDependency(provider=provider.id)},
        outputs={
            "values": pa.DataFrameSchema(
                {
                    "value": pa.Column(
                        int, description="Doubled value", metadata={"unit": None}
                    )
                }
            )
        },
    )(calculate)
    runner = modules["lyra.runtime.local"].LocalRunner.from_definitions(
        {
            "probe": sdk.PluginDefinition(
                metrics=[handler], resources={"places_access": provider}
            )
        }
    )
    inputs = {
        "places": {"provider": provider.id, "ids": ["zone-a", "zone-z", "zone-a"]}
    }
    output = runner.run("native_probe", inputs=inputs)
    require(
        output["values"].value.tolist() == [14, 6],
        "native execution: values or ID order changed",
    )
    require(
        events.count("setup") == events.count("close") == 1
        and ("resolve", ["zone-a", "zone-z"]) in events,
        "native execution: acquisition lifecycle or exact-ID deduplication changed",
    )
    events.clear()
    output = runner.run(
        "native_probe", inputs={"places": {"provider": provider.id, "ids": []}}
    )
    require(output["values"].empty, "native execution: expected empty output")
    require(events == ["calculate"], "empty selection acquired resources")
    for invalid in (
        gpd.GeoDataFrame(
            {"location_id": ["zone-z", "zone-a"], "value": [3, 7]},
            geometry=[point(0, 0), point(1, 1)],
        ),
        frame.to_crs(3857),
        frame.assign(location_id=[1, 2]),
        frame.assign(location_id=["zone-z", "zone-z"]),
        frame.assign(location_id=["", "zone-a"]),
        frame.assign(geometry=gpd.GeoSeries([point(), point(1, 1)], crs=4326)),
        frame.assign(value=pd.Series([None, 7])),
        frame.assign(unexpected=1),
        frame.drop(columns="value"),
        frame.iloc[:1],
    ):
        producer["frame"] = invalid
        events.clear()
        try:
            runner.run("native_probe", inputs=inputs)
        except importlib.import_module(
            "lyra.runtime.execution"
        ).GraphExecutionError as error:
            require(
                isinstance(
                    error.__cause__,
                    (
                        modules["lyra.contracts.errors"].LocationAcquisitionError,
                        modules["lyra.contracts.errors"].LocationProviderContractError,
                    ),
                ),
                "native validation: expected an acquisition error",
            )
            require(
                "calculate" not in events and events[-1] == "close",
                "native validation: handler ran or resource did not close",
            )
        else:
            message = "native validation: accepted invalid producer frame"
            raise ContractMismatchError(message)


def unit_checks(modules: dict[str, ModuleType]) -> None:
    """Check the unit vocabulary and required, nullable column declarations.

    Raises:
        ContractMismatchError: An invalid or missing unit is accepted.
    """
    expected = {
        "mm",
        "m",
        "km",
        "m2",
        "km2",
        "ha",
        "degC",
        "K",
        "s",
        "day",
        "year",
        "calendar_year",
        "count",
        "count/km2",
        "count/ha",
        "count/1000inh",
        "ratio",
        "percent",
        "score",
        "dimensionless",
    }
    require(
        expected <= {item.value for item in modules["lyra.contracts.units"].Unit},
        "units: expected the documented canonical vocabulary",
    )
    pa = importlib.import_module("pandera.pandas")
    compiler = importlib.import_module("lyra.sdk.output_schema")
    for unit in [*sorted(expected), None]:
        _, metadata = compiler.compile_output(
            pa.DataFrameSchema(
                {
                    "value": pa.Column(
                        float, description="Synthetic value.", metadata={"unit": unit}
                    )
                }
            )
        )
        require(
            metadata.model_dump(mode="json")["columns"][0]["unit"] == unit,
            "units: expected canonical strings and explicit null in output",
        )
    for metadata in ({}, {"unit": "unrecognized_unit"}, {"unit": "M2"}):
        try:
            compiler.compile_output(
                pa.DataFrameSchema(
                    {"value": pa.Column(float, description="Value", metadata=metadata)}
                )
            )
        except ValueError:
            continue
        msg = "units: expected missing and noncanonical units to be rejected"
        raise ContractMismatchError(msg)


def authoring_checks(modules: dict[str, ModuleType], pydantic: ModuleType) -> None:
    """Exercise registration, parameters, description, and manifest creation.

    Raises:
        ContractMismatchError: Invalid parameters are accepted.
    """
    unit_checks(modules)
    graph_checks(modules, pydantic)
    parameters = pydantic.create_model(
        "CompatibilityParameters",
        __base__=modules["lyra.sdk"].MetricParameters,
        value=(int, pydantic.Field(description="Synthetic check value.")),
    )

    def handler(location: object, parameters: object) -> None:
        del location, parameters
        msg = "Declaration-only handler"
        raise RuntimeError(msg)

    handler.__annotations__ = {
        "location": importlib.import_module("geopandas").GeoDataFrame,
        "parameters": parameters,
        "return": type(None),
    }
    pa = importlib.import_module("pandera.pandas")
    location_schema = pa.DataFrameSchema(
        {
            "location_id": pa.Column(
                str,
                unique=True,
                checks=pa.Check(lambda values: values.str.len().gt(0), name="nonempty"),
                description="ID",
                metadata={"unit": None},
            ),
            "geometry": pa.Column(
                importlib.import_module("pandera.engines.geopandas_engine").Geometry(
                    crs="EPSG:4326"
                ),
                checks=pa.Check(
                    lambda values: values.is_valid & ~values.is_empty,
                    name="valid_geometry",
                ),
                description="Geometry",
                metadata={"unit": None},
            ),
        },
        strict=True,
    )

    def resolve_records(self: object, ids: list[str]) -> object:
        del self, ids
        msg = "Declaration checks must not acquire records"
        raise RuntimeError(msg)

    provider = type(
        "CheckProvider",
        (modules["lyra.sdk"].LocationProvider,),
        {
            "id": "check_records",
            "schema": location_schema,
            "origins": (),
            "resolve": resolve_records,
        },
    )
    decorated = modules["lyra.sdk"].metric(
        code_revision="1",
        inputs={
            "location": modules["lyra.sdk"].LocationDependency(provider="check_records")
        },
        name="compatibility_probe",
        description="Synthetic authoring check.",
        outputs={
            "compatibility_probe": pa.DataFrameSchema(
                {
                    "value": pa.Column(
                        int,
                        description="Synthetic check value.",
                        metadata={
                            "unit": modules["lyra.contracts.units"].Unit.DIMENSIONLESS
                        },
                    )
                }
            )
        },
    )(handler)
    plugin = modules["lyra.sdk"].PluginDefinition(
        metrics=[decorated], resources={"check_records": provider}
    )
    catalog = importlib.import_module(
        "lyra.runtime.planning.catalog"
    ).compile_live_catalog({"compatibility-probe": plugin})
    plugin = catalog.implementations["compatibility-probe"].definition
    implementation = plugin.implementation("compatibility_probe")
    require(
        isinstance(implementation, modules["lyra.sdk"].NodeImplementation)
        and implementation.function is decorated
        and implementation.parameters is parameters,
        "plugin: expected the registered handler and parameter model",
    )
    binding = modules["lyra.runtime.invocation"].BoundPlugin(plugin)
    prepared = binding.prepare_parameters("compatibility_probe", {"value": 2})
    require(prepared.value == 2, "parameters: expected integer value to be preserved")
    for payload in ({}, {"value": "invalid"}, {"value": 2, "extra": 1}):
        try:
            binding.prepare_parameters("compatibility_probe", payload)
        except modules["lyra.contracts.errors"].MetricInputError:
            continue
        msg = (
            "parameters: expected required fields and rejection of "
            "invalid or extra fields"
        )
        raise ContractMismatchError(msg)
    description = plugin.describe("compatibility_probe")
    require(
        set(description.manifest.location_inputs) == {"location"},
        "description: expected declared location input",
    )
    models = importlib.import_module("lyra.contracts.plugins")
    manifest = plugin.manifest(
        plugin=models.PluginInfo(name="compatibility-probe", version="0.0.0"),
        factory="compatibility_probe:create_plugin",
    ).model_dump(mode="json")
    require("nodes" in manifest, "manifest: expected node declarations")
    require(len(manifest["nodes"]) == 1, "manifest: expected one registered node")


def graph_checks(modules: dict[str, ModuleType], pydantic: ModuleType) -> None:
    """Check metrics, cross-plugin declarations and native ownership."""
    pandas = importlib.import_module("pandas")
    parameters = pydantic.create_model(
        "GraphParameters",
        __base__=modules["lyra.sdk"].MetricParameters,
        baseline_year=(int, pydantic.Field(default=2020, description="Baseline year.")),
    )
    pa = importlib.import_module("pandera.pandas")
    output = pa.DataFrameSchema(
        {"value": pa.Column(int, description="Value", metadata={"unit": None})}
    )

    def acquire(parameters: object, context: object) -> None:
        del parameters, context

    acquire.__annotations__ = {
        "parameters": parameters,
        "context": modules["lyra.sdk"].RunContext,
    }
    source = modules["lyra.sdk"].metric(
        code_revision="1",
        name="external_source",
        description="Source",
        outputs={"external_records": output},
    )(acquire)

    def consume(parameters: object, records: object) -> object:
        del parameters
        return records

    consume.__annotations__ = {"parameters": parameters, "records": pandas.DataFrame}
    consumer = modules["lyra.sdk"].metric(
        code_revision="1",
        name="probe_consumer",
        description="Consumer",
        inputs={
            "records": modules["lyra.sdk"].Dependency(
                "external_records",
                producer="external_source",
                parameters={
                    "baseline_year": modules["lyra.sdk"].Input(
                        "parameters.baseline_year"
                    )
                },
            )
        },
        outputs={"probe_result": output},
    )(consume)
    plugin = modules["lyra.sdk"].PluginDefinition(metrics=[source, consumer])
    models = importlib.import_module("lyra.contracts.plugins")
    manifest = plugin.manifest(
        plugin=models.PluginInfo(name="graph-probe", version="0.0.0"),
        factory="probe:create_plugin",
    )
    require(
        plugin.metric_names == ("external_source", "probe_consumer"),
        "graph: expected both registered metrics",
    )
    require(
        all(node.kind == "metric" for node in manifest.nodes),
        "manifest: expected unified metrics",
    )
    description = plugin.describe("probe_consumer")
    require(
        set(description.manifest.request_schema["properties"]) == {"parameters"},
        "graph: dependency inputs must be private",
    )
    require(
        "records" in description.manifest.inputs,
        "graph: expected dependency declaration",
    )
    binding = modules["lyra.runtime.invocation"].BoundPlugin(plugin)
    frame = pandas.DataFrame({"value": [1]}, index=[42])
    require(
        binding.normalize_result("probe_consumer", frame)["probe_result"].equals(
            frame.reset_index(drop=True)
        ),
        "graph: validated values must be preserved with normalized indexes",
    )
    require(
        hasattr(modules["lyra.sdk"], "BaseIO")
        and hasattr(modules["lyra.sdk"], "Resource"),
        "resources: expected managed I/O interfaces",
    )


def emit(message: str) -> None:
    """Write one concise diagnostic line."""
    compact = " ".join(message.split())
    if len(compact) > 500:
        compact = compact[:497] + "..."
    sys.stdout.write(compact + "\n")


def main() -> int:
    """Check the baseline and print bounded diagnostics.

    Returns:
        0 for pass, 1 for mismatch, or 2 for an unavailable check.
    """
    emit(f"Authoring checks: {CONTRACT_REVISION}")
    try:
        emit(f"Installed lyra: {metadata.version('lyra')}")
        modules = {
            name: importlib.import_module(name)
            for name in dict.fromkeys(REQUIRED_IMPORTS.values())
        }
        # The data extra requires this driver, but loads it only during GIS exports.
        importlib.import_module("pyogrio")
        pydantic = importlib.import_module("pydantic")
    except (
        ImportError,
        RuntimeError,
        OSError,
        AttributeError,
        ValueError,
        TypeError,
    ) as error:
        if isinstance(error, metadata.PackageNotFoundError):
            detail = "missing lyra distribution metadata"
        elif isinstance(error, ModuleNotFoundError) and error.name:
            detail = f"missing module {error.name}"
        else:
            detail = type(error).__name__
        emit(f"UNAVAILABLE: {detail}. Check the target lyra[runtime] environment.")
        return 2
    stage = "explicit imports"
    missing = [
        f"{module}.{name}"
        for name, module in REQUIRED_IMPORTS.items()
        if not hasattr(modules[module], name)
    ]
    try:
        require(not missing, f"public authoring imports missing: {', '.join(missing)}")
        stage = "native execution and producer validation"
        native_checks(modules)
        stage = "units, metric registration, parameters, and manifest"
        authoring_checks(modules, pydantic)
    except ContractMismatchError as error:
        emit(f"MISMATCH: {error}")
        return 1
    except (
        pydantic.ValidationError,
        modules["lyra.contracts.errors"].PluginDefinitionError,
    ) as error:
        emit(
            f"MISMATCH: {stage}: SDK rejected a baseline definition/input "
            f"({type(error).__name__})."
        )
        return 1
    except (
        ImportError,
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
        RuntimeError,
        OSError,
        ArithmeticError,
    ) as error:
        emit(f"UNAVAILABLE: {stage}: {type(error).__name__} interrupted the check.")
        return 2
    emit(
        "PASS: checked native execution, producer validation, and declarations; "
        "validate the actual adapter next."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
