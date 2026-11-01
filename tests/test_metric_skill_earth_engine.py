"""Offline checks of the Earth Engine evaluation workflow and adapter lifecycle."""

import importlib
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import ClassVar
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from lyra.sdk import (
    IOConfig,
    LocalRunner,
    LocationCollection,
    LocationOutputSchema,
    LocationProvider,
    PluginDefinition,
    ResourceConfig,
    SecretReference,
    metric,
)
from lyra.sdk.execution import GraphExecutionError
from lyra.sdk.location_contracts import LocationContract, LocationFamily
from lyra.sdk.models.geometry import GeoJSON
from lyra.sdk.plugin_cli import build_manifest, check_manifest
from lyra.sdk.resources import ResourceConfigurationError, ResourceScope
from lyra.sdk.results import serialize_outputs
from lyra.utils.geometry import convert_geojson_to_gdf
from typing_extensions import override

from tests.location_helpers import output_schema
from tests.smoke_plugin_helpers import feature_collection
from tests.test_location_resolution import selection

SCENARIOS = Path(__file__).parent / "fixtures/skill_workflows/scenarios"
ADAPTER = """from typing import Annotated
import ee
import pandas as pd
from pydantic import Field
import pandera.pandas as pa
from lyra.sdk import (
    Locations,
    PluginDefinition,
    Unit,
    metric,
    Dependency,
    BaseIO,
    IOConfig,
    Resource,
)
from ee_trial_workflow import calculate_metric


class EarthEngineSettings(IOConfig):
    project: str = Field(min_length=1)


class EarthEngineIO(BaseIO[EarthEngineSettings]):
    config_model = EarthEngineSettings
    secret_names = ("service_account_json",)

    def setup(self) -> None:
        credentials = ee.ServiceAccountCredentials(
            key_data=self.secrets["service_account_json"]
        )
        ee.Initialize(credentials, project=self.config.project)

    def close(self) -> None:
        ee.Reset()

    def read(self, location: Locations) -> pd.DataFrame:
        with self.operation("read"):
            return calculate_metric(
                location.to_geodataframe(id_column="id").set_index("id").rename_axis(None)
            )


@metric(
    name="elevation_source",
    description="Mean SRTM elevation at 30 metre reduction resolution.",
    outputs={
        "elevation_records": pa.DataFrameSchema(
            {
                "mean_elevation": pa.Column(
                    "float64",
                    description=(
                        "Mean elevation; null when no source pixels are available."
                    ),
                    metadata={"unit": Unit.METRE},
                    nullable=True,
                )
            }
        )
    },
)
def acquire(
    location: Locations, engine: Annotated[EarthEngineIO, Resource("elevation")]
) -> pd.DataFrame:
    return engine.read(location)


@metric(
    name="mean_elevation",
    description="Mean SRTM elevation by selected zone.",
    inputs={"records": Dependency("elevation_records")},
    outputs={
        "mean_elevation": pa.DataFrameSchema(
            {
                "mean_elevation": pa.Column(
                    "float64",
                    description=(
                        "Mean elevation; null when source pixels are unavailable."
                    ),
                    metadata={"unit": Unit.METRE},
                    nullable=True,
                )
            }
        )
    },
)
def run(location: Locations, records: pd.DataFrame) -> pd.DataFrame:
    return records


def create_plugin() -> PluginDefinition:
    return PluginDefinition(
        metrics=[acquire, run], resources={"elevation": EarthEngineIO}
    )
"""


