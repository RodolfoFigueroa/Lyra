import geopandas as gpd
import pandas as pd
import pandera.pandas as pandera
from shapely.geometry import Point

from test_support.location_helpers import output_schema


def test_location_build_and_revalidation_check_once() -> None:
    calls = []

    def check(frame: pd.DataFrame) -> bool:
        calls.append(len(frame))
        return True

    schema = output_schema(value="Int64")
    schema.schema.checks = [pandera.Check(check, name="count_checks")]
    frame = gpd.GeoDataFrame(
        {"id": ["a"], "value": pd.Series([2], dtype="Int64")},
        geometry=[Point(1, 2)],
        crs=4326,
    )
    value = schema.prepare_frame(frame)
    assert calls == [1]
    schema.prepare_frame(value)
    assert calls == [1, 1]
