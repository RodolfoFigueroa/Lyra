"""Scoped location fixtures for handler and execution tests."""

from typing import TypeVar

import geopandas as gpd
import pandas as pd
import pandera.pandas as pandera
import pyarrow as pa
from pandera.engines.geopandas_engine import Geometry
from shapely.geometry import shape
from typing_extensions import override

from lyra.contracts.locations import LocationContract
from lyra.contracts.resources import IOConfig
from lyra.runtime.native_inputs import NativeLocationInput
from lyra.sdk.location_schema import LocationOutputSchema
from lyra.sdk.locations import LocationProvider
from lyra.sdk.output_schema import OutputSchema
from lyra.sdk.plugin import PluginDefinition
from test_support.native_providers import native_schema

T = TypeVar("T")


def scoped_payload(value: T) -> T | dict:
    """Give geometry fixtures a default test scope."""
    if not isinstance(value, dict):
        return value
    payload = dict(value)
    payload.pop("type", None)
    return {"collection": "test:units", "unit_type": "lot", **payload}


def output_schema(
    *, crs: str = "EPSG:4326", **properties: object
) -> LocationOutputSchema:
    """Build a strict location schema for test provider properties."""
    return LocationOutputSchema(
        pandera.DataFrameSchema(
            {
                name: pandera.Column(
                    dtype,
                    description=name,
                    metadata={"unit": None},
                    checks=pandera.Check(
                        lambda s: s.geom_type.notna(), name="present_geometry"
                    )
                    if name == "geometry"
                    else None,
                )
                for name, dtype in {
                    "id": str,
                    "geometry": Geometry(crs=crs),
                    **properties,
                }.items()
            },
            strict=True,
        )
    )


def producer_result(
    value: gpd.GeoDataFrame | NativeLocationInput,
    schema: LocationOutputSchema | OutputSchema | None = None,
) -> gpd.GeoDataFrame:
    """Export an existing test fixture as a native producer frame."""
    contract = (
        schema
        if isinstance(schema, LocationOutputSchema)
        else LocationOutputSchema(schema)
        if schema is not None
        else output_schema()
    )
    frame = (
        value.frame.copy(deep=True)
        if isinstance(value, NativeLocationInput)
        else value.copy(deep=True)
    )
    for name, column in contract.schema.columns.items():
        if name == contract.geometry_column:
            continue
        if name not in frame and frame.empty:
            frame[name] = pd.Series([], dtype=object)
        if name in frame and not (column.coerce or contract.schema.coerce):
            frame[name] = column.dtype.coerce(frame[name])
    frame = frame[
        [name for name in contract.schema.columns if name in frame]
        + [name for name in frame if name not in contract.schema.columns]
    ]
    return frame


def stored_locations(value: dict) -> gpd.GeoDataFrame:
    """Build typed native fixtures under an explicit Pandera declaration."""
    rows = [
        row.model_dump() if hasattr(row, "model_dump") else row
        for row in value["features"]
    ]
    keys = list(rows[0]["properties"]) if rows else []
    if any(set(row["properties"]) != set(keys) for row in rows):
        message = "missing or unexpected properties"
        raise ValueError(message)
    data = {
        "id": pd.Series(
            [row["id"] for row in rows],
            dtype="str" if all(type(row["id"]) is str for row in rows) else object,
        )
    }
    for key in keys:
        values = [row["properties"][key] for row in rows]
        array = pa.array(
            values, type=pa.int64() if all(v is None for v in values) else None
        )
        dtype = (
            "Int64"
            if pa.types.is_integer(array.type) and array.null_count
            else "boolean"
            if pa.types.is_boolean(array.type) and array.null_count
            else None
        )
        data[key] = pd.Series(values, dtype=dtype or array.to_pandas().dtype)
    frame = gpd.GeoDataFrame(
        data,
        geometry=[shape(row["geometry"]) for row in rows],
        crs=value["crs"]["properties"]["name"],
    )
    schema = LocationOutputSchema(
        pandera.DataFrameSchema(
            {
                "id": pandera.Column(str, description="ID", metadata={"unit": None}),
                "geometry": pandera.Column(
                    Geometry(crs=frame.crs),
                    checks=pandera.Check(
                        lambda s: s.geom_type.notna(), name="present_geometry"
                    ),
                    description="Geometry",
                    metadata={"unit": None},
                ),
                **{
                    name: pandera.Column(
                        series.dtype,
                        nullable=True,
                        description=name,
                        metadata={"unit": None},
                    )
                    for name, series in data.items()
                    if name != "id"
                },
            },
            strict=True,
        )
    )
    return schema.prepare_frame(frame)


def fixture_locations(
    value: dict, schema: LocationOutputSchema | None = None
) -> gpd.GeoDataFrame:
    """Construct execution fixtures through a known producer declaration."""
    contract = (
        schema
        if schema is not None
        else output_schema(crs=value["crs"]["properties"]["name"])
    )
    rows = [
        row.model_dump() if hasattr(row, "model_dump") else row
        for row in value["features"]
    ]
    contract = type(contract)(
        contract.schema.update_column(
            contract.geometry_column,
            dtype=Geometry(crs=value["crs"]["properties"]["name"]),
        ),
        id_column=contract.id_column,
    )
    frame = contract.empty_frame(value["crs"]["properties"]["name"])
    if rows:
        frame = gpd.GeoDataFrame(
            [{contract.id_column: row["id"], **row["properties"]} for row in rows],
            geometry=[shape(row["geometry"]) for row in rows],
            crs=value["crs"]["properties"]["name"],
        )
        for name, column in contract.schema.columns.items():
            if name != contract.geometry_column and name in frame:
                frame[name] = column.dtype.coerce(frame[name])
    return contract.prepare_frame(frame)


def node_contract(
    collection: str = "test:units",
    unit_type: str = "lot",
    schema: LocationOutputSchema | OutputSchema | None = None,
) -> LocationContract:
    """Return an explicit concrete fixture contract."""
    output = (
        schema
        if isinstance(schema, LocationOutputSchema)
        else LocationOutputSchema(schema)
        if schema is not None
        else output_schema()
    )
    return LocationContract(
        family=output.family(collection, unit_type),
        source="fixture",
        geographical_edition="current",
        meaning="Provider locations",
    )


def provider_plugin(
    contract: LocationContract, schema: LocationOutputSchema, frame: gpd.GeoDataFrame
) -> "PluginDefinition":
    """Return a provider backed solely by a fixture frame."""

    class FixtureProvider(LocationProvider[IOConfig]):
        id = contract.family.collection
        origins = ()

        @override
        def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
            return (
                frame.loc[frame[schema.id_column].isin(ids)]
                .rename(columns={schema.id_column: "location_id"})
                .copy()
            )

    FixtureProvider.schema = native_schema(schema, str(frame.crs))

    return PluginDefinition(resources={"fixture": FixtureProvider})
