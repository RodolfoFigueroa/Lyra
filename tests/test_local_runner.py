"""Local execution uses SDK graphs and isolated runtime resources."""

from __future__ import annotations

import builtins
import logging
from contextlib import nullcontext
from typing import TYPE_CHECKING, Annotated
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from lyra.sdk import (
    BaseIO,
    Dependency,
    GraphExecutionError,
    Input,
    IOConfig,
    LocalRunner,
    Locations,
    LocationSelectionError,
    MetricInputError,
    MetricParameters,
    PluginDefinition,
    PluginDefinitionError,
    Resource,
    ResourceConfig,
    RunContext,
    UnresolvedLocationIDsError,
    metric,
    plugin_loader,
)
from lyra.sdk.computation import CacheRevision
from lyra.sdk.errors import LocationAcquisitionError, LocationProviderContractError
from lyra.sdk.models.geometry import PointGeometry
from lyra.sdk.models.job import JobCreateRequest
from lyra.sdk.models.plugin import PluginInfo
from lyra.sdk.resources import ResourceConfigurationError
from pydantic import Field, TypeAdapter, ValidationError
from shapely.geometry import Point
from typing_extensions import override

from tests.location_helpers import producer_result

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path
    from types import ModuleType

from tests.test_location_resolution import Provider, features, selection

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
            "data": PluginDefinition(metrics=[source]),
            "metric": PluginDefinition(metrics=[root]),
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
        {"plugin": PluginDefinition(metrics=[root, source])}
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
    def root(location: Locations, context: RunContext) -> gpd.GeoDataFrame:
        assert len(location) == 1
        assert context.temp_dir == tmp_path
        return frame

    definition = PluginDefinition(metrics=[root])
    runner = LocalRunner.from_definitions(
        {
            "plugin": definition,
            "geography": PluginDefinition(
                resources={"locations": Provider}, location_provider="locations"
            ),
        },
        context_factory=factory,
    )
    result = runner.run(
        "root",
        {"location": selection("area").model_dump()},
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

    definition = PluginDefinition(metrics=[root])
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
        {"plugin": PluginDefinition(metrics=[source, root])}
    )
    assert runner.run("root", {})["root"].empty
    assert runner.run("source", {})["source"].empty
    with pytest.raises(PluginDefinitionError, match="Unknown public metric"):
        runner.run("missing", {})
    with pytest.raises(ValueError, match="at least one"):
        LocalRunner.from_definitions({})


@pytest.mark.parametrize("fail_handler", [False, True])
def test_resolution_scope_bindings_and_cache(
    tmp_path: Path, *, fail_handler: bool
) -> None:
    events: list[object] = []

    class Settings(IOConfig):
        label: str

    class ConfiguredProvider(Provider):
        config_model = Settings

    class FakeProvider(ConfiguredProvider):
        @override
        def setup(self) -> None:
            assert isinstance(self.config, Settings)
            events.append(("setup", self.config.label))

        @override
        def resolve(
            self, collection: str, unit_type: str, ids: list[str]
        ) -> gpd.GeoDataFrame:
            events.append((collection, unit_type, ids))
            return producer_result(
                Locations.from_dict(
                    features(ids).to_dict()
                    | {"collection": collection, "unit_type": unit_type}
                ),
                self.outputs[collection, unit_type],
            )

        @override
        def close(self) -> None:
            events.append("provider closed")

    class Support(BaseIO[IOConfig]):
        @override
        def setup(self) -> None:
            assert "provider closed" not in events
            events.append("support setup")

        @override
        def close(self) -> None:
            events.append("support closed")

    @metric(name="source", description="Source", outputs={"source": TABLE})
    def source(
        location: Locations, support: Annotated[BaseIO, Resource("support")]
    ) -> pd.DataFrame:
        assert isinstance(support, Support)
        events.append(("source", list(location.ids)))
        return pd.DataFrame({"value": [1]})

    @metric(
        name="root",
        description="Root",
        outputs={"root": TABLE},
        inputs={
            "a": Dependency("source", locations={"location": Input("lots")}),
            "b": Dependency("source", locations={"location": Input("blocks")}),
        },
    )
    def root(
        lots: Locations, blocks: Locations, a: pd.DataFrame, b: pd.DataFrame
    ) -> pd.DataFrame:
        assert lots.to_features() == blocks.to_features()
        assert lots.unit_type == "lot"
        assert blocks.unit_type == "block"
        pd.testing.assert_frame_equal(a, b)
        events.append("root")
        if fail_handler:
            message = "handler failure"
            raise RuntimeError(message)
        return a

    runner = LocalRunner.from_definitions(
        {
            "metrics": PluginDefinition(
                metrics=[root, source], resources={"support": Support}
            ),
            "geography": PluginDefinition(
                resources={"locations": ConfiguredProvider},
                location_provider="locations",
            ),
        },
        resource_config={"locations": ResourceConfig(config={"label": "fake"})},
        resource_overrides={"locations": FakeProvider},
        cache_dir=tmp_path,
    )
    request = {
        "lots": selection("a", "a").model_dump(),
        "blocks": selection("a", unit_type="block"),
    }
    with pytest.raises(GraphExecutionError) if fail_handler else nullcontext():
        assert runner.run("root", request)["root"].value.tolist() == [1]
    assert events == [
        ("setup", "fake"),
        ("test:units", "lot", ["a"]),
        ("test:units", "block", ["a"]),
        "support setup",
        ("source", ["a"]),
        ("source", ["a"]),
        "root",
        "support closed",
        "provider closed",
    ]
    if not fail_handler:
        events.clear()
        runner.run("root", request)
        assert events == [
            ("setup", "fake"),
            ("test:units", "lot", ["a"]),
            ("test:units", "block", ["a"]),
            "provider closed",
        ]
        events.clear()
        with pytest.raises(GraphExecutionError) as error:
            runner.run("root", request | {"lots": selection("a", unit_type="wrong")})
        assert isinstance(error.value.__cause__, LocationSelectionError)
        assert events == []


