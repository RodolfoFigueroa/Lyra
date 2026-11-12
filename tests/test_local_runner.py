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
from pydantic import Field, TypeAdapter, ValidationError
from shapely.geometry import Point
from typing_extensions import override

from lyra.contracts.dependencies import Input
from lyra.contracts.errors import (
    LocationAcquisitionError,
    LocationProviderContractError,
    LocationSelectionError,
    MetricInputError,
    ResourceConfigurationError,
    UnresolvedLocationIDsError,
)
from lyra.contracts.geometry import PointGeometry
from lyra.contracts.jobs import JobCreateRequest
from lyra.contracts.resources import IOConfig, ResourceConfig
from lyra.contracts.revisions import CacheRevision
from lyra.data.locations import (
    Locations,  # ruff: ignore[typing-only-first-party-import]
)
from lyra.runtime.execution import GraphExecutionError
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.preparation import PreparationError, prepare_execution
from lyra.sdk.context import RunContext
from lyra.sdk.dependencies import Dependency
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition, metric
from lyra.sdk.resources import BaseIO, Resource
from test_support.canonical_execution import canonical_workload
from test_support.location_helpers import (
    fixture_locations,
    node_contract,
    output_schema,
    producer_result,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path
    from types import ModuleType

    from lyra.contracts.locations import LocationContract

from test_support.location_resolution import Provider, features, selection

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

    @metric(
        code_revision="1",
        name="source",
        description="source",
        outputs={"a": TABLE, "b": TABLE},
    )
    def source(parameters: Parameters, context: RunContext) -> dict[str, pd.DataFrame]:
        contexts.append(context)
        assert context.temp_dir.is_dir()
        context.report_progress(stage="read", current=1, total=1)
        frame = pd.DataFrame({"value": [parameters.count]}, index=[42])
        frames.append(frame)
        return {"a": frame, "b": frame}

    @metric(
        code_revision="1",
        name="root",
        description="root",
        inputs={
            "a": Dependency("a", producer="source"),
            "b": Dependency("b", producer="source"),
        },
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
            result = runner.run("root", inputs={}, parameters={"count": index})
            pd.testing.assert_frame_equal(
                result["result"], frames[index].reset_index(drop=True)
            )
            assert result["result"].index.tolist() == [0]
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

    @metric(
        code_revision="1",
        name="source",
        description="source",
        outputs={"source": TABLE},
    )
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
        code_revision="1",
        name="root",
        description="root",
        inputs={"data": Dependency("source", producer="source")},
        outputs={"root": TABLE},
    )
    def root(data: pd.DataFrame) -> pd.DataFrame:
        return data

    runner = LocalRunner.from_definitions(
        {"plugin": PluginDefinition(metrics=[root, source])}
    )
    with pytest.raises(GraphExecutionError) as error:
        runner.run("root", inputs={})
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
        code_revision="1",
        locations={"location": node_contract()},
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
        inputs={"location": selection("area").model_dump()},
    )
    pd.testing.assert_frame_equal(result["root"], frame.reset_index(drop=True))
    factory.assert_called_once_with("root")
    assert tmp_path.exists()
    with pytest.raises(PreparationError, match="Unknown public metric"):
        runner.run("unknown", inputs={})
    contract = definition.node_manifests[0]
    contract.description = "changed"
    assert definition.node_manifests[0].description == "root"


def test_installed_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    @metric(code_revision="1", name="root", description="root", outputs={"root": TABLE})
    def root() -> pd.DataFrame:
        return pd.DataFrame({"value": [1]})

    definition = PluginDefinition(metrics=[root])
    monkeypatch.setattr(
        "lyra.runtime.local.load_installed_plugin_definition", lambda _: definition
    )
    assert LocalRunner(["Local_Plugin"]).run("root", inputs={})[
        "root"
    ].value.tolist() == [1]
    with pytest.raises(ValueError, match="Duplicate"):
        LocalRunner(["local-plugin", "Local_Plugin"])


