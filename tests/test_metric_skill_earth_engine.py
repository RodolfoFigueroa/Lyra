"""Offline checks of the Earth Engine evaluation workflow and adapter lifecycle."""

import importlib
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from typing_extensions import override

from lyra.contracts.errors import ResourceConfigurationError
from lyra.contracts.geometry import GeoJSON
from lyra.contracts.resources import IOConfig, ResourceConfig, SecretReference
from lyra.data.results import serialize_outputs
from lyra.runtime.execution import GraphExecutionError
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.local import LocalRunner
from lyra.runtime.resources import ResourceScope
from lyra.sdk.locations import LocationProvider
from lyra.sdk.plugin import PluginDefinition, metric
from lyra.sdk.plugin_cli import render_manifest
from lyra.utils.geometry import convert_geojson_to_gdf
from test_support.location_helpers import output_schema
from test_support.native_providers import native_schema
from tests.smoke_plugin_helpers import feature_collection

SCENARIOS = Path(__file__).parent / "fixtures/skill_workflows/scenarios"
ADAPTER = """from typing import Annotated
import ee
import pandas as pd
from pydantic import Field
import pandera.pandas as pa
import geopandas as gpd
from lyra.sdk.plugin import PluginDefinition, metric
from lyra.sdk.dependencies import Dependency, LocationDependency
from lyra.sdk.resources import BaseIO, Resource
from lyra.contracts.resources import IOConfig
from ee_trial_workflow import calculate_metric
from tests.test_metric_skill_earth_engine import PolygonProvider

import pandera.pandas as pa



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

    def read(self, location: gpd.GeoDataFrame) -> pd.DataFrame:
        with self.operation("read"):
            return calculate_metric(
                location
                .set_index("location_id")
                .rename_axis(None)
            )


@metric(code_revision="1",
    name="elevation_source",
    inputs={"location": LocationDependency(provider="test:units")},
    description="Mean SRTM elevation at 30 metre reduction resolution.",
    outputs={
        "elevation_records": pa.DataFrameSchema(strict=True,
            columns={
                "mean_elevation": pa.Column(
                    "float64",
                    description=(
                        "Mean elevation; null when no source pixels are available."
                    ),
                    metadata={"unit": "m"},
                    nullable=True,
                )
            }
        )
    },
)
def acquire(
    location: gpd.GeoDataFrame, engine: Annotated[EarthEngineIO, Resource("elevation")]
) -> pd.DataFrame:
    return engine.read(location)


@metric(code_revision="1",
    name="mean_elevation",
    description="Mean SRTM elevation by selected zone.",
    inputs={
        "location": LocationDependency(provider="test:units"),
        "records": Dependency("elevation_records", producer="elevation_source"),
    },
    outputs={
        "mean_elevation": pa.DataFrameSchema(strict=True,
            columns={
                "mean_elevation": pa.Column(
                    "float64",
                    description=(
                        "Mean elevation; null when source pixels are unavailable."
                    ),
                    metadata={"unit": "m"},
                    nullable=True,
                )
            }
        )
    },
)
def run(location: gpd.GeoDataFrame, records: pd.DataFrame) -> pd.DataFrame:
    return records


def create_plugin() -> PluginDefinition:
    return PluginDefinition(
        metrics=[acquire, run],
        resources={"elevation": EarthEngineIO, "locations": PolygonProvider},
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
        '[project.entry-points."lyra.plugins"]\n'
        'plugin = "ee_trial_adapter:create_plugin"\n',
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
    assert render_manifest(tmp_path)
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
    location = projected.reset_index(names="location_id")

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
    assert actual.isna().iloc[1, 0]
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
    normalized = BoundPlugin(adapter.create_plugin()).normalize_result(
        "mean_elevation", actual
    )
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
    id = "test:units"
    schema = native_schema(output_schema(), "EPSG:4326")

    @override
    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
        frame = convert_geojson_to_gdf(
            GeoJSON.model_validate(feature_collection(tuple(ids)))
        )
        frame["location_id"] = frame.index
        return frame[["location_id", "geometry"]].reset_index(drop=True)


def local_runner(adapter: ModuleType) -> LocalRunner:
    return LocalRunner.from_definitions(
        {
            "elevation": adapter.create_plugin(),
            "geometry": PluginDefinition(resources={"locations": PolygonProvider}),
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
            "mean_elevation",
            inputs={
                "location": {
                    "provider": PolygonProvider.id,
                    "ids": ["zone-z", "zone-a"],
                }
            },
        ).tables["mean_elevation"]
        assert actual["mean_elevation"].tolist()[0] == pytest.approx(124.5)
        assert actual.isna().iloc[1, 0]
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
            "mean_elevation",
            inputs={"location": {"provider": PolygonProvider.id, "ids": ["zone-z"]}},
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
        code_revision="1",
        name="constant",
        description="A calculation without external data",
        outputs={
            "value": pa.DataFrameSchema(
                {
                    "value": pa.Column(
                        int, description="Constant", metadata={"unit": None}
                    )
                },
                strict=True,
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
    assert runner.run("constant", inputs={}).tables["value"]["value"].tolist() == [1]
    assert engine.mock_calls == []
    with pytest.raises(GraphExecutionError, match="elevation") as error:
        local_runner(adapter).run(
            "mean_elevation",
            inputs={"location": {"provider": PolygonProvider.id, "ids": ["zone-z"]}},
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
