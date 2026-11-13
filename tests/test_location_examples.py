"""Execute the small location plugin through local and fake-worker orchestration."""

import asyncio
import importlib
import json
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pandas as pd
import pytest

from lyra.contracts.jobs import JobCreateRequest
from lyra.contracts.resources import IOConfig
from lyra.data.results import serialize_outputs
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.preparation import PreparationError
from lyra.sdk import loading
from lyra.sdk.plugin_cli import render_description, render_manifest
from lyra.server import job_submission, registry, worker
from lyra.server.plugins import InstalledPlugin
from test_support.manifest_graph import build_graph
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
        ).tables
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
def test_selection_membership_and_record_scores(
    example: ModuleType,
    tmp_path: Path,
    ids: list[str],
    expected: list[float],
) -> None:
    runner = LocalRunner.from_definitions(
        {"example": example.create_plugin()},
        artifact_dir=tmp_path,
    )
    result = runner.run(
        "block_score", inputs={"blocks": selection("block", *ids)}
    ).tables["block_scores"]
    assert result.id.tolist() == ids
    assert result.score.tolist() == expected
    expected_ids = (
        []
        if ids == ["b-empty"]
        else ["s1", "s2"]
        if ids == ["b1"]
        else ["s1", "s2", "s3"]
    )
    mapping = example.select_streets(example.BlockProvider(IOConfig(), {}).resolve(ids))
    assert set(mapping) == set(ids)
    assert {target for targets in mapping.values() for target in targets} == set(
        expected_ids
    )
    direct = runner.run(
        "street_score",
        inputs={
            "streets": selection("street", *expected_ids),
            "surroundings": selection("street", *expected_ids),
        },
    ).tables["street_scores"]
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
        ).tables["street_scores"]
        assert result.to_dict("records") == [
            {"id": "s1", "score": float(len(context_ids))}
        ]


def test_internal_construction_and_empty_conversion(
    example: ModuleType, tmp_path: Path
) -> None:
    sites = example.SiteProvider(IOConfig(), {}).resolve(["site-a", "site-b"])
    original = sites.copy(deep=True)
    buffers = example.site_buffers(sites)
    assert buffers == {"site-a": ("site-a",), "site-b": ("site-b",)}
    assert sites.geometry.geom_type.tolist() == ["Point", "Point"]
    pd.testing.assert_frame_equal(sites, original)
    assert example.site_buffers(sites.iloc[:0].copy()) == {}
    runner = LocalRunner.from_definitions(
        {"example": example.create_plugin()}, artifact_dir=tmp_path
    )
    assert (
        runner.run("site_coverage", inputs={"sites": selection("site")})
        .tables["coverage"]
        .empty
    )
    native = runner.run(
        "buffer_area", inputs={"buffers": selection("buffer", "site-a")}
    ).tables["buffer_areas"]
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
        ).tables
        assert result["outputs"] == serialize_outputs(sample["metric"], expected)


@pytest.mark.parametrize("development", [False, True])
@pytest.mark.parametrize("ids", [["b1", "b2"], ["b2", "b1"], []])
def test_hybrid_summary_metadata(
    example: ModuleType, tmp_path: Path, ids: list[str], *, development: bool
) -> None:
    del example
    hybrid = importlib.import_module("location_plugin.hybrid")
    runner = LocalRunner.from_definitions(
        {"hybrid": hybrid.create_plugin()}, artifact_dir=tmp_path
    )
    run = runner.run_development if development else runner.run
    inputs = {"blocks": selection("block", *ids)}
    area = run("block_area", inputs=inputs)
    assert set(area.tables["area"].location_id) == set(ids)
    assert area.tables["area"].row_key.tolist() == ["area"] * len(ids)
    owner = area.metadata.outputs["area"].owner_scope
    assert owner is not None
    assert owner.provider_id == "city_blocks"
    summary = run("area_summary", inputs=inputs)
    assert summary.metadata.metric_name == "area_summary"
    assert summary.metadata.parameters == {
        "minimum_area": 100.0,
        "include_requested": True,
    }
    assert set(summary.tables) == set(summary.metadata.outputs) == {"total", "count"}
    assert summary.tables["total"].value.tolist() == [100.0 * len(ids)]
    assert summary.tables["count"].value.tolist() == [float(len(ids))]
    assert all(
        output.scope == "unpartitioned" and output.owner_scope is None
        for output in summary.metadata.outputs.values()
    )
    empty = run(
        "area_summary",
        inputs=inputs,
        parameters={"minimum_area": 101, "include_requested": False},
    )
    assert empty.tables["total"].value.tolist() == [0.0]
    assert empty.tables["count"].value.tolist() == [0.0]
    assert summary.tables["total"].value.tolist() == [100.0 * len(ids)]


