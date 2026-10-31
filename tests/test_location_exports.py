import importlib
import json
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import geopandas as gpd
import pytest
from lyra.sdk import export_locations, read_location_artifact
from lyra.sdk.location_artifacts import encode
from lyra.sdk.models.location import Locations, location_snapshot
from shapely.geometry import Point


def value(properties: dict | None = None) -> Locations:
    frame = gpd.GeoDataFrame(
        {"id": ["a", "b"], **(properties or {"count": [1, 2]})},
        geometry=[Point(1, 2), Point(3, 4)],
        crs=4326,
    )
    return Locations.from_geodataframe(
        frame, collection="test:points", unit_type="point", id_column="id"
    )


@pytest.mark.parametrize("file_format", ["geoparquet", "geopackage", "shapefile"])
def test_export(file_format: str, tmp_path: Path) -> None:
    if file_format != "geoparquet":
        pytest.importorskip("pyogrio")
    source = value()
    before = source.to_dict()
    artifact_path = tmp_path / "source.arrow"
    artifact_path.write_bytes(encode(location_snapshot(source)))
    artifact = read_location_artifact(artifact_path)
    artifact.preview()
    assert list(tmp_path.iterdir()) == [artifact_path]
    destination = export_locations(artifact, tmp_path / "out", file_format=file_format)
    assert json.loads((destination / "scope.json").read_text()) == {
        "collection": source.collection,
        "unit_type": source.unit_type,
        "crs": source.crs.model_dump(),
    }
    suffix = {"geoparquet": "parquet", "geopackage": "gpkg", "shapefile": "shp"}[
        file_format
    ]
    output = destination / f"locations.{suffix}"
    restored = (
        gpd.read_parquet(output)
        if file_format == "geoparquet"
        else gpd.read_file(output, engine="pyogrio")
    )
    assert restored.crs == source.to_geodataframe().crs
    assert (
        Locations.from_geodataframe(
            restored,
            collection=source.collection,
            unit_type=source.unit_type,
            id_column="id",
        ).to_dict()["features"]
        == before["features"]
    )
    assert artifact.to_locations().to_dict() == before
    with pytest.raises(FileExistsError):
        export_locations(source, destination)


@pytest.mark.parametrize(
    ("properties", "file_format", "reason"),
    [
        ({"count": [2**63, 1]}, "geoparquet", "64-bit"),
        ({"count": [2**60, 1]}, "shapefile", "range"),
        ({"long_field_name": [1, 2]}, "shapefile", "10 characters"),
        ({"count": [1, "x"]}, "geoparquet", "mixed"),
        ({"count": [None, "x"]}, "shapefile", "non-null"),
        ({"Foo": [1, 2], "foo": [1, 2]}, "geopackage", "collision"),
    ],
)
def test_rejected(
    properties: dict, file_format: str, reason: str, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match=reason):
        export_locations(value(properties), tmp_path / "out", file_format=file_format)
    assert not list(tmp_path.iterdir())


def test_missing_driver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pyogrio = pytest.importorskip("pyogrio")
    monkeypatch.setattr(pyogrio, "list_drivers", Mock(return_value={}))
    with pytest.raises(ValueError, match="optional"):
        export_locations(value(), tmp_path / "out", file_format="geopackage")
    export_locations(value({"count": [2**60 + 1, 2]}), tmp_path / "parquet")


@pytest.mark.parametrize("case", ["missing", "dimensions", "mixed_geometry"])
def test_structural_rejection(case: str, tmp_path: Path) -> None:
    raw = value().to_dict()
    if case == "missing":
        raw["features"][0]["properties"] = {}
    elif case == "dimensions":
        raw["features"][0]["geometry"]["coordinates"] = [1, 2, 3, 4]
    else:
        raw["features"][0]["geometry"] = {
            "type": "LineString",
            "coordinates": [[1, 2], [3, 4]],
        }
    with pytest.raises(ValueError, match=r"missing|dimensions|Mixed geometry"):
        export_locations(
            Locations.from_dict(raw), tmp_path / "out", file_format="geopackage"
        )
    assert not list(tmp_path.iterdir())


def test_nullable_large_integer(tmp_path: Path) -> None:
    raw = value().to_dict()
    raw["features"][0]["properties"]["count"] = 2**60 + 1
    raw["features"][1]["properties"]["count"] = None
    source = Locations.from_dict(raw)
    export_locations(source, tmp_path / "out")
    result = gpd.read_parquet(tmp_path / "out" / "locations.parquet")
    assert result["count"].iloc[0] == 2**60 + 1
    assert source.to_dict() == raw


def test_missing_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = importlib.import_module

    def import_without_driver(name: str) -> ModuleType:
        if name == "pyogrio":
            message = "No module named pyogrio"
            raise ModuleNotFoundError(message)
        return original_import(name)

    monkeypatch.setattr(
        "lyra.sdk.location_exports.importlib.import_module", import_without_driver
    )
    with pytest.raises(ValueError, match="optional pyogrio"):
        export_locations(value(), tmp_path / "out", file_format="geopackage")
    assert not list(tmp_path.iterdir())
    export_locations(value(), tmp_path / "parquet")
