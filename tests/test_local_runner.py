"""Local execution uses SDK graphs and isolated runtime resources."""

from __future__ import annotations

import builtins
import logging
from typing import TYPE_CHECKING
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from lyra.sdk import (
    Dependency,
    GraphExecutionError,
    LocalRunner,
    LocationInput,
    MetricParameters,
    PluginDefinition,
    PluginDefinitionError,
    RunContext,
    metric,
    plugin_loader,
)
from lyra.sdk.models.plugin import PluginInfo
from lyra.sdk.resources import ResourceConfigurationError
from pydantic import Field, ValidationError
from shapely.geometry import Point

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path
    from types import ModuleType

TABLE = pa.DataFrameSchema(
    {"value": pa.Column(int, description="Value", metadata={"unit": None})}
)


class Parameters(MetricParameters):
    count: int = Field(default=2, description="Requested count")


def test_cross_plugin_reuse_context_and_cleanup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    contexts: list[RunContext] = []
    frames: list[pd.DataFrame] = []

    @metric(name="source", description="source", outputs={"a": TABLE, "b": TABLE})
    def source(parameters: Parameters, context: RunContext) -> dict[str, pd.DataFrame]:
        contexts.append(context)
        assert context.temp_dir.is_dir()
        context.report_progress(stage="read", current=1, total=1)
        frame = pd.DataFrame({"value": [parameters.count]}, index=[42])
        frames.append(frame)
        return {"a": frame, "b": frame}

    @metric(
        name="root",
        description="root",
        inputs={"a": Dependency("a"), "b": Dependency("b")},
        outputs={"result": TABLE},
    )
    def root(
        parameters: Parameters, a: pd.DataFrame, b: pd.DataFrame, context: RunContext
    ) -> pd.DataFrame:
        contexts.append(context)
        pd.testing.assert_frame_equal(a, b)
        assert a.value.iloc[0] == parameters.count
        assert not hasattr(context, "db")
        return a

    runner = LocalRunner.from_definitions(
        {
            "data": PluginDefinition(nodes=[source]),
            "metric": PluginDefinition(nodes=[root]),
        }
    )
    with caplog.at_level(logging.INFO):
        for index in range(2):
            result = runner.run("root", {"parameters": {"count": index}})
            pd.testing.assert_frame_equal(result["result"], frames[index])
            assert result["result"].index.tolist() == [42]
            first, second = contexts[index * 2 : index * 2 + 2]
            assert first.job_id == second.job_id
            assert first.temp_dir == second.temp_dir
            assert not first.temp_dir.exists()
            assert (first.node, second.node) == ("source", "root")
    assert contexts[0].job_id != contexts[2].job_id
    assert "Progress:" in caplog.text


@pytest.mark.parametrize("failure", ["db", "handler", "progress"])
def test_failure_cleanup(failure: str) -> None:
    directories: list[Path] = []

    @metric(name="source", description="source", outputs={"source": TABLE})
    def source(context: RunContext) -> pd.DataFrame:
        directories.append(context.temp_dir)
        if failure == "db":
            message = "database is not configured"
            raise ResourceConfigurationError(message)
        if failure == "progress":
            context.report_progress(stage="read", current=-1)
        msg = "deliberate failure"
        raise RuntimeError(msg)

    @metric(
        name="root",
        description="root",
        inputs={"data": Dependency("source")},
        outputs={"root": TABLE},
    )
    def root(data: pd.DataFrame) -> pd.DataFrame:
        return data

    runner = LocalRunner.from_definitions(
        {"plugin": PluginDefinition(nodes=[root, source])}
    )
    with pytest.raises(GraphExecutionError) as error:
        runner.run("root", {})
    assert error.value.path == ("root", "source")
    assert not directories[0].exists()
    if failure == "db":
        assert "database" in str(error.value)
    elif failure == "progress":
        assert isinstance(error.value.__cause__, ValidationError)


