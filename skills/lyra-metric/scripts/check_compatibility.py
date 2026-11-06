"""Check the authoring baseline against the installed Lyra SDK and runtime."""

from __future__ import annotations

import importlib
import sys
from copy import deepcopy
from importlib import metadata
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from types import ModuleType

CONTRACT_REVISION = "lyra-authoring"
REQUIRED_IMPORTS = {
    "Locations": "lyra.data",
    "LocationSelection": "lyra.contracts.locations",
    "LocationProvider": "lyra.sdk",
    "LocationCollection": "lyra.contracts.providers",
    "UnresolvedLocationIDsError": "lyra.contracts.errors",
    "LocationAcquisitionError": "lyra.contracts.errors",
    "MetricParameters": "lyra.sdk",
    "LocalRunner": "lyra.runtime.local",
    "LocationDependency": "lyra.sdk",
    "LocationContext": "lyra.sdk",
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
    "LocationContract": "lyra.contracts.locations",
    "LocationOutputSchema": "lyra.sdk",
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


def resolve(node: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve local schema references.

    Returns:
        The referenced schema node.
    """
    while "$ref" in node:
        reference = node["$ref"]
        require(reference.startswith("#/"), "spatial schema: expected local references")
        node = schema
        for part in reference[2:].split("/"):
            node = node[part.replace("~1", "/").replace("~0", "~")]
    return node


def geometry_types(node: dict[str, Any], schema: dict[str, Any]) -> set[str]:
    """Collect geometry discriminator values.

    Returns:
        The supported geometry names.
    """
    node = resolve(node, schema)
    alternatives = node.get("anyOf", node.get("oneOf"))
    if alternatives is not None:
        return set().union(*(geometry_types(item, schema) for item in alternatives))
    discriminator = resolve(node["properties"]["type"], schema)
    if "const" in discriminator:
        return {discriminator["const"]}
    return set(discriminator["enum"])


def spatial_checks(modules: dict[str, ModuleType], pydantic: ModuleType) -> None:
    """Verify public spatial schemas and representative validation behavior.

    Raises:
        ContractMismatchError: An invalid spatial input is accepted.
    """
    require(
        hasattr(modules["lyra.sdk"].LocationOutputSchema, "build"),
        "LocationOutputSchema: frame builder required",
    )
    ring = [[0, 0], [1, 0], [1, 1], [0, 0]]
    multi = {"type": "MultiPolygon", "coordinates": [[ring]]}
    base: dict[str, Any] = {
        "collection": "example:places",
        "unit_type": "area",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": [
            {
                "type": "Feature",
                "id": "zone-z",
                "properties": {},
                "geometry": {"type": "Polygon", "coordinates": [ring]},
            }
        ],
    }
    model = importlib.import_module("lyra.data.location_payload").LocationPayload
    schema = model.model_json_schema()
    features = resolve(schema["properties"]["features"], schema)
    observed = geometry_types(
        resolve(features["items"], schema)["properties"]["geometry"], schema
    )
    expected = {
        "Point",
        "MultiPoint",
        "LineString",
        "MultiLineString",
        "Polygon",
        "MultiPolygon",
    }
    require(
        observed == expected,
        f"Locations: expected geometry types {sorted(expected)}, "
        f"observed {sorted(observed)}",
    )
    require(
        features.get("minItems", 0) == 0 and features.get("maxItems") is None,
        "Locations: expected zero or more features",
    )
    require("crs" in schema.get("required", []), "Locations: expected required CRS")
    model.model_validate(base)
    mixed = deepcopy(base)
    mixed["features"].append(
        {"type": "Feature", "id": "zone-a", "properties": {}, "geometry": multi}
    )
    model.model_validate(mixed)
    model.model_validate({**base, "features": []})
    del base["crs"]
    try:
        model.model_validate(base)
    except pydantic.ValidationError:
        return
    msg = "Locations: accepted features without a CRS"
    raise ContractMismatchError(msg)


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
        msg = "Compatibility checks must not execute metric calculations"
        raise RuntimeError(msg)

    handler.__annotations__ = {
        "location": modules["lyra.data"].Locations,
        "parameters": parameters,
        "return": type(None),
    }
    pa = importlib.import_module("pandera.pandas")
    location_schema = modules["lyra.sdk"].LocationOutputSchema(
        pa.DataFrameSchema(
            {
                "id": pa.Column(str, description="ID", metadata={"unit": None}),
                "geometry": pa.Column(
                    "geometry", description="Geometry", metadata={"unit": None}
                ),
            }
        )
    )
    contract = modules["lyra.contracts.locations"].LocationContract(
        family=location_schema.family("check:units", "unit"),
        source="probe",
        geographical_edition="current",
        meaning="Compatibility probe",
    )
    locations = location_schema.build_contract(
        location_schema.empty_frame("EPSG:4326"), contract
    )
    require(
        isinstance(locations, modules["lyra.data"].Locations)
        and locations.contract == contract.reference,
        "locations: expected native data bound to the declared contract",
    )
    decorated = modules["lyra.sdk"].metric(
        locations={"location": contract},
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
    plugin = modules["lyra.sdk"].PluginDefinition(metrics=[decorated])
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
    for payload in ({}, {"value": "2"}, {"value": 2, "extra": 1}):
        try:
            binding.prepare_parameters("compatibility_probe", payload)
        except modules["lyra.contracts.errors"].MetricInputError:
            continue
        msg = (
            "parameters: expected strict required fields and rejection of extra fields"
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

    def acquire(context: object) -> None:
        del context

    acquire.__annotations__ = {"context": modules["lyra.sdk"].RunContext}
    source = modules["lyra.sdk"].metric(
        name="probe_source", description="Source", outputs={"probe_records": output}
    )(acquire)

    def consume(parameters: object, records: object) -> object:
        del parameters
        return records

    consume.__annotations__ = {"parameters": parameters, "records": pandas.DataFrame}
    consumer = modules["lyra.sdk"].metric(
        name="probe_consumer",
        description="Consumer",
        inputs={
            "records": modules["lyra.sdk"].Dependency(
                "external_records",
                producer="external_source",
                parameters={
                    "year": modules["lyra.sdk"].Input("parameters.baseline_year")
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
        plugin.metric_names == ("probe_consumer", "probe_source"),
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
    emit(f"Authoring contract: {CONTRACT_REVISION}")
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
        stage = "spatial validation"
        spatial_checks(modules, pydantic)
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
        "PASS: checked spatial and authoring baseline; "
        "validate the actual adapter next."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
