import asyncio
import json
from copy import deepcopy
from pathlib import Path
from typing import Literal
from unittest.mock import MagicMock, Mock

import geopandas
import pytest
from lyra.sdk.models.plugin import SpatialInputKind
from shapely.geometry import Point, Polygon
from sqlalchemy import Connection, Engine

from lyra_app import spatial_inputs
from lyra_app.converters import bounds, build_converter_map, location
from lyra_app.db.connection import ApplicationDatabaseRuntime
from tests.config_helpers import load_test_config


@pytest.mark.parametrize("kind", ["location", "bounds"])
@pytest.mark.usefixtures("dummy_database_engines")
@pytest.mark.parametrize("source", ["geojson", "cvegeo_list", "met_zone_code"])
def test_spatial_resolution_uses_bound_converters_and_executor(
    kind: SpatialInputKind,
    source: Literal["geojson", "cvegeo_list", "met_zone_code"],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    geometry = geopandas.GeoDataFrame(
        geometry=[Polygon([(0, 0), (1, 0), (1, 1), (0, 0)])],
        index=["09002"],
        crs="EPSG:6372",
    )
    geojson = json.loads(geometry.to_json())
    value = {"geojson": geojson, "cvegeo_list": ["09002"], "met_zone_code": "09.01"}[
        source
    ]
    payload = {kind: {"data_type": source, "value": value}, "other": 3}
    original_payload = deepcopy(payload)
    lookups: list[str | list[str]] = []

    def load(reference: str | list[str], *, conn: Connection) -> geopandas.GeoDataFrame:
        assert not conn.closed
        lookups.append(reference)
        return geometry

    module = location if kind == "location" else bounds
    prefix = "load_geometries" if kind == "location" else "load_bounds"
    monkeypatch.setattr(module, f"{prefix}_from_cvegeos", load)
    monkeypatch.setattr(module, f"{prefix}_from_met_zone_code", load)
    engine = MagicMock(spec=Engine)
    engine.connect.return_value.__enter__.return_value.closed = False
    monkeypatch.setattr(
        "lyra_app.db.connection.create_sync_database_engine", lambda *_: engine
    )
    runtime = ApplicationDatabaseRuntime(load_test_config(tmp_path))

    async def exercise() -> dict:
        await runtime.start()
        try:
            if source == "geojson":
                monkeypatch.setattr(
                    engine,
                    "connect",
                    Mock(
                        side_effect=AssertionError(
                            "GeoJSON must not query the database"
                        )
                    ),
                )
            return await runtime.run_spatial(
                spatial_inputs.resolve_spatial_inputs,
                payload,
                {kind: kind},
                converter_map=build_converter_map(runtime.require_spatial_engine()),
            )
        finally:
            await runtime.close()

    resolution = asyncio.run(exercise())
    assert resolution == {kind: geojson, "other": 3}
    assert payload == original_payload
    assert lookups == ([] if source == "geojson" else [value])


@pytest.mark.parametrize("kind", ["location", "bounds"])
@pytest.mark.parametrize("source", ["geojson", "cvegeo_list", "met_zone_code"])
def test_spatial_resolution_rejects_points(
    kind: SpatialInputKind,
    source: Literal["geojson", "cvegeo_list", "met_zone_code"],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    geometry = geopandas.GeoDataFrame(
        geometry=[Point(1, 2)], index=["09002"], crs="EPSG:6372"
    )
    values = {
        "geojson": json.loads(geometry.to_json()),
        "cvegeo_list": ["09002"],
        "met_zone_code": "09.01",
    }
    module = location if kind == "location" else bounds
    prefix = "load_geometries" if kind == "location" else "load_bounds"
    lookup = Mock(return_value=geometry)
    monkeypatch.setattr(module, f"{prefix}_from_cvegeos", lookup)
    monkeypatch.setattr(module, f"{prefix}_from_met_zone_code", lookup)
    engine = MagicMock(spec=Engine)
    with pytest.raises(spatial_inputs.SpatialInputValidationError) as exc:
        spatial_inputs.resolve_spatial_inputs(
            {kind: {"data_type": source, "value": values[source]}},
            {kind: kind},
            converter_map=build_converter_map(engine),
        )
    assert all(error["loc"][0] == kind for error in exc.value.errors)
    assert lookup.call_count == (0 if source == "geojson" else 1)
