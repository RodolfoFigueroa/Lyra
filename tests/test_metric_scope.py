from typing import cast

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from pandera.engines.geopandas_engine import Geometry
from pydantic import TypeAdapter, ValidationError
from shapely.geometry import Point

from lyra.contracts.dependencies import Dependency, LocationDependency
from lyra.contracts.errors import MetricResultError, PluginDefinitionError
from lyra.contracts.metric_scope import CachePolicy
from lyra.runtime.planning.native_bindings import bind_native_definitions
from lyra.sdk.plugin import PluginDefinition, metric
from lyra.sdk.results import normalize_native_result
from test_support.location_schema import column
from tests.metric_scope_helpers import identity_schema
from tests.node_declaration_helpers import Tracts


@pytest.mark.parametrize("count", [0, 1, 2])
@pytest.mark.parametrize("cache", ["off", "full"])
def test_unmarked_inputs_are_unpartitioned(count: int, cache: CachePolicy) -> None:
    def no_inputs() -> None:
        pass

    def one_input(main: gpd.GeoDataFrame) -> None:
        pass

    def two_inputs(main: gpd.GeoDataFrame, reference: gpd.GeoDataFrame) -> None:
        pass

    inputs = {
        name: LocationDependency(provider="tracts")
        for name in ("main", "reference")[:count]
    }
    handler = metric(
        name="summary",
        code_revision="1",
        description="Summary",
        inputs=inputs,
        outputs={"data": pa.DataFrameSchema({"value": column(int)}, strict=True)},
        cache=cache,
    )((no_inputs, one_input, two_inputs)[count])
    plugin = PluginDefinition(metrics=[handler], resources={"tracts": Tracts})
    definition = plugin.declarations[0]
    assert definition.scope == "unpartitioned"
    assert definition.primary_input is None
    manifest = plugin.describe("summary").manifest
    assert manifest.kind == "metric"
    assert manifest.scope == "unpartitioned"
    assert manifest.primary_input is None
    assert manifest.cache == cache


@pytest.mark.parametrize("cache", ["off", "full", "per_location"])
def test_primary_governs_every_output_and_survives_binding(cache: CachePolicy) -> None:
    def findings(main: gpd.GeoDataFrame, reference: gpd.GeoDataFrame) -> None:
        pass

    schema = identity_schema()
    metric(
        name="findings",
        code_revision="1",
        description="Findings",
        inputs={
            "main": LocationDependency(provider="tracts", primary=True),
            "reference": LocationDependency(provider="tracts"),
        },
        outputs={"first": schema, "second": schema},
        cache=cache,
    )(findings)
    plugin = PluginDefinition(metrics=[findings], resources={"tracts": Tracts})
    bound = bind_native_definitions({"test": plugin})["test"]
    implementation = bound.implementation("findings")
    metadata = bound.describe("findings").model_dump(mode="json")["manifest"]
    assert metadata["scope"] == "location"
    assert metadata["primary_input"] == "main"
    assert metadata["cache"] == cache
    rows = pd.DataFrame(
        {"location_id": ["A", "A", "B"], "row_key": ["roof", "door", "roof"]}
    )
    result = normalize_native_result(
        "findings", implementation.schemas, {"first": rows, "second": rows}
    )
    for frame in result.values():
        pd.testing.assert_frame_equal(frame, rows)
    with pytest.raises(pa.errors.SchemaError):
        implementation.schemas["first"].validate(
            pd.concat([rows, rows], ignore_index=True)
        )
    schema.columns.clear()
    assert set(implementation.schemas["first"].columns) == {"location_id", "row_key"}


def test_multiple_primaries_and_ordinary_dependency_markers_fail() -> None:
    def two_inputs(main: gpd.GeoDataFrame, reference: gpd.GeoDataFrame) -> None:
        pass

    with pytest.raises(PluginDefinitionError, match="one primary"):
        metric(
            name="invalid",
            code_revision="1",
            description="Invalid",
            inputs={
                name: LocationDependency(provider="tracts", primary=True)
                for name in ("main", "reference")
            },
            outputs={"data": identity_schema()},
        )(two_inputs)
    with pytest.raises(ValidationError):
        TypeAdapter(Dependency).validate_python(
            {"producer": "producer", "output": "data", "primary": True}
        )


@pytest.mark.parametrize("provider", [True, False])
def test_nested_bindings_cannot_set_primary(*, provider: bool) -> None:
    override = (
        LocationDependency(provider="tracts", primary=True)
        if provider
        else LocationDependency(operation="select", primary=True)
    )
    with pytest.raises(ValueError, match="override primary"):
        Dependency("data", producer="producer").bind(main=override)
    with pytest.raises(ValueError, match="override primary"):
        LocationDependency(operation="select").bind(main=override)


@pytest.mark.parametrize(
    "cache", [True, False, None, 1, "FULL", "per-location", [], {}]
)
def test_malformed_cache_policies_fail(cache: object) -> None:
    def handler() -> None:
        pass

    with pytest.raises(PluginDefinitionError, match="cache must"):
        metric(
            name="bad",
            code_revision="1",
            description="Bad",
            outputs={"data": identity_schema()},
            cache=cast("CachePolicy", cache),
        )(handler)


