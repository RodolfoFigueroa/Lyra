import asyncio

import pytest
from jsonschema import validate
from jsonschema.exceptions import ValidationError

from lyra_app.routes.data_types import list_data_types


def test_data_types_describe_only_id_selections() -> None:
    response = asyncio.run(list_data_types())
    assert set(response.model_dump()) == {"location_selection_schema"}
    schema = response.location_selection_schema
    validate(
        {"collection": "test:units", "unit_type": "parcel", "ids": ["opaque"]}, schema
    )
    for invalid in (
        {"data_type": "geojson", "value": {}},
        {"data_type": "met_zone_code", "value": "09.01"},
        {"collection": "test:units", "unit_type": "parcel", "ids": []},
        {
            "collection": "test:units",
            "unit_type": "parcel",
            "ids": ["opaque"],
            "geometry": {},
        },
    ):
        with pytest.raises(ValidationError):
            validate(invalid, schema)