def test_no_application_imports(monkeypatch: pytest.MonkeyPatch) -> None:
    original = builtins.__import__

    def guarded(
        name: str,
        module_globals: Mapping[str, object] | None = None,
        module_locals: Mapping[str, object] | None = None,
        fromlist: Sequence[str] | None = (),
        level: int = 0,
    ) -> ModuleType:
        if name.startswith("lyra.server"):
            pytest.fail("Local execution must not import the application")
        return original(name, module_globals, module_locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded)

    @metric(
        code_revision="1",
        name="source",
        description="source",
        outputs={"source": TABLE},
    )
    def source() -> pd.DataFrame:
        return pd.DataFrame({"value": pd.Series(dtype="int64")})

    @metric(
        code_revision="1",
        name="root",
        description="root",
        inputs={"data": Dependency("source", producer="source")},
        outputs={"root": TABLE},
    )
    def root(data: pd.DataFrame) -> pd.DataFrame:
        return data

    runner = LocalRunner.from_definitions(
        {"plugin": PluginDefinition(metrics=[source, root])}
    )
    assert runner.run("root", inputs={})["root"].empty
    assert runner.run("source", inputs={})["source"].empty
    with pytest.raises(PreparationError, match="Unknown public metric"):
        runner.run("missing", inputs={})
    with pytest.raises(ValueError, match="at least one"):
        LocalRunner.from_definitions({})


