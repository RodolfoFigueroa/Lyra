"""Declared reuse survives local, development, and normalized worker entry points."""

import asyncio
from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pytest

from lyra.contracts.jobs import JobEnvelope
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.preparation import PreparationError
from lyra.runtime.result_store import ResultStore
from lyra.runtime.results import RunResult
from lyra.sdk import Dependency, Input, LocationDependency, PluginDefinition, metric
from lyra.server import worker
from tests.frame_assertions import assert_logical_equal
from tests.metric_execution_helpers import schema
from tests.queue_helpers import read_retained
from tests.selector_reuse_helpers import selector_definitions
from tests.submission_execution_helpers import resources, submit
from tests.worker_graph_integration_helpers import install_definitions, native_job

pytestmark = pytest.mark.usefixtures("worker_state")


def comparison_definitions(events: list, work: list) -> dict[str, PluginDefinition]:
    memory, source, target = resources(events)
    target.cache = True

    @metric(
        name="areas",
        description="Independent areas",
        cache="per_location",
        inputs={"places": LocationDependency(provider="target", primary=True)},
        outputs={"rows": schema()},
    )
    def areas(places: gpd.GeoDataFrame) -> pd.DataFrame:
        work.append(("areas", set(places.location_id)))
        return pd.DataFrame(
            {"location_id": places.location_id, "row_key": "area", "value": 1}
        )

    @metric(
        name="comparison",
        description="Compare with complete references",
        cache="per_location",
        inputs={
            "places": LocationDependency(provider="target", primary=True),
            "reference": LocationDependency(provider="target"),
            "area": Dependency("rows", producer="areas"),
            "reference_area": Dependency("rows", producer="areas").bind(
                places=Input("reference")
            ),
        },
        outputs={"rows": schema()},
    )
    def comparison(
        places: gpd.GeoDataFrame,
        reference: gpd.GeoDataFrame,
        area: pd.DataFrame,
        reference_area: pd.DataFrame,
    ) -> pd.DataFrame:
        work.append(("comparison", set(places.location_id)))
        assert set(area.location_id) == set(places.location_id)
        assert (
            set(reference.location_id) == set(reference_area.location_id) == {"x", "y"}
        )
        return area.assign(value=area.value + len(reference_area))

    @metric(
        name="complete",
        description="Complete selection",
        cache="full",
        inputs={"places": LocationDependency(provider="target", primary=True)},
        outputs={"rows": schema()},
    )
    def complete(places: gpd.GeoDataFrame) -> pd.DataFrame:
        work.append(("complete", set(places.location_id)))
        return pd.DataFrame(
            {"location_id": places.location_id, "row_key": "full", "value": len(places)}
        )

    @metric(
        name="private",
        description="Private cached root",
        private=True,
        cache="full",
        inputs={"places": LocationDependency(provider="target", primary=True)},
        outputs={"rows": schema()},
    )
    def private(places: gpd.GeoDataFrame) -> pd.DataFrame:
        return complete(places)

    return {
        "test": PluginDefinition(
            metrics=[areas, comparison, complete, private],
            resources={"memory": memory, "source": source, "target": target},
        )
    }


def entry_point(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    definitions: dict[str, PluginDefinition],
    mode: str,
) -> Callable[..., RunResult]:
    """Use separate configured stores and fresh worker job metadata on every call."""
    if mode in {"local", "development"}:
        runner = LocalRunner.from_definitions(
            definitions, result_store_path=tmp_path / "local.sqlite"
        )
        return runner.run if mode == "local" else runner.run_development
    _, runtime = install_definitions(tmp_path, monkeypatch, definitions)
    worker.initialize_worker("lightweight", config=runtime.config)
    enqueue = Mock()
    runtime.jobs.enqueue = enqueue

    def run(name: str, *, inputs: dict, parameters: dict | None = None) -> RunResult:
        assert asyncio.run(
            submit(
                runtime,
                mode,
                {"metric": name, "inputs": inputs, "parameters": parameters or {}},
            )
        )
        envelope = JobEnvelope.model_validate_json(
            enqueue.call_args.args[0].model_dump_json()
        )
        native = native_job(monkeypatch, name)
        native.id = envelope.job_id
        result = worker.run_metric_task(envelope.model_dump(mode="json"))
        # Reuse must not need the previous job's execution observations.
        native.meta.clear()
        return read_retained(str(result["result_ref"]))

    return run


