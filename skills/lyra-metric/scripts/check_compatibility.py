"""Check the bundled authoring baseline using the target environment's public SDK."""

from __future__ import annotations

import importlib
import sys
from copy import deepcopy
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from types import ModuleType

CONTRACT_REVISION = "lyra-authoring"
REQUIRED_EXPORTS = (
    "LocationInput",
    "LocationSelection",
    "LocationProvider",
    "LocationCollection",
    "UnresolvedLocationIDsError",
    "LocationAcquisitionError",
    "MetricParameters",
    "PluginDefinition",
    "Unit",
    "Dependency",
    "Input",
    "RunContext",
    "MetricInputError",
    "MetricResultError",
    "PluginDefinitionError",
    "metric",
)


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


def spatial_checks(sdk: ModuleType, pydantic: ModuleType) -> None:
    """Verify public spatial schemas and representative validation behavior.

    Raises:
        ContractMismatchError: An invalid spatial input is accepted.
    """
    ring = [[0, 0], [1, 0], [1, 1], [0, 0]]
    multi = {"type": "MultiPolygon", "coordinates": [[ring]]}
    base: dict[str, Any] = {
        "type": "FeatureCollection",
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
    for name, expected, maximum in (
        (
            "LocationInput",
            {
                "Point",
                "MultiPoint",
                "LineString",
                "MultiLineString",
                "Polygon",
                "MultiPolygon",
            },
            None,
        ),
    ):
        model = getattr(sdk, name)
        schema = model.model_json_schema()
        features = resolve(schema["properties"]["features"], schema)
        observed = geometry_types(
            resolve(features["items"], schema)["properties"]["geometry"], schema
        )
        require(
            observed == expected,
            f"{name}: expected geometry types {sorted(expected)}, "
            f"observed {sorted(observed)}",
        )
        require(
            features.get("minItems") == 1 and features.get("maxItems") == maximum,
            f"{name}: expected feature count minimum 1, maximum {maximum}; "
            f"observed {features.get('minItems')}, {features.get('maxItems')}",
        )
        require("crs" in schema.get("required", []), f"{name}: expected required CRS")
        valid = [deepcopy(base)]
        if name == "LocationInput":
            mixed = deepcopy(base)
            mixed["features"].append(
                {"type": "Feature", "id": "zone-a", "properties": {}, "geometry": multi}
            )
            valid.append(mixed)
        for payload in valid:
            model.model_validate(payload)
        invalid = []
        empty = deepcopy(base)
        empty["features"] = []
        invalid.append(empty)
        missing_crs = deepcopy(base)
        del missing_crs["crs"]
        invalid.append(missing_crs)
        for payload in invalid:
            try:
                model.model_validate(payload)
            except pydantic.ValidationError:
                continue
            msg = f"{name}: accepted input outside documented spatial constraints"
            raise ContractMismatchError(msg)


def unit_checks(sdk: ModuleType) -> None:
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
        "ratio",
        "percent",
        "score",
        "dimensionless",
    }
    require(
        expected <= {item.value for item in sdk.Unit},
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


def authoring_checks(sdk: ModuleType, pydantic: ModuleType) -> None:
    """Exercise registration, parameters, description, and manifest creation.

    Raises:
        ContractMismatchError: Invalid parameters are accepted.
    """
    unit_checks(sdk)
    graph_checks(sdk, pydantic)
    parameters = pydantic.create_model(
        "CompatibilityParameters",
        __base__=sdk.MetricParameters,
        value=(int, pydantic.Field(description="Synthetic check value.")),
    )

    def handler(location: object, parameters: object) -> None:
        del location, parameters
        msg = "Compatibility checks must not execute metric calculations"
        raise RuntimeError(msg)

    handler.__annotations__ = {
        "location": sdk.LocationInput,
        "parameters": parameters,
        "return": type(None),
    }
    decorated = sdk.metric(
        name="compatibility_probe",
        description="Synthetic authoring check.",
        outputs={
            "compatibility_probe": importlib.import_module(
                "pandera.pandas"
            ).DataFrameSchema(
                {
                    "value": importlib.import_module("pandera.pandas").Column(
                        int,
                        description="Synthetic check value.",
                        metadata={"unit": sdk.Unit.DIMENSIONLESS},
                    )
                }
            )
        },
    )(handler)
    plugin = sdk.PluginDefinition(nodes=[decorated])
    prepared = plugin.prepare_parameters("compatibility_probe", {"value": 2})
    require(prepared.value == 2, "parameters: expected integer value to be preserved")
    for payload in ({}, {"value": "2"}, {"value": 2, "extra": 1}):
        try:
            plugin.prepare_parameters("compatibility_probe", payload)
        except sdk.MetricInputError:
            continue
        msg = (
            "parameters: expected strict required fields and rejection of extra fields"
        )
        raise ContractMismatchError(msg)
    description = plugin.describe("compatibility_probe")
    require(
        description.spatial_inputs == {"location": "location"},
        "description: expected declared location input",
    )
    models = importlib.import_module("lyra.sdk.models.plugin")
    manifest = plugin.manifest(
        plugin=models.PluginInfo(name="compatibility-probe", version="0.0.0"),
        factory="compatibility_probe:create_plugin",
    ).model_dump(mode="json")
    require("nodes" in manifest, "manifest: expected node declarations")
    require(len(manifest["nodes"]) == 1, "manifest: expected one registered node")


def graph_checks(sdk: ModuleType, pydantic: ModuleType) -> None:
    """Check metrics, cross-plugin declarations and native ownership."""
    pandas = importlib.import_module("pandas")
    parameters = pydantic.create_model(
        "GraphParameters",
        __base__=sdk.MetricParameters,
        baseline_year=(int, pydantic.Field(default=2020, description="Baseline year.")),
    )
    pa = importlib.import_module("pandera.pandas")
    output = pa.DataFrameSchema(
        {"value": pa.Column(int, description="Value", metadata={"unit": None})}
    )

    def acquire(context: object) -> None:
        del context

    acquire.__annotations__ = {"context": sdk.RunContext}
    source = sdk.metric(
        name="probe_source", description="Source", outputs={"probe_records": output}
    )(acquire)

    def consume(parameters: object, records: object) -> object:
        del parameters
        return records

    consume.__annotations__ = {"parameters": parameters, "records": pandas.DataFrame}
    consumer = sdk.metric(
        name="probe_consumer",
        description="Consumer",
        inputs={
            "records": sdk.Dependency(
                "external_records",
                arguments={
                    "parameters": {"year": sdk.Input("parameters.baseline_year")}
                },
            )
        },
        outputs={"probe_result": output},
    )(consume)
    plugin = sdk.PluginDefinition(nodes=[source, consumer])
    models = importlib.import_module("lyra.sdk.models.plugin")
    manifest = plugin.manifest(
        plugin=models.PluginInfo(name="graph-probe", version="0.0.0"),
        factory="probe:create_plugin",
    )
    require(
        plugin.metric_names == ("probe_consumer", "probe_source"),
        "graph: every metric must be public",
    )
    require(
        all("role" not in node.model_dump() for node in manifest.nodes),
        "manifest: expected unified metrics",
    )
    description = plugin.describe("probe_consumer")
    require(
        set(description.request_schema["properties"]) == {"parameters"},
        "graph: dependency inputs must be private",
    )
    require("records" in description.inputs, "graph: expected dependency declaration")
    frame = pandas.DataFrame({"value": [1]}, index=[42])
    require(
        plugin.normalize_result("probe_consumer", frame)["probe_result"].equals(frame),
        "graph: validated values and indices must be preserved",
    )
    require(
        hasattr(sdk, "BaseIO") and hasattr(sdk, "Resource"),
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
        emit(f"Installed lyra-sdk: {version('lyra-sdk')}")
        sdk = importlib.import_module("lyra.sdk")
        pydantic = importlib.import_module("pydantic")
    except (
        ImportError,
        PackageNotFoundError,
        RuntimeError,
        OSError,
        AttributeError,
        ValueError,
        TypeError,
    ) as error:
        emit(f"UNAVAILABLE: {type(error).__name__}; run in the target SDK environment.")
        return 2
    stage = "public exports"
    missing = [name for name in REQUIRED_EXPORTS if not hasattr(sdk, name)]
    try:
        require(not missing, f"public authoring exports missing: {', '.join(missing)}")
        stage = "spatial validation"
        spatial_checks(sdk, pydantic)
        stage = "units, metric registration, parameters, and manifest"
        authoring_checks(sdk, pydantic)
    except ContractMismatchError as error:
        emit(f"MISMATCH: {error}")
        return 1
    except (pydantic.ValidationError, sdk.PluginDefinitionError) as error:
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