@pytest.mark.parametrize(
    "invalid",
    [
        "unknown",
        "wrong_id_type",
        "empty",
        "unit_type",
        "geometry",
        "model",
        "extra",
        "missing",
        "parameters",
    ],
)
def test_rejects_before_handler(invalid: str) -> None:
    events: list[str] = []

    class FakeProvider(Provider):
        @override
        def setup(self) -> None:
            events.append("setup")

        @override
        def resolve(
            self, collection: str, unit_type: str, ids: list[str]
        ) -> gpd.GeoDataFrame:
            events.append("resolve")
            if "unknown" in ids or ("block_id" in ids and unit_type == "lot"):
                raise UnresolvedLocationIDsError(collection, unit_type, ids)
            return producer_result(
                Locations.from_dict(
                    features(ids).to_dict()
                    | {"collection": collection, "unit_type": unit_type}
                ),
                self.outputs[collection, unit_type],
            )

        @override
        def close(self) -> None:
            events.append("close")

    @metric(name="root", description="Root", outputs={"root": TABLE})
    def root(
        lots: Locations, blocks: Locations, parameters: Parameters
    ) -> pd.DataFrame:
        pytest.fail(
            f"Invalid requests must never execute: {lots}, {blocks}, {parameters}"
        )

    runner = LocalRunner.from_definitions(
        {
            "plugin": PluginDefinition(
                metrics=[root],
                resources={"locations": Provider},
                location_provider="locations",
            )
        },
        resource_overrides={"locations": FakeProvider},
    )
    bad = {
        "unknown": selection("unknown"),
        "wrong_id_type": selection("block_id"),
        "empty": {"collection": "test:units", "unit_type": "lot", "ids": []},
        "unit_type": selection("a", unit_type="wrong"),
        "geometry": features(["a"]).to_dict(),
        "model": features(["a"]),
    }.get(invalid, selection("a"))
    request: dict[str, object] = {
        "lots": selection("a"),
        "blocks": bad,
        "parameters": {},
    }
    if invalid == "extra":
        request["extra"] = 1
    if invalid == "missing":
        del request["blocks"]
    if invalid == "parameters":
        request["parameters"] = {"count": "bad"}
    execution_failure = invalid in {"unknown", "wrong_id_type", "unit_type"}
    with pytest.raises(
        GraphExecutionError
        if execution_failure
        else (LocationSelectionError, MetricInputError)
    ) as error:
        runner.run("root", request)
    if execution_failure:
        assert isinstance(error.value, GraphExecutionError)
        assert error.value.node == "root"
        assert error.value.path == ("root",)
        cause = error.value.__cause__
        assert isinstance(cause, (LocationSelectionError, UnresolvedLocationIDsError))
        assert cause.node == "root"
        assert cause.input_name == "blocks"
    assert events == (
        ["setup", "resolve", "resolve", "close"]
        if invalid in {"unknown", "wrong_id_type"}
        else []
    )


