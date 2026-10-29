from typing import Any
from unittest.mock import create_autospec

import pandas as pd
import pandera.pandas as pa
import pytest
from jsonschema import Draft202012Validator
from lyra.sdk import (
    Locations,
    PluginDefinition,
    PluginDefinitionError,
    RunContext,
    metric,
)
from lyra.sdk.models.geometry import GeoJSON, PointGeometry, SingleGeoJSON
from lyra.sdk.models.job import JobEnvelope
from pydantic import ValidationError

from tests.location_helpers import scoped_payload


def _collection(*geometry_types: str) -> dict[str, Any]:
    ring = [[0, 0], [1, 0], [1, 1], [0, 0]]
    coordinates = {"Point": [0, 0], "Polygon": [ring], "MultiPolygon": [[ring]]}
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": str(index),
                "geometry": {"type": kind, "coordinates": coordinates[kind]},
                "properties": {"label": "retained"},
            }
            for index, kind in enumerate(geometry_types)
        ],
        "crs": {"type": "name", "properties": {"name": "EPSG:6372"}},
    }


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
    payload = _collection(*types)
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
        name="point_contract",
        description="Check point inputs.",
        outputs={"point_contract": pa.DataFrameSchema({})},
    )
    def handler(location: Locations) -> pd.DataFrame:
        observed.append(location)
        return pd.DataFrame(index=[feature.id for feature in location.features])

    plugin = PluginDefinition(metrics=[handler])
    payload = _collection("Point")
    job = JobEnvelope(
        job_id="point-job",
        metric="point_contract",
        input={"location": scoped_payload(payload)},
    )
    plugin(job, create_autospec(RunContext, instance=True))
    assert len(observed) == 1
    assert isinstance(observed[0], Locations)
    assert observed[0].model_dump() == scoped_payload(payload)
