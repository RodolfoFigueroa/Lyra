import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from pydantic import ValidationError
from shapely.geometry import Point

from lyra.contracts.errors import MetricInputError, PluginDefinitionError
from lyra.contracts.plugins import PluginInfo, PluginManifest
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.native_inputs import NativeLocationInput
from lyra.runtime.planning.provider_registry import capture_provider
from lyra.sdk.dependencies import LocationDependency
from lyra.sdk.location_schema import LocationOutputSchema
from lyra.sdk.plugin import PluginDefinition, location_operation, metric
from test_support.runtime_inputs import native_inputs
from tests.provider_helpers import FakeProvider


def test_named_concrete_inputs_and_operation_output() -> None:
    class OtherProvider(FakeProvider):
        id = "other_records"

    first = capture_provider("first", FakeProvider).contract
    second = capture_provider("second", OtherProvider).contract
    schema = LocationOutputSchema(FakeProvider.schema, id_column="location_id")

    @metric(
        code_revision="1",
        name="compare",
        description="Compare population observations.",
        outputs={
            "comparison": pa.DataFrameSchema(
                {},
                strict=True,
                metadata={
                    "meaning": "Population difference",
                    "granularity": "One row per unit",
                },
            )
        },
        inputs={
            "baseline": LocationDependency(provider=FakeProvider.id),
            "current": LocationDependency(provider=OtherProvider.id),
        },
    )
    def compare(baseline: gpd.GeoDataFrame, current: gpd.GeoDataFrame) -> pd.DataFrame:
        assert (
            set(baseline.columns)
            == set(current.columns)
            == {"location_id", "geometry", "label"}
        )
        return pd.DataFrame()

    @location_operation(
        code_revision="1",
        name="convert",
        description="Select another observation.",
        inputs={"source": LocationDependency(provider=FakeProvider.id)},
        target_provider=OtherProvider.id,
    )
    def convert(source: gpd.GeoDataFrame) -> object:
        return dict.fromkeys(source.location_id, ("001",))

    plugin = PluginDefinition(
        metrics=[compare],
        location_operations=[convert],
        resources={"first": FakeProvider, "second": OtherProvider},
    )
    binding = BoundPlugin(plugin)
    frame = gpd.GeoDataFrame(
        {"location_id": ["001"], "label": ["Place"]},
        geometry=[Point(1, 2)],
        crs="EPSG:27700",
    )
    value = NativeLocationInput(schema.prepare_frame(frame), first.reference)
    other = NativeLocationInput(schema.prepare_frame(frame), second.reference)
    prepared = binding.prepare_resolved_invocation(
        "compare", native_inputs({"baseline": value, "current": other})
    )
    assert prepared.locations["baseline"].contract == first.reference
    prepare = binding.prepare_resolved_invocation
    with pytest.raises(MetricInputError, match="execution-owned native records"):
        prepare(
            "compare",
            {"baseline": frame, "current": other},
        )
    with pytest.raises(MetricInputError, match="concrete contract"):
        prepare("compare", native_inputs({"baseline": other, "current": other}))
    invocation = binding.prepare_resolved_invocation(
        "convert", native_inputs({"source": value})
    )
    assert binding.invoke_operation(invocation) == {"001": ("001",)}
    declaration = binding.node_manifest("convert")
    assert declaration is not None
    assert declaration.kind == "location_operation"
    assert declaration.output_contract.reference == second.reference
    manifest = plugin.manifest(
        plugin=PluginInfo(name="test", version="1"), factory="test:create"
    )
    restored = PluginManifest.model_validate_json(manifest.model_dump_json())
    assert restored == manifest
    for index, field in [(0, "location_inputs"), (1, "output_contract")]:
        payload = manifest.model_dump()
        if index == 0:
            del payload["nodes"][index][field]["baseline"]["contract"]
        else:
            del payload["nodes"][index][field]
        with pytest.raises(ValidationError, match="contract"):
            PluginManifest.model_validate(payload)
    assert (
        restored.nodes[0].location_inputs["baseline"].contract.field_definitions
        == first.field_definitions
    )

    assert restored.nodes[0].kind == "metric"
    assert restored.nodes[0].outputs["comparison"].meaning == "Population difference"
    assert restored.nodes[0].kind == "metric"
    assert restored.nodes[0].outputs["comparison"].granularity == "One row per unit"


def test_declarations_reject_incomplete_inputs_and_additional_operation_source() -> (
    None
):
    def handler(left: gpd.GeoDataFrame, right: gpd.GeoDataFrame) -> pd.DataFrame:
        del left, right
        return pd.DataFrame()

    with pytest.raises(PluginDefinitionError, match="unsupported name"):
        metric(
            code_revision="1",
            name="incomplete",
            description="Incomplete inputs.",
            outputs={"result": pa.DataFrameSchema({}, strict=True)},
            inputs={"left": LocationDependency(provider=FakeProvider.id)},
        )(handler)
    with pytest.raises(PluginDefinitionError, match="one provider source"):
        location_operation(
            code_revision="1",
            name="invalid_target",
            description="Two sources.",
            inputs={
                "left": LocationDependency(provider=FakeProvider.id),
                "right": LocationDependency(provider=FakeProvider.id),
            },
            target_provider=FakeProvider.id,
        )(handler)


def test_registered_provider_is_input_schema_authority() -> None:
    @metric(
        code_revision="1",
        name="consume",
        description="Use all registered fields.",
        outputs={"result": pa.DataFrameSchema({}, strict=True)},
        inputs={"subject": LocationDependency(provider=FakeProvider.id)},
    )
    def consume(subject: gpd.GeoDataFrame) -> pd.DataFrame:
        return pd.DataFrame(index=subject.index)

    plugin = PluginDefinition(metrics=[consume], resources={"places": FakeProvider})
    location = plugin.node_manifests[0].location_inputs["subject"].contract
    assert location == capture_provider("places", FakeProvider).contract
    assert {column.name for column in location.family.structure.output.columns} == {
        "location_id",
        "geometry",
        "label",
    }