@pytest.fixture
def adapted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[ModuleType, ModuleType, Mock]]:
    shutil.copyfile(SCENARIOS / "earth_engine.py", tmp_path / "ee_trial_workflow.py")
    (tmp_path / "ee_trial_adapter.py").write_text(ADAPTER, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "ee-trial"\nversion = "0.1.0"\n'
        '[tool.lyra]\nfactory = "ee_trial_adapter:create_plugin"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("EE_TEST_JSON", SERVICE_ACCOUNT)
    monkeypatch.syspath_prepend(str(tmp_path))
    for name in ("ee_trial_workflow", "ee_trial_adapter"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    engine = Mock()
    monkeypatch.setitem(sys.modules, "ee", engine)
    try:
        workflow = importlib.import_module("ee_trial_workflow")
        adapter = importlib.import_module("ee_trial_adapter")
        yield workflow, adapter, engine
    finally:
        for name in ("ee_trial_workflow", "ee_trial_adapter"):
            sys.modules.pop(name, None)


def test_import_factory_and_manifest_do_not_use_earth_engine(
    adapted: tuple[ModuleType, ModuleType, Mock], tmp_path: Path
) -> None:
    _, adapter, engine = adapted
    assert adapter.create_plugin().metric_names == (
        "elevation_source",
        "mean_elevation",
    )
    build_manifest(tmp_path)
    assert check_manifest(tmp_path) == (True, "")
    assert engine.mock_calls == []


def test_earth_engine_adapter_preserves_reprojection_and_missing_values(
    adapted: tuple[ModuleType, ModuleType, Mock],
) -> None:
    workflow, adapter, engine = adapted
    geographic = convert_geojson_to_gdf(
        GeoJSON.model_validate(feature_collection(("zone-z", "zone-a")))
    )
    projected = geographic.to_crs("EPSG:3857")
    # No workflow-specific feature properties are supplied.
    location = output_schema().build(
        projected.reset_index(names="id"), collection="test:units", unit_type="lot"
    )
    image = engine.Image.return_value.select.return_value
    image.reduceRegion.return_value.get.return_value.getInfo.side_effect = [
        124.5,
        None,
        124.5,
        None,
    ]
    scope = ResourceScope({"elevation": adapter.EarthEngineIO}, resource_settings())
    with scope.activate():
        records = adapter.acquire(location=location, engine=scope.get("elevation"))
    calls = list(engine.mock_calls)
    actual = adapter.run(location=location, records=records)
    assert engine.mock_calls == calls
    expected = workflow.calculate_metric(projected)
    pd.testing.assert_frame_equal(actual, expected)
    assert actual.index.tolist() == ["zone-z", "zone-a"]
    assert actual.iloc[0, 0] == pytest.approx(124.5)
    assert pd.isna(actual.iloc[1, 0])
    engine.Image.assert_called_with("USGS/SRTMGL1_003")
    engine.Image.return_value.select.assert_called_with("elevation")
    expected_geometry = gpd.GeoDataFrame.from_features(
        [
            {"type": "Feature", "geometry": call.args[0], "properties": {}}
            for call in engine.Geometry.call_args_list[:2]
        ],
        crs="EPSG:4326",
    )
    assert expected_geometry.geometry.geom_equals_exact(
        gpd.GeoSeries(geographic.geometry.to_numpy(), crs=geographic.crs),
        tolerance=1e-8,
    ).all()
    for call in image.reduceRegion.call_args_list:
        assert call.kwargs["scale"] == 30
        assert call.kwargs["reducer"] is engine.Reducer.mean.return_value
    engine.Authenticate.assert_not_called()
    engine.ServiceAccountCredentials.assert_called_once_with(key_data=SERVICE_ACCOUNT)
    engine.Initialize.assert_called_once_with(
        engine.ServiceAccountCredentials.return_value, project="test-project"
    )
    engine.Reset.assert_called_once_with()
    normalized = adapter.create_plugin().normalize_result("mean_elevation", actual)
    normalized = next(iter(normalized.values()))
    assert normalized.index.tolist() == [0, 1]
    pd.testing.assert_frame_equal(normalized, expected.reset_index(drop=True))
    encoded = serialize_outputs("mean_elevation", {"mean_elevation": normalized})
    assert encoded == {
        "mean_elevation": [{"mean_elevation": 124.5}, {"mean_elevation": None}]
    }


SERVICE_ACCOUNT = '{"client_email":"offline@example.test"}'


def resource_settings() -> dict[str, ResourceConfig]:
    return {
        "elevation": ResourceConfig(
            config={"project": "test-project"},
            secrets={"service_account_json": SecretReference(env="EE_TEST_JSON")},
        )
    }


class PolygonProvider(LocationProvider[IOConfig]):
    collections = (
        LocationCollection(name="test:units", unit_types=("lot",), crs="EPSG:4326"),
    )
    outputs: ClassVar[dict[tuple[str, str], LocationOutputSchema]] = {
        ("test:units", "lot"): output_schema()
    }
    contracts = tuple(
        LocationContract(
            family=LocationFamily.from_schema(collection, unit, schema),
            source="fixture",
            geographical_edition="current",
            meaning="Provider locations",
        )
        for (collection, unit), schema in outputs.items()
    )

    @override
    def resolve(self, contract: LocationContract, ids: list[str]) -> gpd.GeoDataFrame:
        _collection, _unit_type = contract.family.collection, contract.family.unit_type
        frame = convert_geojson_to_gdf(
            GeoJSON.model_validate(feature_collection(tuple(ids)))
        )
        frame["id"] = frame.index
        return frame[["id", "geometry"]].reset_index(drop=True)


def local_runner(adapter: ModuleType) -> LocalRunner:
    return LocalRunner.from_definitions(
        {
            "elevation": adapter.create_plugin(),
            "geometry": PluginDefinition(
                resources={"locations": PolygonProvider}, location_provider="locations"
            ),
        },
        resource_config=resource_settings(),
    )


def test_local_execution_initializes_per_run_and_preserves_values(
    adapted: tuple[ModuleType, ModuleType, Mock],
) -> None:
    _, adapter, engine = adapted
    runner = local_runner(adapter)
    assert engine.mock_calls == []
    for count in (1, 2):
        image = engine.Image.return_value.select.return_value
        image.reduceRegion.return_value.get.return_value.getInfo.side_effect = [
            124.5,
            None,
        ]
        actual = runner.run(
            "mean_elevation", {"location": selection("zone-z", "zone-a").model_dump()}
        )["mean_elevation"]
        assert actual["mean_elevation"].tolist()[0] == pytest.approx(124.5)
        assert pd.isna(actual.iloc[1, 0])
        assert engine.Initialize.call_count == count
        assert engine.Reset.call_count == count
    engine.Authenticate.assert_not_called()


@pytest.mark.parametrize("stage", ["credentials", "initialization", "acquisition"])
def test_resource_failures_propagate_and_close(
    adapted: tuple[ModuleType, ModuleType, Mock], stage: str
) -> None:
    _, adapter, engine = adapted
    failure = RuntimeError("source unavailable")
    boundary = {
        "credentials": engine.ServiceAccountCredentials,
        "initialization": engine.Initialize,
        "acquisition": engine.Image,
    }[stage]
    boundary.side_effect = failure
    with pytest.raises(GraphExecutionError, match="source unavailable") as error:
        local_runner(adapter).run(
            "mean_elevation", {"location": selection("zone-z").model_dump()}
        )
    assert error.value.__cause__ is failure
    engine.Reset.assert_called_once_with()
    if stage != "acquisition":
        engine.Image.assert_not_called()


def test_unrequested_resource_does_not_resolve_credentials(
    adapted: tuple[ModuleType, ModuleType, Mock], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, adapter, engine = adapted
    monkeypatch.delenv("EE_TEST_JSON")

    @metric(
        name="constant",
        description="A calculation without external data",
        outputs={
            "value": pa.DataFrameSchema(
                {
                    "value": pa.Column(
                        int, description="Constant", metadata={"unit": None}
                    )
                }
            )
        },
    )
    def constant() -> pd.DataFrame:
        return pd.DataFrame({"value": [1]})

    runner = LocalRunner.from_definitions(
        {
            "elevation": adapter.create_plugin(),
            "local": PluginDefinition(metrics=[constant]),
        },
        resource_config=resource_settings(),
    )
    assert runner.run("constant", {})["value"]["value"].tolist() == [1]
    assert engine.mock_calls == []
    with pytest.raises(GraphExecutionError, match="elevation") as error:
        local_runner(adapter).run(
            "mean_elevation", {"location": selection("zone-z").model_dump()}
        )
    assert engine.mock_calls == []
    assert isinstance(error.value.__cause__, ResourceConfigurationError)


def test_resource_is_reused_within_execution(
    adapted: tuple[ModuleType, ModuleType, Mock],
) -> None:
    _, adapter, engine = adapted
    scope = ResourceScope({"elevation": adapter.EarthEngineIO}, resource_settings())
    with scope.activate():
        first = scope.get("elevation")
        assert scope.get("elevation") is first
        engine.Initialize.assert_called_once()
        engine.Reset.assert_not_called()
    engine.Reset.assert_called_once_with()
