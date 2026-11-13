"""Native provider graphs for preparation, execution, and identity tests."""

from typing import ClassVar

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
from pandera.engines.geopandas_engine import Geometry
from shapely.geometry import Point
from typing_extensions import override

from lyra.sdk import (
    Dependency,
    Input,
    LocationDependency,
    LocationProvider,
    Origin,
    PluginDefinition,
    location_operation,
    metric,
)

RECORDS = pa.DataFrameSchema(
    {
        "location_id": pa.Column(
            str,
            unique=True,
            checks=pa.Check(lambda s: s.str.len().gt(0), name="nonempty"),
            description="ID",
            metadata={"unit": None},
        ),
        "geometry": pa.Column(
            Geometry(crs="EPSG:4326"),
            checks=pa.Check(lambda s: s.is_valid & ~s.is_empty, name="valid"),
            description="Geometry",
            metadata={"unit": None},
        ),
        "value": pa.Column(int, description="Value", metadata={"unit": None}),
    },
    strict=True,
)
RESULT = pa.DataFrameSchema(
    {"total": pa.Column(int, description="Total", metadata={"unit": None})}, strict=True
)


def provider_execution_definitions(
    events: list[str],
    *,
    revision: str = "1",
    alias: str = "source_binding",
    metric_revision: str = "1",
    invalid: bool = False,
) -> dict[str, PluginDefinition]:
    class Source(LocationProvider):
        id = "source"
        code_revision = revision
        origins = (Origin(id="external", revision="2025"),)
        schema = RECORDS
        trace: ClassVar[list[str]] = events

        @override
        def setup(self) -> None:
            self.trace.append(f"setup:{self.id}")

        @override
        def close(self) -> None:
            self.trace.append(f"close:{self.id}")

        @override
        def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
            self.trace.append(f"resolve:{self.id}")
            frame = gpd.GeoDataFrame(
                {"location_id": ids, "value": [1] * len(ids)},
                geometry=[Point(0, 0)] * len(ids),
                crs="EPSG:4326",
            )
            frame.attrs["provider_scope"] = {"provider_id": "forged"}
            if invalid:
                frame["unexpected"] = 1
            return frame

    class Target(Source):
        id = "target"

    @location_operation(
        cache="full",
        name="select_target",
        code_revision="1",
        description="Select target records",
        inputs={"places": LocationDependency(provider="source")},
        target_provider="target",
        canonical=True,
    )
    def select_target(places: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        events.append("operation")
        return {key: (key,) for key in places.location_id}

    @metric(
        cache="full",
        name="count",
        code_revision="1",
        description="Count values",
        inputs={"places": LocationDependency(provider="target")},
        outputs={"result": RESULT},
        origins=(Origin(id="metric-direct", revision=metric_revision),),
    )
    def count(places: gpd.GeoDataFrame) -> pd.DataFrame:
        events.append("metric")
        assert set(places.columns) - {"source_location_id"} == {
            "location_id",
            "geometry",
            "value",
        }
        if "source_location_id" in places:
            assert places.source_location_id.tolist() == places.location_id.tolist()
        return pd.DataFrame({"total": [int(places.value.sum())]})

    @metric(
        cache="full",
        name="derived",
        code_revision="1",
        description="Forward derived records",
        inputs={
            "source": LocationDependency(provider="source"),
            "places": LocationDependency(operation="select_target").bind(
                places=Input("source")
            ),
            "data": Dependency("result", producer="count").bind(places=Input("places")),
        },
        outputs={"result": RESULT},
    )
    def derived(
        source: gpd.GeoDataFrame, places: gpd.GeoDataFrame, data: pd.DataFrame
    ) -> pd.DataFrame:
        assert source.location_id.tolist() == places.location_id.tolist()
        return data

    return {
        "calculations": PluginDefinition(
            metrics=[count, derived], location_operations=[select_target]
        ),
        "providers": PluginDefinition(
            resources={alias: Source, "target_binding": Target}
        ),
    }