def test_per_location_requires_primary_and_default_cache_is_off() -> None:
    def handler(main: gpd.GeoDataFrame) -> None:
        pass

    options = {"main": LocationDependency(provider="tracts")}
    with pytest.raises(PluginDefinitionError, match="Unpartitioned"):
        metric(
            name="bad",
            code_revision="1",
            description="Bad",
            inputs=options,
            outputs={"data": identity_schema()},
            cache="per_location",
        )(handler)
    options["main"] = LocationDependency(provider="tracts", primary=True)
    metric(
        name="valid",
        code_revision="1",
        description="Valid",
        inputs=options,
        outputs={"data": identity_schema()},
    )(handler)
    assert PluginDefinition(metrics=[handler]).declarations[0].cache == "off"


@pytest.mark.parametrize(
    "fault",
    [
        "missing_owner",
        "missing_key",
        "nullable_owner",
        "nullable_key",
        "numeric_owner",
        "numeric_key",
        "no_unique",
        "individual_unique",
        "extra_unique",
        "source",
        "geometry",
        "alias",
    ],
)
def test_weakened_structural_declarations_fail(fault: str) -> None:
    def handler(main: gpd.GeoDataFrame) -> None:
        pass

    schema = identity_schema()
    if fault.startswith("missing"):
        del schema.columns["location_id" if fault.endswith("owner") else "row_key"]
    elif fault.startswith("nullable"):
        schema.columns[
            "location_id" if fault.endswith("owner") else "row_key"
        ].nullable = True
    elif fault.startswith("numeric"):
        schema.columns[
            "location_id" if fault.endswith("owner") else "row_key"
        ].dtype = int
    elif fault == "no_unique":
        schema.unique = None
    elif fault == "individual_unique":
        schema.unique = None
        schema.columns["location_id"].unique = True
        schema.columns["row_key"].unique = True
    elif fault == "extra_unique":
        schema.columns["value"] = column(int)
        schema.unique = ["location_id", "row_key", "value"]
    elif fault == "source":
        schema.columns["source_location_id"] = column(str)
    elif fault == "geometry":
        schema.columns["geometry"] = column(str)
    else:
        schema = schema.rename_columns({"row_key": "alias"})
    with pytest.raises(PluginDefinitionError):
        metric(
            name="bad",
            code_revision="1",
            description="Bad",
            inputs={"main": LocationDependency(provider="tracts", primary=True)},
            outputs={"data": schema},
        )(handler)


@pytest.mark.parametrize(
    "reserved", ["location_id", "row_key", "source_location_id", "geometry"]
)
def test_unpartitioned_outputs_cannot_repurpose_structural_names(reserved: str) -> None:
    def handler() -> None:
        pass

    with pytest.raises(PluginDefinitionError, match="reserved"):
        metric(
            name="bad",
            code_revision="1",
            description="Bad",
            outputs={"data": pa.DataFrameSchema({reserved: column(str)}, strict=True)},
        )(handler)


def test_spatial_metric_dependencies_do_not_establish_ownership() -> None:
    def handler(main: gpd.GeoDataFrame) -> None:
        pass

    metric(
        name="spatial",
        code_revision="1",
        description="Spatial",
        inputs={"main": Dependency("data", producer="producer")},
        outputs={
            "data": pa.DataFrameSchema({"geometry": column(Geometry)}, strict=True)
        },
    )(handler)
    definition = PluginDefinition(metrics=[handler]).declarations[0]
    assert definition.scope == "unpartitioned"
    assert definition.primary_input is None
    assert definition.outputs["data"].geometry == "geometry"


def test_all_named_outputs_require_the_declared_identity() -> None:
    def handler(main: gpd.GeoDataFrame) -> None:
        pass

    with pytest.raises(PluginDefinitionError, match="location_id"):
        metric(
            name="mixed",
            code_revision="1",
            description="Mixed",
            inputs={"main": LocationDependency(provider="tracts", primary=True)},
            outputs={
                "owned": identity_schema(),
                "summary": pa.DataFrameSchema({"value": column(int)}, strict=True),
            },
        )(handler)


def test_captured_nested_bindings_are_revalidated() -> None:
    def handler(main: gpd.GeoDataFrame) -> None:
        pass

    dependency = Dependency("data", producer="producer")
    dependency.locations["main"] = LocationDependency(provider="tracts", primary=True)
    with pytest.raises(PluginDefinitionError, match="override primary"):
        metric(
            name="bad",
            code_revision="1",
            description="Bad",
            inputs={"main": dependency},
            outputs={"data": pa.DataFrameSchema(strict=True)},
        )(handler)


def test_spatial_owned_outputs_require_active_geometry() -> None:
    def handler(main: gpd.GeoDataFrame) -> None:
        pass

    schema = identity_schema()
    schema.columns["geometry"] = column(Geometry)
    metric(
        name="spatial",
        code_revision="1",
        description="Spatial",
        inputs={"main": LocationDependency(provider="tracts", primary=True)},
        outputs={"data": schema},
    )(handler)
    definition = PluginDefinition(metrics=[handler]).declarations[0]
    frame = gpd.GeoDataFrame(
        {"location_id": ["A"], "row_key": ["roof"], "geometry": [Point(1, 2)]},
        crs=4326,
    )
    result = normalize_native_result("spatial", dict(definition.schemas), frame)["data"]
    assert isinstance(result, gpd.GeoDataFrame)
    assert result.active_geometry_name == "geometry"
    assert result.crs == frame.crs
    with pytest.raises(MetricResultError):
        normalize_native_result(
            "spatial", dict(definition.schemas), frame.rename_geometry("shape")
        )