def test_resolution_shares_provider_and_content_controls_cache(tmp_path: Path) -> None:
    events: list[str] = []
    instances: list[Provider] = []
    coordinate = 1

    class SharedProvider(Provider):
        @override
        def setup(self) -> None:
            events.append("setup")
            instances.append(self)

        @override
        def resolve(
            self, collection: str, unit_type: str, ids: list[str]
        ) -> gpd.GeoDataFrame:
            events.append("resolve")
            value = features(ids)
            rows = value.to_features()
            assert isinstance(rows[0].geometry, PointGeometry)
            rows[0].geometry.coordinates = [coordinate, 2]
            value = Locations.from_dict(value.to_dict() | {"features": rows})
            return producer_result(value, self.outputs[collection, unit_type])

        @override
        def close(self) -> None:
            events.append("close")

    class Unused(BaseIO[IOConfig]):
        @override
        def setup(self) -> None:
            pytest.fail("Unused resources must remain unacquired")

    @metric(name="root", description="Root", outputs={"root": TABLE})
    def root(
        location: Locations,
        provider: Annotated[Provider, Resource("locations")],
        parameters: Parameters,
    ) -> pd.DataFrame:
        assert provider is instances[-1]
        assert "close" not in events
        assert parameters.count == 2
        events.append("handler")
        geometry = location.to_features()[0].geometry
        assert isinstance(geometry, PointGeometry)
        return pd.DataFrame({"value": [int(geometry.coordinates[0])]})

    runner = LocalRunner.from_definitions(
        {
            "plugin": PluginDefinition(
                metrics=[root],
                resources={"locations": SharedProvider, "unused": Unused},
                location_provider="locations",
            )
        },
        cache_dir=tmp_path,
    )
    request = {"location": selection("a"), "parameters": {}}
    for expected_events, expected_value in [
        (["setup", "resolve", "handler", "close"], 1),
        (["setup", "resolve", "close"], 1),
        (["setup", "resolve", "handler", "close"], 3),
    ]:
        coordinate = expected_value
        events.clear()
        assert runner.run("root", request)["root"].value.tolist() == [expected_value]
        assert events == expected_events
    assert len(instances) == 3


@pytest.mark.parametrize("failure", ["setup", "resolve", "result"])
def test_initial_resolution_failure_context_and_cleanup(failure: str) -> None:
    events: list[str] = []

    class FailingProvider(Provider):
        @override
        def setup(self) -> None:
            events.append("setup")
            if failure == "setup":
                message = "private credentials"
                raise RuntimeError(message)

        @override
        def resolve(
            self, collection: str, unit_type: str, ids: list[str]
        ) -> gpd.GeoDataFrame:
            events.append("resolve")
            if failure == "resolve":
                message = "private credentials"
                raise RuntimeError(message)
            return producer_result(
                features(["unexpected"]), self.outputs[collection, unit_type]
            )

        @override
        def close(self) -> None:
            events.append("close")

    @metric(name="root", description="Root", outputs={"root": TABLE})
    def root(location: Locations) -> pd.DataFrame:
        pytest.fail(f"Handler must not run after resolution failure: {location}")

    runner = LocalRunner.from_definitions(
        {
            "plugin": PluginDefinition(
                metrics=[root],
                resources={"locations": FailingProvider},
                location_provider="locations",
            )
        }
    )
    with pytest.raises(GraphExecutionError) as error:
        runner.run("root", {"location": selection("a")})
    assert error.value.node == "root"
    assert error.value.path == ("root",)
    cause = error.value.__cause__
    assert isinstance(cause, (LocationAcquisitionError, LocationProviderContractError))
    assert cause.node == "root"
    assert cause.input_name == "location"
    assert "private credentials" not in str(error.value)
    assert events == (
        ["setup", "close"] if failure == "setup" else ["setup", "resolve", "close"]
    )


@pytest.mark.parametrize("revision", [None, " data-1 "])
def test_local_cache_revision(tmp_path: Path, revision: str | None) -> None:
    calls = []

    @metric(name="root", description="Root", outputs={"answer": TABLE})
    def root() -> pd.DataFrame:
        calls.append("root")
        return pd.DataFrame({"value": [7]})

    runner = LocalRunner.from_definitions(
        {"local": PluginDefinition(metrics=[root])}, cache_dir=tmp_path
    )
    for value, count in [(revision, 1), (revision, 1), ("changed", 2), (revision, 2)]:
        runner.run("root", {}, cache_revision=value)
        assert len(calls) == count


@pytest.mark.parametrize("value", ["", "  ", "x" * 257, 1, True, [], {}])
def test_invalid_cache_revision(value: object) -> None:

    with pytest.raises(ValidationError):
        TypeAdapter(CacheRevision).validate_python(value)
    with pytest.raises(ValidationError):
        JobCreateRequest.model_validate(
            {"metric": "root", "input": {}, "cache_revision": value}
        )