@pytest.mark.parametrize("mode", ["local", "development", "http", "mcp"])
@pytest.mark.parametrize("name", ["complete", "comparison"])
def test_metric_reuse_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, name: str
) -> None:
    events, work = [], []
    run = entry_point(tmp_path, monkeypatch, comparison_definitions(events, work), mode)
    results = []
    for ids in (["a", "b"], ["b", "c"], ["c", "b"]):
        events.clear()
        work.clear()
        inputs = {"places": {"provider": "target", "ids": ids}}
        if name == "comparison":
            inputs["reference"] = {"provider": "target", "ids": ["x", "y"]}
        results.append(run(name, inputs=inputs))
        expected_work = set(ids) if len(results) == 1 or name == "complete" else {"c"}
        if len(results) < 3:
            assert (name, expected_work) in work
            if name == "comparison" and len(results) == 2:
                assert work == [("areas", {"c"}), ("comparison", {"c"})]
            opened = [resource for event, resource in events if event == "open"]
            assert opened
            assert all(not resource.open for resource in opened)
            assert all(events.count(("close", resource)) == 1 for resource in opened)
        else:
            assert events == work == []
        if len(results) == 1:
            with ResultStore(
                worker.SQLiteStore(results[-1].metadata.store_path)
            ) as reader:
                reference = results[-1].metadata.result_ref
                assert set(
                    reader.read_location_input(reference, "places").location_id
                ) == set(ids)
                if name == "comparison":
                    assert set(
                        reader.read_location_input(reference, "reference").location_id
                    ) == {"x", "y"}
        owner = results[-1].metadata.outputs["rows"].owner_scope
        assert owner is not None
        assert owner.provider_id == "target"
        rows = results[-1].tables["rows"]
        assert set(rows.location_id) == set(ids)
        assert set(rows.value) == ({2} if name == "complete" else {3})
    assert len({result.metadata.result_ref for result in results}) == 3
    assert_logical_equal(results[1].tables["rows"], results[2].tables["rows"])


@pytest.mark.parametrize("mode", ["local", "development", "http", "mcp"])
@pytest.mark.parametrize("target_cache", [False, True])
def test_selector_reuse_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, *, target_cache: bool
) -> None:
    definitions, events, seen = selector_definitions(target_cache=target_cache)
    run = entry_point(tmp_path, monkeypatch, definitions, mode)
    references = set()
    for index, ids in enumerate((["a", "b"], ["b", "c"], ["c", "b"])):
        events.clear()
        result = run("consumer", inputs={"source": {"provider": "sources", "ids": ids}})
        references.add(result.metadata.result_ref)
        assert result.tables["result"].value.tolist() == [2]
        assert seen[-1] == {(key, f"{key}-2") for key in ids}
        callbacks = [
            event for event in events if event[0] in {"selector", "query", "mapping"}
        ]
        missing = {"a", "b"} if index == 0 else {"c"}
        assert callbacks == (
            []
            if index == 2
            else [
                ("selector", frozenset(missing)),
                ("query", frozenset(f"{key}-2" for key in missing)),
                ("mapping", frozenset(missing)),
            ]
        )
        exact = [event for event in events if event[0] == "exact"]
        assert exact == (
            []
            if target_cache or index == 0
            else [("exact", frozenset({"b-2"} if index == 1 else {"b-2", "c-2"}))]
        )
    assert len(references) == 3


def test_development_reuses_local_values_and_private_roots(tmp_path: Path) -> None:
    events, work = [], []
    runner = LocalRunner.from_definitions(
        comparison_definitions(events, work),
        result_store_path=tmp_path / "values.sqlite",
    )
    inputs = {"places": {"provider": "target", "ids": ["a", "b"]}}
    first = runner.run("complete", inputs=inputs)
    events.clear()
    work.clear()
    second = runner.run_development("complete", inputs=inputs)
    assert events == work == []
    assert first.metadata.result_ref != second.metadata.result_ref
    assert_logical_equal(first.tables["rows"], second.tables["rows"])
    private = runner.run_development("private", inputs=inputs)
    work.clear()
    repeated = runner.run_development("private", inputs=inputs)
    assert work == []
    assert private.metadata.result_ref != repeated.metadata.result_ref
    with pytest.raises(PreparationError, match="private"):
        runner.run("private", inputs=inputs)


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_submission_rejects_unexpected_field_before_enqueue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transport: str,
) -> None:
    events, work = [], []
    _, runtime = install_definitions(
        tmp_path, monkeypatch, comparison_definitions(events, work)
    )
    enqueue = Mock()
    runtime.jobs.enqueue = enqueue
    assert not asyncio.run(
        submit(
            runtime,
            transport,
            {
                "metric": "complete",
                "inputs": {"places": {"provider": "target", "ids": ["a"]}},
                "unexpected": True,
            },
        )
    )
    enqueue.assert_not_called()
    assert events == work == []