def make_source(
    name: str,
    contract: LocationContract,
    support_type: type[BaseIO],
    events: list[object],
) -> Callable[..., pd.DataFrame]:
    @metric(
        locations={"location": contract},
        code_revision="1",
        name=name,
        description="Source",
        outputs={name: TABLE},
    )
    def source(
        location: Locations, support: Annotated[BaseIO, Resource("support")]
    ) -> pd.DataFrame:
        assert isinstance(support, support_type)
        events.append(("source", list(location.ids)))
        return pd.DataFrame({"value": [1]})

    return source


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
            self, contract: LocationContract, ids: list[str]
        ) -> gpd.GeoDataFrame:
            collection, unit_type = (
                contract.family.collection,
                contract.family.unit_type,
            )
            events.append((collection, unit_type, ids))
            return producer_result(
                fixture_locations(
                    features(ids).to_dict()
                    | {"collection": collection, "unit_type": unit_type},
                    schema=output_schema(original=str),
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

    source = make_source("source", Provider.contracts[0], Support, events)
    block_source = make_source("block_source", Provider.contracts[1], Support, events)

    @metric(
        locations={"lots": Provider.contracts[0], "blocks": Provider.contracts[1]},
        code_revision="1",
        name="root",
        description="Root",
        outputs={"root": TABLE},
        inputs={
            "a": Dependency("source", producer="source").bind(location=Input("lots")),
            "b": Dependency(
                "block_source",
                producer="block_source",
            ).bind(location=Input("blocks")),
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
                metrics=[root, source, block_source], resources={"support": Support}
            ),
            "geography": PluginDefinition(
                resources={"locations": ConfiguredProvider},
                location_provider="locations",
            ),
        },
        resource_config={"locations": ResourceConfig(config={"label": "fake"})},
        resource_overrides={"locations": FakeProvider},
        artifact_dir=tmp_path,
    )
    request = {
        "lots": selection("a", "a").model_dump(),
        "blocks": selection("a", unit_type="block").model_dump(),
    }
    with pytest.raises(GraphExecutionError) if fail_handler else nullcontext():
        assert runner.run("root", inputs=request)["root"].value.tolist() == [1]
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
        runner.run("root", inputs=request)
        assert events == [
            ("setup", "fake"),
            ("test:units", "lot", ["a"]),
            ("test:units", "block", ["a"]),
            "provider closed",
        ]
        events.clear()
        with pytest.raises(PreparationError, match="unsupported source"):
            runner.run(
                "root",
                inputs=request
                | {"lots": selection("a", unit_type="wrong").model_dump()},
            )
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
@pytest.mark.parametrize("method", ["run", "run_development"])
def test_rejects_before_handler(invalid: str, method: str) -> None:
    events: list[str] = []

    class FakeProvider(Provider):
        @override
        def setup(self) -> None:
            events.append("setup")

        @override
        def resolve(
            self, contract: LocationContract, ids: list[str]
        ) -> gpd.GeoDataFrame:
            collection, unit_type = (
                contract.family.collection,
                contract.family.unit_type,
            )
            events.append("resolve")
            if "unknown" in ids or ("block_id" in ids and unit_type == "lot"):
                raise UnresolvedLocationIDsError(collection, unit_type, ids)
            return producer_result(
                fixture_locations(
                    features(ids).to_dict()
                    | {"collection": collection, "unit_type": unit_type},
                    schema=output_schema(original=str),
                ),
                self.outputs[collection, unit_type],
            )

        @override
        def close(self) -> None:
            events.append("close")

    @metric(
        code_revision="1",
        locations={"lots": node_contract(), "blocks": node_contract()},
        name="root",
        description="Root",
        outputs={"root": TABLE},
    )
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
        "unknown": selection("unknown").model_dump(),
        "wrong_id_type": selection("block_id").model_dump(),
        "empty": {"collection": "test:units", "unit_type": "lot", "ids": []},
        "unit_type": selection("a", unit_type="wrong").model_dump(),
        "geometry": features(["a"]).to_dict(),
        "model": features(["a"]),
    }.get(invalid, selection("a").model_dump())
    request: dict[str, object] = {
        "lots": selection("a").model_dump(),
        "blocks": bad,
    }
    if invalid == "extra":
        request["extra"] = 1
    if invalid == "missing":
        del request["blocks"]
    parameters = {}
    if invalid == "parameters":
        parameters = {"count": "bad"}
    execution_failure = invalid in {"unknown", "wrong_id_type"}
    with pytest.raises(
        GraphExecutionError
        if execution_failure
        else (LocationSelectionError, MetricInputError)
    ) as error:
        getattr(runner, method)("root", inputs=request, parameters=parameters)
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
            self, contract: LocationContract, ids: list[str]
        ) -> gpd.GeoDataFrame:
            collection, unit_type = (
                contract.family.collection,
                contract.family.unit_type,
            )
            events.append("resolve")
            value = features(ids)
            rows = value.to_features()
            assert isinstance(rows[0].geometry, PointGeometry)
            rows[0].geometry.coordinates = [coordinate, 2]
            value = fixture_locations(
                value.to_dict() | {"features": rows}, schema=output_schema(original=str)
            )
            return producer_result(value, self.outputs[collection, unit_type])

        @override
        def close(self) -> None:
            events.append("close")

    class Unused(BaseIO[IOConfig]):
        @override
        def setup(self) -> None:
            pytest.fail("Unused resources must remain unacquired")

    @metric(
        code_revision="1",
        locations={"location": node_contract()},
        name="root",
        description="Root",
        outputs={"root": TABLE},
    )
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
        artifact_dir=tmp_path,
    )
    request = {"location": selection("a").model_dump()}
    for expected_events, expected_value in [
        (["setup", "resolve", "handler", "close"], 1),
        (["setup", "resolve", "close"], 1),
        (["setup", "resolve", "handler", "close"], 3),
    ]:
        coordinate = expected_value
        events.clear()
        assert runner.run("root", inputs=request)["root"].value.tolist() == [
            expected_value
        ]
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
            self, contract: LocationContract, ids: list[str]
        ) -> gpd.GeoDataFrame:
            collection, unit_type = (
                contract.family.collection,
                contract.family.unit_type,
            )
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

    @metric(
        code_revision="1",
        locations={"location": node_contract()},
        name="root",
        description="Root",
        outputs={"root": TABLE},
    )
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
        runner.run("root", inputs={"location": selection("a").model_dump()})
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

    @metric(
        code_revision="1", name="root", description="Root", outputs={"answer": TABLE}
    )
    def root() -> pd.DataFrame:
        calls.append("root")
        return pd.DataFrame({"value": [7]})

    runner = LocalRunner.from_definitions(
        {"local": PluginDefinition(metrics=[root])}, artifact_dir=tmp_path
    )
    for value, count in [(revision, 1), (revision, 1), ("changed", 2), (revision, 2)]:
        runner.run("root", inputs={}, cache_revision=value)
        assert len(calls) == count


