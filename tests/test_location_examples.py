"""Execute the small location plugin through local and fake-worker orchestration."""

import asyncio
import importlib
import json
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pytest

from lyra.contracts.jobs import JobCreateRequest
from lyra.contracts.resources import IOConfig
from lyra.data.results import serialize_outputs
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.graph import build_graph
from lyra.runtime.planning.preparation import PreparationError
from lyra.sdk import loading
from lyra.sdk.plugin_cli import render_description, render_manifest
from lyra.server import job_submission, registry, worker
from lyra.server.plugins import InstalledPlugin
from tests.config_helpers import load_test_config
from tests.queue_helpers import run_and_read, submitted_input
from tests.runtime_helpers import make_runtime
from tests.worker_graph_integration_helpers import native_job

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/location-plugin"


@pytest.fixture
def example(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(EXAMPLE))
    return importlib.import_module("location_plugin.graph")


@pytest.fixture
def worker_cleanup() -> Iterator[None]:
    yield
    worker.RUNNER_REGISTRY.clear()
    worker.set_runner_temp_base(None)


def selection(unit: str, *ids: str) -> dict:
    return {
        "provider": {
            "block": "city_blocks",
            "street": "city_streets",
            "site": "analysis_sites",
            "buffer": "analysis_buffers",
        }[unit],
        "ids": list(ids),
    }


def test_registration_and_request_samples(example: ModuleType, tmp_path: Path) -> None:
    plugin = example.create_plugin()
    assert render_manifest(EXAMPLE)
    inspected = json.loads(render_description(EXAMPLE, json_output=True))
    assert {
        node["manifest"]["name"]
        for node in inspected["nodes"]
        if node["manifest"]["kind"] == "location_operation"
    } == {"select_streets", "site_buffers"}
    assert set(
        plugin.describe("block_score").manifest.request_schema["properties"]
    ) == {"blocks"}
    graph = build_graph({"example": plugin.node_manifests})
    assert (
        graph.dependencies["block_score"]["members"].output.producer.node.name
        == "street_membership"
    )
    assert not graph.dependencies["select_streets"]
    assert len(graph.location_dependencies["block_score"]) == 2
    runner = LocalRunner.from_definitions({"example": plugin}, artifact_dir=tmp_path)
    samples = json.loads((EXAMPLE / "requests.json").read_text())
    outputs = [
        runner.run(
            sample["metric"],
            inputs=sample["inputs"],
            parameters=sample.get("parameters"),
        )
        for sample in samples
    ]
    assert outputs[0]["street_scores"].to_dict("records") == [
        {"id": "s1", "score": 3.0}
    ]
    assert outputs[1]["block_scores"].to_dict("records") == [
        {"id": "b1", "score": 4.5},
        {"id": "b2", "score": 4.5},
        {"id": "b-empty", "score": 0.0},
    ]
    assert outputs[2]["coverage"].id.tolist() == ["site-a", "site-b"]
    assert outputs[2]["coverage"].area_m2.tolist() == pytest.approx(
        [313.654849, 313.654849]
    )


