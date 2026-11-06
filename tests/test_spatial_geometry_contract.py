import pandas as pd
import pandera.pandas as pa
import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from lyra.contracts.errors import PluginDefinitionError
from lyra.contracts.geometry import GeoJSON, PointGeometry, SingleGeoJSON
from lyra.data.locations import Locations
from lyra.runtime.local import LocalRunner
from lyra.sdk.plugin import PluginDefinition, metric
from test_support.location_helpers import (
    fixture_locations,
    node_contract,
    output_schema,
    scoped_payload,
)
from tests.geometry_fixtures import projected_collection


@pytest.mark.parametrize(
    ("model", "types", "valid"),
    [
        (GeoJSON, ("Polygon",), True),
        (GeoJSON, ("MultiPolygon",), True),
        (GeoJSON, ("Polygon", "MultiPolygon"), True),
        (GeoJSON, ("Point",), True),
        (GeoJSON, ("Polygon", "Point"), True),
        (GeoJSON, (), False),
        (SingleGeoJSON, ("Polygon",), True),
        (SingleGeoJSON, ("Point",), False),
        (SingleGeoJSON, ("MultiPolygon",), False),
        (SingleGeoJSON, ("Polygon", "Polygon"), False),
        (SingleGeoJSON, (), False),
    ],
)
def test_spatial_model_and_schema_agree(
    model: type[GeoJSON] | type[SingleGeoJSON], types: tuple[str, ...], *, valid: bool
) -> None:
    payload = projected_collection(*types)
    assert Draft202012Validator(model.model_json_schema()).is_valid(payload) is valid
    if valid:
        assert model.model_validate(payload).model_dump() == payload
    else:
        with pytest.raises(ValidationError):
            model.model_validate(payload)


def test_standalone_point_geometry_remains_importable() -> None:
    assert PointGeometry(type="Point", coordinates=[0, 0]).type == "Point"


def test_special_bounds_annotation_is_rejected() -> None:
    with pytest.raises(PluginDefinitionError, match="bounds: unsupported"):

        @metric(
            name="bad_bounds",
            description="Invalid bounds.",
            outputs={"bad_bounds": pa.DataFrameSchema({})},
        )
        def handler(bounds: SingleGeoJSON) -> None:
            pass


def test_sdk_passes_resolved_points_to_location_handler() -> None:
    observed: list[Locations] = []

    @metric(
        locations={"location": node_contract()},
        name="point_contract",
        description="Check point inputs.",
        outputs={"point_contract": pa.DataFrameSchema({})},
    )
    def handler(location: Locations) -> pd.DataFrame:
        observed.append(location)
        return pd.DataFrame(index=list(location.ids))

    plugin = PluginDefinition(metrics=[handler])
    payload = projected_collection("Point")
    inputs = {
        "location": fixture_locations(
            scoped_payload(payload), schema=output_schema(label=str)
        )
    }
    LocalRunner.from_definitions({"test": plugin}).run_development(
        "point_contract", inputs
    )
    assert len(observed) == 1
    assert isinstance(observed[0], Locations)
    assert observed[0].contract == node_contract().reference
    assert observed[0].with_binding(None, None).to_dict() == scoped_payload(payload)