@pytest.mark.parametrize("value", ["", "  ", "x" * 257, 1, True, [], {}])
def test_invalid_cache_revision(value: object) -> None:

    with pytest.raises(ValidationError):
        TypeAdapter(CacheRevision).validate_python(value)
    with pytest.raises(ValidationError):
        JobCreateRequest.model_validate(
            {"metric": "root", "inputs": {}, "parameters": {}, "cache_revision": value}
        )


@pytest.mark.parametrize("routed", ["native", "routed"])
@pytest.mark.parametrize("inputs", [1, 2])
def test_public_canonical_inspection(tmp_path: Path, routed: str, inputs: int) -> None:
    work = canonical_workload(routed, 2, inputs)
    runner = LocalRunner.from_definitions(
        {name: binding.definition for name, binding in work.executor.plugins.items()},
        artifact_dir=tmp_path,
    )
    request = {
        item.name: selection(
            *item.ids,
            collection=item.binding.source.collection,
            unit_type=item.binding.source.unit_type,
        ).model_dump()
        for item in work.prepared.inputs
    }
    assert runner.inspect() is None
    for warm in (False, True):
        result = runner.run(work.prepared.metric, inputs=request)
        assert isinstance(result["result"], pd.DataFrame)
        assert len(result["result"]) == 2 * inputs
        inspection = runner.inspect()
        assert inspection is not None
        assert inspection.execution.succeeded
        assert inspection.accepted is not None
        assert len(inspection.execution.native_inputs) == inputs
        assert (
            any(o.outcome == "cache_hit" for o in inspection.execution.observations)
            == warm
        )
    with pytest.raises(PreparationError):
        runner.run("select", inputs=request)
    assert runner.inspect() is None


def test_private_development() -> None:
    @metric(
        code_revision="1",
        name="hidden",
        description="Hidden",
        private=True,
        outputs={"a": TABLE},
    )
    def hidden() -> pd.DataFrame:
        return pd.DataFrame({"value": [7]})

    runner = LocalRunner.from_definitions({"test": PluginDefinition(metrics=[hidden])})
    with pytest.raises(PreparationError):
        runner.run("hidden", inputs={})
    assert runner.run_development("hidden", inputs={})["a"].value.tolist() == [7]
    assert runner.inspect() is not None


def test_required_local_edition() -> None:
    @metric(
        code_revision="1",
        name="named",
        description="Score",
        outputs={"a": TABLE},
    )
    def named() -> pd.DataFrame:
        return pd.DataFrame({"value": [3]})

    runner = LocalRunner.from_definitions({"test": PluginDefinition(metrics=[named])})
    with pytest.raises(PreparationError):
        runner.run("score", inputs={})
    assert runner.run("named", inputs={})["a"].value.tolist() == [3]
    inspection = runner.inspect()
    assert inspection is not None
    assert inspection.accepted is not None
    assert inspection.accepted.metric == "named"