def test_spatial_and_custom_context(tmp_path: Path) -> None:
    frame = gpd.GeoDataFrame(
        {"value": [1]}, geometry=[Point(1, 2)], crs=3857, index=[7]
    )
    context = Mock(spec=RunContext, temp_dir=tmp_path)
    factory = Mock(return_value=context)

    @metric(
        name="root",
        description="root",
        outputs={
            "root": pa.DataFrameSchema(
                {
                    "value": pa.Column(
                        int, description="Value", metadata={"unit": None}
                    ),
                    "geometry": pa.Column(
                        "geometry", description="Geometry", metadata={"unit": None}
                    ),
                }
            )
        },
    )
    def root(location: LocationInput, context: RunContext) -> gpd.GeoDataFrame:
        assert len(location.features) == 1
        assert context.temp_dir == tmp_path
        return frame

    definition = PluginDefinition(nodes=[root])
    runner = LocalRunner.from_definitions(
        {"plugin": definition}, context_factory=factory
    )
    result = runner.run(
        "root",
        {
            "location": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "id": "area",
                        "properties": {},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]],
                        },
                    }
                ],
                "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
            }
        },
    )
    pd.testing.assert_frame_equal(result["root"], frame)
    factory.assert_called_once_with("root")
    assert tmp_path.exists()
    with pytest.raises(PluginDefinitionError, match="Unknown public metric"):
        runner.run("unknown", {})
    contract = definition.node_manifests[0]
    contract.description = "changed"
    assert definition.node_manifests[0].description == "root"


def test_installed_loading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    @metric(name="root", description="root", outputs={"root": TABLE})
    def root() -> pd.DataFrame:
        return pd.DataFrame({"value": [1]})

    definition = PluginDefinition(nodes=[root])
    manifest = definition.manifest(
        plugin=PluginInfo(name="local-plugin", version="1"),
        factory="example:create_plugin",
    )
    monkeypatch.setattr(plugin_loader.sys, "prefix", str(tmp_path))
    monkeypatch.setattr(plugin_loader, "installed_version", lambda _: "1")
    monkeypatch.setattr(plugin_loader, "load_plugin_definition", lambda _: definition)
    path = plugin_loader.installed_manifest_path("local-plugin")
    path.parent.mkdir(parents=True)
    path.write_text(manifest.model_dump_json())
    assert LocalRunner(["Local_Plugin"]).run("root", {})["root"].value.tolist() == [1]
    with pytest.raises(ValueError, match="Duplicate"):
        LocalRunner(["local-plugin", "Local_Plugin"])
    manifest.nodes[0].description = "stale"
    path.write_text(manifest.model_dump_json())
    with pytest.raises(RuntimeError, match="build-manifest"):
        LocalRunner(["local-plugin"])


def test_no_application_imports(monkeypatch: pytest.MonkeyPatch) -> None:
    original = builtins.__import__

    def guarded(
        name: str,
        module_globals: Mapping[str, object] | None = None,
        module_locals: Mapping[str, object] | None = None,
        fromlist: Sequence[str] | None = (),
        level: int = 0,
    ) -> ModuleType:
        if name.startswith("lyra_app"):
            pytest.fail("Local execution must not import the application")
        return original(name, module_globals, module_locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded)

    @metric(name="source", description="source", outputs={"source": TABLE})
    def source() -> pd.DataFrame:
        return pd.DataFrame({"value": pd.Series(dtype="int64")})

    @metric(
        name="root",
        description="root",
        inputs={"data": Dependency("source")},
        outputs={"root": TABLE},
    )
    def root(data: pd.DataFrame) -> pd.DataFrame:
        return data

    runner = LocalRunner.from_definitions(
        {"plugin": PluginDefinition(nodes=[source, root])}
    )
    assert runner.run("root", {})["root"].empty
    assert runner.run("source", {})["source"].empty
    with pytest.raises(PluginDefinitionError, match="Unknown public metric"):
        runner.run("missing", {})
    with pytest.raises(ValueError, match="at least one"):
        LocalRunner.from_definitions({})
