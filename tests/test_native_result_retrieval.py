"""Client tables preserve captured native contracts and independent ownership."""

from pathlib import Path

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from geopandas.testing import assert_geodataframe_equal
from pandera.engines.geopandas_engine import Geometry
from shapely.geometry import Point

# Exercise client decoding independently of HTTP transport.
from lyra.api.client._tables import decode_tables  # ruff: ignore[import-private-name]
from lyra.api.exceptions import DownloadError
from lyra.contracts.jobs import TableJobResult
from lyra.data.results import serialize_outputs
from lyra.runtime.artifacts import ArtifactStore
from lyra.sdk.output_schema import compile_output


@pytest.mark.parametrize("state", ["populated", "empty", "all-null"])
@pytest.mark.parametrize("spatial", [False, True])
def test_declared_types_and_independent_reads(
    tmp_path: Path, state: str, *, spatial: bool
) -> None:
    values = {
        "count": pd.Series([2**60 + 1, None], dtype="Int64"),
        "ratio": pd.Series([1.5, None], dtype="Float64"),
        "flag": pd.Series([True, None], dtype="boolean"),
        "label": pd.Series(["first", None], dtype="string"),
    }
    frame = pd.DataFrame(values)
    if state == "all-null":
        frame.iloc[:] = pd.NA
    if spatial:
        frame = gpd.GeoDataFrame(
            frame,
            geometry=gpd.GeoSeries([Point(1000, 2000), Point(3000, 4000)]),
            crs=3857,
        )
    if state == "empty":
        frame = frame.iloc[:0]
    schema = pa.DataFrameSchema(
        {
            name: pa.Column(
                Geometry(crs=3857) if name == "geometry" else series.dtype,
                nullable=True,
                description=str(name),
                checks=pa.Check(
                    lambda values: values.dropna().is_valid.all(), name="valid_geometry"
                )
                if name == "geometry"
                else None,
                metadata={"unit": None},
            )
            for name, series in frame.items()
        },
        strict=True,
    )
    _, description = compile_output(schema)
    result = TableJobResult.model_validate(
        {
            "job_id": "native",
            "outputs": serialize_outputs("native", {"rows": frame}),
            "output_metadata": {"rows": description},
        }
    )
    store = ArtifactStore(tmp_path)
    key = store.write({"rows": frame}, {"rows": schema})
    original = result.model_dump_json()
    for tables in [decode_tables(result), store.read(key)]:
        restored = tables["rows"]
        assert_retrieved_frame(restored, frame)
        pd.testing.assert_series_equal(
            restored["count"], frame["count"], check_exact=True
        )
        restored.attrs["scope"] = {"provider": "forged"}
        if len(restored):
            restored.loc[0, "count"] = 99
        del restored["flag"]
    assert_retrieved_frame(decode_tables(result)["rows"], frame)
    pd.testing.assert_frame_equal(store.read(key)["rows"], frame)
    assert result.model_dump_json() == original
    descriptions = store.descriptions(key)
    descriptions["rows"].table_schema.columns.clear()
    assert store.descriptions(key)["rows"].table_schema == description


def test_malformed_declared_columns_raise_useful_error() -> None:
    schema = pa.DataFrameSchema(
        {"value": pa.Column(int, description="Value", metadata={"unit": None})},
        strict=True,
    )
    result = TableJobResult(
        job_id="invalid",
        outputs={"rows": [{"unexpected": 1}]},
        output_metadata={"rows": compile_output(schema)[1]},
    )
    with pytest.raises(DownloadError, match="invalid output 'rows'"):
        decode_tables(result)


def assert_retrieved_frame(actual: pd.DataFrame, expected: pd.DataFrame) -> None:
    """Allow only the coordinate rounding from GeoJSON CRS reprojection."""
    if isinstance(expected, gpd.GeoDataFrame):
        assert isinstance(actual, gpd.GeoDataFrame)
        assert_geodataframe_equal(actual, expected, check_less_precise=True)
        pd.testing.assert_frame_equal(
            actual.drop(columns="geometry"),
            expected.drop(columns="geometry"),
            check_exact=True,
        )
    else:
        pd.testing.assert_frame_equal(actual, expected, check_exact=True)