def test_shared_relationship_keys(example: ModuleType, tmp_path: Path) -> None:
    del example
    hybrid = importlib.import_module("location_plugin.hybrid")
    runner = LocalRunner.from_definitions(
        {"hybrid": hybrid.create_plugin()}, artifact_dir=tmp_path
    )
    result = runner.run(
        "street_relationships",
        inputs={"blocks": selection("block", "b1", "b2", "b-empty")},
    )
    table = result.tables["relationships"]
    assert set(zip(table.location_id, table.row_key, strict=True)) == {
        ("s1", "b1"),
        ("s2", "b1"),
        ("s2", "b2"),
        ("s3", "b2"),
    }
    assert table.length_m.tolist() == [10.0] * 4
    owner = result.metadata.outputs["relationships"].owner_scope
    assert owner is not None
    assert owner.provider_id == "city_streets"
    empty = runner.run(
        "street_relationships", inputs={"blocks": selection("block", "b-empty")}
    )
    assert empty.tables["relationships"].empty
    assert (
        empty.metadata.outputs["relationships"]
        == result.metadata.outputs["relationships"]
    )


@pytest.mark.parametrize("development", [False, True])
@pytest.mark.parametrize("ids", [["b1", "b2"], ["b2", "b1"], ["b-empty"], []])
@pytest.mark.parametrize("threshold", [100.0, 101.0])
def test_receiving_route_example(
    example: ModuleType, ids: list[str], threshold: float, *, development: bool
) -> None:
    del example
    receiving = importlib.import_module("location_plugin.receiving")
    plugin = receiving.create_plugin()
    runner = LocalRunner.from_definitions({"receiving": plugin})
    run = runner.run_development if development else runner.run
    result = run(
        "routed_street_summary",
        inputs={"blocks": selection("block", *ids)},
        parameters={"minimum_area": threshold},
        cache_revision="example",
    )
    length = 30.0 if "b1" in ids else 0.0
    assert result.tables["summary"].to_dict("records") == [
        {
            "all_length_m": length,
            "large_length_m": length if threshold <= 100.0 else 0.0,
        }
    ]
    assert result.metadata.parameters == {"minimum_area": threshold}
    assert result.metadata.outputs["summary"].owner_scope is None
    assert result.metadata.outputs["summary"].scope == "unpartitioned"
    operation = plugin.describe("large_blocks")
    assert operation.manifest.kind == "location_operation"
    assert operation.manifest.target_provider == "city_blocks"
    assert operation.manifest.parameters_schema is not None
    assert (
        operation.manifest.parameters_schema["properties"]
        == receiving.AreaParameters.model_json_schema()["properties"]
    )
    assert set(operation.manifest.request_schema["properties"]) == {
        "sources",
        "parameters",
    }
    # The same conversion also works at the public root, with target ownership.
    direct = run("street_lengths", inputs={"streets": selection("block", *ids)})
    table = direct.tables["lengths"]
    assert set(table.location_id) == ({"s1", "s2", "s3"} if length else set())
    assert table.row_key.tolist() == ["length"] * len(table)
    scope = direct.metadata.outputs["lengths"].owner_scope
    assert scope is not None
    assert scope.provider_id == "city_streets"
