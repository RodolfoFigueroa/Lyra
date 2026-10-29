"""Geometry models used by spatial metric requests."""

from typing import Annotated, Any, Literal

from lyra.sdk.models.strict import StrictBaseModel
from pydantic import Field


class CRSProperties(StrictBaseModel):
    """Coordinate reference system properties."""

    name: str = Field(min_length=1, description="CRS identifier such as EPSG:4326.")


class CRS(StrictBaseModel):
    """GeoJSON coordinate reference system object."""

    type: Literal["name"] = Field(description="CRS object type.")
    properties: CRSProperties = Field(description="CRS properties.")


Position = Annotated[list[float], Field(min_length=2)]
LineCoordinates = Annotated[list[Position], Field(min_length=2)]


class PointGeometry(StrictBaseModel):
    """GeoJSON point geometry."""

    type: Literal["Point"] = Field(description="GeoJSON geometry type.")
    coordinates: Position = Field(description="Point coordinate pair.")


class MultiPointGeometry(StrictBaseModel):
    """GeoJSON multi-point geometry."""

    type: Literal["MultiPoint"] = Field(description="GeoJSON geometry type.")
    coordinates: list[Position] = Field(description="Point coordinate pairs.")


class LineStringGeometry(StrictBaseModel):
    """GeoJSON line-string geometry."""

    type: Literal["LineString"] = Field(description="GeoJSON geometry type.")
    coordinates: LineCoordinates = Field(description="Line coordinate pairs.")


class MultiLineStringGeometry(StrictBaseModel):
    """GeoJSON multi-line-string geometry."""

    type: Literal["MultiLineString"] = Field(description="GeoJSON geometry type.")
    coordinates: list[LineCoordinates] = Field(
        description="Lines and their coordinate pairs.",
    )


class PolygonGeometry(StrictBaseModel):
    """GeoJSON polygon geometry."""

    type: Literal["Polygon"] = Field(description="GeoJSON geometry type.")
    coordinates: list[list[list[float]]] = Field(
        description="Polygon rings and coordinate pairs.",
    )


class MultiPolygonGeometry(StrictBaseModel):
    """GeoJSON multi-polygon geometry."""

    type: Literal["MultiPolygon"] = Field(description="GeoJSON geometry type.")
    coordinates: list[list[list[list[float]]]] = Field(
        description="Multi-polygon rings and coordinate pairs.",
    )


class Feature(StrictBaseModel):
    """GeoJSON feature containing point, line, or polygon geometry."""

    id: str = Field(min_length=1, description="Stable feature identifier.")
    type: Literal["Feature"] = Field(description="GeoJSON feature type.")
    geometry: (
        PointGeometry
        | MultiPointGeometry
        | LineStringGeometry
        | MultiLineStringGeometry
        | PolygonGeometry
        | MultiPolygonGeometry
    ) = Field(
        discriminator="type",
        description="Feature geometry.",
    )
    properties: dict[str, Any] = Field(description="Feature properties.")


class FeatureNoMultiPolygon(StrictBaseModel):
    """GeoJSON feature containing polygon geometry."""

    id: str = Field(min_length=1, description="Stable feature identifier.")
    type: Literal["Feature"] = Field(description="GeoJSON feature type.")
    geometry: PolygonGeometry = Field(description="Feature geometry.")
    properties: dict[str, Any] = Field(description="Feature properties.")


class GeoJSON(StrictBaseModel):
    """GeoJSON FeatureCollection accepted by explicit location inputs."""

    type: Literal["FeatureCollection"] = Field(description="GeoJSON collection type.")
    features: list[Feature] = Field(
        min_length=1,
        description="One or more point, line, or polygon GeoJSON features.",
    )
    crs: CRS = Field(description="Coordinate reference system for all features.")


class SingleGeoJSON(StrictBaseModel):
    """GeoJSON FeatureCollection constrained to one polygon feature."""

    type: Literal["FeatureCollection"] = Field(description="GeoJSON collection type.")
    features: list[FeatureNoMultiPolygon] = Field(
        min_length=1,
        max_length=1,
        description="Exactly one polygon feature.",
    )
    crs: CRS = Field(description="Coordinate reference system for the feature.")