def test_local_unavailable_revision_inspection(monkeypatch: pytest.MonkeyPatch) -> None:
    work = canonical_workload("native", 1, 1)
    plugin = work.executor.plugins["bench"]
    monkeypatch.setattr(
        plugin.resources["locations"],
        "resolve",
        Mock(side_effect=ValueError("Unavailable revision")),
    )
    runner = LocalRunner.from_definitions({"bench": plugin.definition})
    with pytest.raises(GraphExecutionError, match="left") as error:
        runner.run(
            "one",
            inputs={
                "left": selection(
                    "0", collection="bench:target", unit_type="point"
                ).model_dump()
            },
        )
    cause = error.value.__cause__
    assert isinstance(cause, LocationAcquisitionError)
    assert str(cause.__cause__) == "Unavailable revision"
    assert cause.input_name == "left"
    assert cause.collection == "bench:target"
    inspection = runner.inspect()
    assert inspection == error.value.provenance
    assert inspection is not None
    assert not inspection.execution.succeeded


class SubmissionParameters(MetricParameters):
    count: int = Field(2, description="Count")
    options: dict[str, list[int | None]] = Field(
        default_factory=lambda: {"values": [None, 1]}, description="Options"
    )


class RequiredSubmissionParameters(SubmissionParameters):
    count: int = Field(description="Required count")


@pytest.mark.parametrize("method", ["run", "run_development"])
@pytest.mark.parametrize(
    "submission",
    [
        {},
        {"inputs": None, "parameters": None},
        {"inputs": {}, "parameters": {}},
        {"inputs": {}, "parameters": {"count": 2}},
        {"parameters": {"count": 5, "options": {"values": [None, 3]}}},
    ],
)
def test_local_submission_matches_remote_preparation(
    method: str, submission: dict
) -> None:
    @metric(
        code_revision="1", name="root", description="Root", outputs={"answer": TABLE}
    )
    def root(parameters: SubmissionParameters) -> pd.DataFrame:
        assert parameters.options["values"][0] is None
        return pd.DataFrame({"value": [parameters.count]})

    runner = LocalRunner.from_definitions({"test": PluginDefinition(metrics=[root])})
    remote = prepare_execution(
        runner.catalog,
        {"metric": "root", **{k: v for k, v in submission.items() if v is not None}},
    )
    result = getattr(runner, method)("root", **submission)
    assert result["answer"].value.tolist() == [remote.parameters["count"]]
    inspection = runner.inspect()
    assert inspection is not None
    assert inspection.accepted is not None
    assert inspection.accepted.parameters == remote.parameters


@pytest.mark.parametrize("method", ["run", "run_development"])
@pytest.mark.parametrize(
    "submission",
    [
        {},
        {"parameters": None},
        {"parameters": {}},
        {"parameters": {"count": 1, "unknown": 2}},
        {"inputs": {"unknown": {}}, "parameters": {"count": 1}},
        {"parameters": {"count": 1, "options": {"values": [float("nan")]}}},
        {"parameters": {"count": 1, "options": {"values": (1, 2)}}},
        {"parameters": {"count": 1}, "cache_revision": " "},
    ],
)
def test_local_submission_rejects_invalid_values(method: str, submission: dict) -> None:
    @metric(
        code_revision="1", name="root", description="Root", outputs={"answer": TABLE}
    )
    def root(parameters: RequiredSubmissionParameters) -> pd.DataFrame:
        pytest.fail(f"Invalid submission reached handler: {parameters}")

    runner = LocalRunner.from_definitions({"test": PluginDefinition(metrics=[root])})
    with pytest.raises(PreparationError):
        getattr(runner, method)("root", **submission)
    assert runner.inspect() is None


@pytest.mark.parametrize("method", ["run", "run_development"])
@pytest.mark.parametrize("submission", [{}, {"inputs": None}, {"inputs": {}}])
def test_required_selection_after_omission(method: str, submission: dict) -> None:
    @metric(
        code_revision="1",
        name="root",
        description="Root",
        locations={"location": node_contract()},
        outputs={"answer": TABLE},
    )
    def root(location: Locations) -> pd.DataFrame:
        pytest.fail(f"Missing selection reached handler: {location}")

    runner = LocalRunner.from_definitions({"test": PluginDefinition(metrics=[root])})
    with pytest.raises(PreparationError, match="Expected inputs"):
        getattr(runner, method)("root", **submission)