@pytest.mark.parametrize(
    ("ids", "expected"),
    [(["b1"], [3.0]), (["b2", "b1"], [4.5, 4.5]), (["b-empty"], [0.0])],
)
def test_selection_identity_and_reuse(
    example: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ids: list[str],
    expected: list[float],
) -> None:
    selected: list[gpd.GeoDataFrame] = []
    original = example.selected_street_records

    def select(blocks: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        result = original(blocks)
        selected.append(result.copy(deep=True))
        return result

    monkeypatch.setattr(example, "selected_street_records", select)
    runner = LocalRunner.from_definitions(
        {"example": example.create_plugin()},
        artifact_dir=tmp_path,
    )
    result = runner.run("block_score", inputs={"blocks": selection("block", *ids)})[
        "block_scores"
    ]
    assert result.id.tolist() == ids
    assert result.score.tolist() == expected
    # Equivalent derived inputs share the selector result within a run.
    assert len(selected) == 1
    expected_ids = (
        []
        if ids == ["b-empty"]
        else ["s1", "s2"]
        if ids == ["b1"]
        else ["s1", "s2", "s3"]
    )
    assert selected[0].location_id.tolist() == expected_ids
    if expected_ids:
        assert selected[0].geometry.iloc[0].bounds == (2.0, 0.0, 2.0, 10.0)
    direct = runner.run(
        "street_score",
        inputs={
            "streets": selection("street", *expected_ids),
            "surroundings": selection("street", *expected_ids),
        },
    )["street_scores"]
    assert direct.id.tolist() == expected_ids
    assert direct.score.tolist() == [float(len(expected_ids))] * len(expected_ids)


def test_context_changes_scores_without_expanding_results(
    example: ModuleType, tmp_path: Path
) -> None:
    runner = LocalRunner.from_definitions(
        {"example": example.create_plugin()}, artifact_dir=tmp_path
    )
    for context_ids in (["s1"], ["s1", "s2", "s3"]):
        result = runner.run(
            "street_score",
            inputs={
                "streets": selection("street", "s1"),
                "surroundings": selection("street", *context_ids),
            },
        )["street_scores"]
        assert result.to_dict("records") == [
            {"id": "s1", "score": float(len(context_ids))}
        ]


def test_internal_construction_and_empty_conversion(
    example: ModuleType, tmp_path: Path
) -> None:
    sites = example.SiteProvider(IOConfig(), {}).resolve(["site-a", "site-b"])
    original = sites.copy(deep=True)
    buffers = example.site_buffers(sites)
    assert (
        sites.location_id.tolist()
        == buffers.location_id.tolist()
        == ["site-a", "site-b"]
    )
    assert sites.geometry.geom_type.tolist() == ["Point", "Point"]
    assert buffers.geometry.geom_type.tolist() == ["Polygon", "Polygon"]
    pd.testing.assert_frame_equal(sites, original)
    empty = sites.iloc[:0].copy()
    assert example.buffer_area(example.site_buffers(empty)).empty
    runner = LocalRunner.from_definitions(
        {"example": example.create_plugin()}, artifact_dir=tmp_path
    )
    assert runner.run("site_coverage", inputs={"sites": selection("site")})[
        "coverage"
    ].empty
    native = runner.run(
        "buffer_area", inputs={"buffers": selection("buffer", "site-a")}
    )["buffer_areas"]
    assert native.area_m2.tolist() == pytest.approx([313.654849])
    with pytest.raises(PreparationError, match="provider"):
        runner.run(
            "street_score",
            inputs={
                "streets": selection("site", "site-a"),
                "surroundings": selection("street", "s1"),
            },
        )


@pytest.mark.usefixtures("worker_cleanup")
def test_examples_through_submission_and_fake_worker(
    example: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = example.create_plugin()
    installed = [
        InstalledPlugin("example", plugin, dict.fromkeys(plugin.metric_names, "heavy"))
    ]
    monkeypatch.setattr(worker, "load_installed_plugins", lambda _: installed)
    monkeypatch.setattr(registry, "load_installed_plugins", lambda _: installed)
    monkeypatch.setattr(loading, "load_plugin_definition", lambda _: plugin)
    config = load_test_config(tmp_path)
    config.artifact_dir = tmp_path / "cache"
    runtime = make_runtime(config)
    worker.refresh_runner_registry("heavy", config=config)
    worker.set_runner_temp_base(tmp_path / "scratch")
    enqueue = Mock()
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    local = LocalRunner.from_definitions(
        {"example": plugin}, artifact_dir=tmp_path / "local"
    )
    for sample in json.loads((EXAMPLE / "requests.json").read_text()):
        native = native_job(monkeypatch, sample["metric"])
        asyncio.run(
            job_submission.submit_job(
                JobCreateRequest.model_validate(sample),
                catalog=runtime.catalog,
                jobs=runtime.jobs,
                job_id_factory=lambda native=native: native.id,
            )
        )
        (envelope,) = enqueue.call_args.args
        assert submitted_input(envelope) == {
            **sample["inputs"],
            **({"parameters": sample["parameters"]} if "parameters" in sample else {}),
        }
        native.args = (envelope.model_dump(mode="json"),)
        result = run_and_read(worker.run_metric_task, envelope.model_dump(mode="json"))
        expected = local.run(
            sample["metric"],
            inputs=sample["inputs"],
            parameters=sample.get("parameters"),
        )
        assert result["outputs"] == serialize_outputs(sample["metric"], expected)
