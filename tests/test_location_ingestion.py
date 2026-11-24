"""Producer validation, deferred materialization, and exact geometry ingestion."""

import geopandas as gpd
import pandera.pandas as pandera
import pytest
import shapely

from lyra.contracts.errors import MetricResultError
from test_support.location_helpers import (
    output_schema,
)


def source() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"id": ["b", "a"], "count": [1, 2]},
        geometry=shapely.points([1, 2], [3, 4]),
        crs=4326,
    )


def test_lazy_schema_reports_multiple_failures() -> None:
    schema = output_schema(count=int)
    schema.schema.columns["count"].checks = [pandera.Check.gt(10), pandera.Check.lt(0)]
    with pytest.raises(MetricResultError) as caught:
        schema.prepare_frame(source(), lazy=True)
    assert len(caught.value.failures) == 2


def test_missing_geometry_is_rejected() -> None:
    result = source()
    result.loc[0, "geometry"] = None
    with pytest.raises(ValueError, match="geometry"):
        output_schema(count=int).prepare_frame(result)
