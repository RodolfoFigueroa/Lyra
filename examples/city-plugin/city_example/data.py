"""Read the synthetic city shipped with this package."""

from importlib.resources import files

import geopandas as gpd
import pandas as pd
from pydantic import BaseModel
from shapely.geometry import LineString, box


class Street(BaseModel):
    """One synthetic street observation in projected metres."""

    id: str
    coordinates: list[tuple[float, float]]
    condition_score: float


class Block(BaseModel):
    """The rectangular bounds of one example block."""

    id: str
    bounds: tuple[float, float, float, float]


class Membership(BaseModel):
    """The share of a street allocated to a block."""

    block_id: str
    street_id: str
    weight: float


class CityData(BaseModel):
    """Bundled geometry, condition observations, and explicit allocation weights."""

    revision: str
    crs: str
    streets: list[Street]
    blocks: list[Block]
    membership: list[Membership]


def load_city() -> CityData:
    """Read package data independently of the working directory.

    Returns:
        A fresh validated copy of the synthetic dataset.
    """
    source = files("city_example").joinpath("data/city.json")
    return CityData.model_validate_json(source.read_text(encoding="utf-8"))


def street_frame(city: CityData) -> gpd.GeoDataFrame:
    """Return all street geometry and observed conditions.

    Returns:
        A projected frame whose score scale runs from zero to one hundred.
    """
    return gpd.GeoDataFrame(
        {
            "id": pd.Series([street.id for street in city.streets], dtype="str"),
            "condition_score": pd.Series(
                [street.condition_score for street in city.streets], dtype="float64"
            ),
        },
        geometry=[LineString(street.coordinates) for street in city.streets],
        crs=city.crs,
    )


def block_frame(city: CityData) -> gpd.GeoDataFrame:
    """Return the block geometry, including a block with no member streets.

    Returns:
        A projected frame with explicit string identifiers.
    """
    return gpd.GeoDataFrame(
        {"id": pd.Series([block.id for block in city.blocks], dtype="str")},
        geometry=[box(*block.bounds) for block in city.blocks],
        crs=city.crs,
    )
