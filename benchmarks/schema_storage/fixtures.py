"""Contracts independent of observed runtime output dtypes."""

import geopandas as gpd
import pandas as pd
import pandera.pandas as pandera
import pyarrow as pa
import shapely
from pandera.engines.geopandas_engine import Geometry


def declarations(*, spatial: bool = False) -> pandera.DataFrameSchema:
    """Return the explicit nullable acceptance contract."""
    types = {
        "small": pd.Int8Dtype(),
        "signed": pd.Int64Dtype(),
        "unsigned": pd.UInt64Dtype(),
        "single": pd.Float32Dtype(),
        "double": pd.Float64Dtype(),
        "flag": pd.BooleanDtype(),
        "text": pd.StringDtype(),
        "time": pd.DatetimeTZDtype(unit="us", tz="UTC"),
        "date": pd.ArrowDtype(pa.date32()),
        "category": pd.CategoricalDtype(["a", "b", "unused"], ordered=True),
    }
    columns = {
        name: pandera.Column(dtype, nullable=True) for name, dtype in types.items()
    }
    if spatial:
        columns["geometry"] = pandera.Column(Geometry)
    return pandera.DataFrameSchema(columns, strict=True)


def frame(state: str = "populated", *, spatial: bool = False) -> pd.DataFrame:
    """Build populated, empty, all-null, shuffled, or incidental-index inputs.

    Returns:
        An independent native frame with declaration-derived dtypes.
    """
    values = {
        "small": [-128, 127, None],
        "signed": [-(2**63), 2**63 - 1, None],
        "unsigned": [0, 2**64 - 1, None],
        "single": [-0.0, 0.0, None],
        "double": [-0.0, 1.7976931348623157e308, None],
        "flag": [True, False, None],
        "text": ["a", "b", None],
        "time": ["2024-01-01T00:00:00.123456Z", "2024-01-02", None],
        "date": [pd.Timestamp("2024-01-01").date(), None, None],
        "category": ["a", "b", None],
    }
    schema = declarations()
    data = {
        name: pd.Series(
            [] if state == "empty" else [None] * 3 if state == "all-null" else items,
            dtype=column.dtype.type,
        )
        for (name, column), items in zip(
            schema.columns.items(), values.values(), strict=True
        )
    }
    result = pd.DataFrame(data)
    if spatial:
        result = gpd.GeoDataFrame(
            result,
            geometry=shapely.points(range(len(result)), range(len(result))),
            crs=4326,
        )
    if state == "shuffled":
        result = result[list(reversed(result.columns))]
    if state == "indexed":
        result.index = pd.Index(["third", "first", "second"], name="incidental")
    return result


def named_outputs() -> dict[str, pd.DataFrame]:
    """Return ordinary and spatial named metric outputs."""
    return {"summary": frame(), "places": frame(spatial=True)}
