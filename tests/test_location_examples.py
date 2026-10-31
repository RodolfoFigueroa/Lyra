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
from lyra.sdk import LocalRunner, Locations, ResourceConfig, plugin_loader
from lyra.sdk.execution import GraphExecutionError
from lyra.sdk.graph import build_graph
from lyra.sdk.models.job import JobCreateRequest
from lyra.sdk.models.plugin import PluginInfo
from lyra.sdk.plugin_cli import check_manifest, render_description
from lyra.sdk.results import serialize_outputs

from lyra_app import job_submission, registry, worker
from lyra_app.plugins import InstalledPlugin
from tests.config_helpers import load_test_config, worker_credentials
from tests.runtime_helpers import make_runtime
from tests.test_worker_graph_integration import native_job

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


def selection(unit: str, *ids: str, collection: str = "city:entities") -> dict:
    return {"collection": collection, "unit_type": unit, "ids": list(ids)}


def test_registration_and_request_samples(example: ModuleType, tmp_path: Path) -> None:
    plugin = example.create_plugin()
    assert check_manifest(EXAMPLE) == (True, "")
    inspected = json.loads(render_description(EXAMPLE, json_output=True))
    assert {
        node["name"]
        for node in inspected["nodes"]
        if node["kind"] == "location_operation"
    } == {"select_streets", "sites_from_table", "site_buffers"}
    assert set(plugin.describe("block_score").request_schema["properties"]) == {
        "blocks"
    }
    graph = build_graph({"example": plugin.node_manifests})
    assert (
        graph.dependencies["block_score"]["members"].output.producer.node.name
        == "street_membership"
    )
    assert (
        graph.dependencies["select_streets"]["members"].output.producer.node.name
        == "street_membership"
    )
    assert len(graph.location_dependencies["block_score"]) == 2
    runner = LocalRunner.from_definitions({"example": plugin}, cache_dir=tmp_path)
    samples = json.loads((EXAMPLE / "requests.json").read_text())
    outputs = [runner.run(sample["metric"], sample["input"]) for sample in samples]
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
    resolved: list[Locations] = []
    original = example.CityProvider.resolve

    def resolve(
        provider: object, collection: str, unit_type: str, requested: list[str]
    ) -> Locations:
        result = original(provider, collection, unit_type, requested)
        resolved.append(
            example.SHAPES.build(result, collection=collection, unit_type=unit_type)
        )
        return result

    monkeypatch.setattr(example.CityProvider, "resolve", resolve)
    runner = LocalRunner.from_definitions(
        {"example": example.create_plugin()},
        cache_dir=tmp_path,
        resource_config={"city": ResourceConfig(config={"origin": 100.0})},
    )
    result = runner.run("block_score", {"blocks": selection("block", *ids)})[
        "block_scores"
    ]
    assert result.id.tolist() == ids
    assert result.score.tolist() == expected
    streets = [value for value in resolved if value.unit_type == "street"]
    # Both receiving arguments use the equivalent operation; resolve executes once.
    assert len(streets) == (0 if ids == ["b-empty"] else 1)
    if streets:
        assert streets[0].collection == "city:entities"
        assert streets[0].ids == (["s1", "s2"] if ids == ["b1"] else ["s1", "s2", "s3"])
        assert streets[0].to_geodataframe().geometry.iloc[0].bounds == (
            102.0,
            0.0,
            102.0,
            10.0,
        )
        direct = runner.run(
            "street_score",
            {
                "streets": selection("street", *streets[0].ids),
                "surroundings": selection("street", *streets[0].ids),
            },
        )["street_scores"]
        assert direct.id.tolist() == streets[0].ids
        assert direct.score.tolist() == [float(len(streets[0].ids))] * len(
            streets[0].ids
        )


def test_context_changes_scores_without_expanding_results(
    example: ModuleType, tmp_path: Path
) -> None:
    runner = LocalRunner.from_definitions(
        {"example": example.create_plugin()}, cache_dir=tmp_path
    )
    for context_ids in (["s1"], ["s1", "s2", "s3"]):
        result = runner.run(
            "street_score",
            {
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
    rows = example.site_source()
    original = rows.copy(deep=True)
    sites = example.SHAPES.build(
        example.sites_from_table(rows), collection="analysis:sites", unit_type="site"
    )
    buffers = example.SHAPES.build(
        example.site_buffers(sites), collection="analysis:buffers", unit_type="buffer"
    )
    assert sites.collection == "analysis:sites"
    assert buffers.collection == "analysis:buffers"
    assert sites.unit_type == "site"
    assert buffers.unit_type == "buffer"
    assert sites.ids == buffers.ids == ["site-a", "site-b"]
    assert sites.to_features()[0].geometry.type == "Point"
    assert buffers.to_features()[0].geometry.type == "Polygon"
    pd.testing.assert_frame_equal(rows, original)
    empty = example.SHAPES.build(
        example.sites_from_table(rows.iloc[:0]),
        collection="analysis:sites",
        unit_type="site",
    )
    assert empty.ids == []
    assert example.buffer_area(
        example.SHAPES.build(
            example.site_buffers(empty), collection="analysis:sites", unit_type="site"
        )
    ).empty
    runner = LocalRunner.from_definitions(
        {"example": example.create_plugin()}, cache_dir=tmp_path
    )
    runner.run("site_coverage", {})
    for collection, unit, metric, argument in [
        ("analysis:sites", "site", "street_score", "streets"),
        ("analysis:buffers", "buffer", "buffer_area", "buffers"),
    ]:
        inputs = {argument: selection(unit, "site-a", collection=collection)}
        if metric == "street_score":
            inputs["surroundings"] = selection("street", "s1")
        with pytest.raises(GraphExecutionError, match="collection"):
            runner.run(metric, inputs)


@pytest.mark.usefixtures("worker_cleanup")
def test_examples_through_submission_and_fake_worker(
    example: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = example.create_plugin()
    manifest = plugin.manifest(
        plugin=PluginInfo(name="example", version="1"),
        factory="location_plugin:create_plugin",
    )
    installed = [
        InstalledPlugin(
            "example", manifest, dict.fromkeys(plugin.metric_names, "heavy")
        )
    ]
    monkeypatch.setattr(worker, "load_installed_plugins", lambda _: installed)
    monkeypatch.setattr(registry, "load_installed_plugins", lambda _: installed)
    monkeypatch.setattr(plugin_loader, "load_plugin_definition", lambda _: plugin)
    config = load_test_config(tmp_path)
    config.cache_dir = tmp_path / "cache"
    runtime = make_runtime(config)
    worker.refresh_runner_registry(
        "heavy", config=config, credentials=worker_credentials()
    )
    worker.set_runner_temp_base(tmp_path / "scratch")
    enqueue = Mock()
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    local = LocalRunner.from_definitions(
        {"example": plugin}, cache_dir=tmp_path / "local"
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
        envelope, provenance, _ = enqueue.call_args.args
        assert envelope.input == sample["input"]
        native.meta["provenance"] = provenance.model_dump(mode="json")
        result = worker.run_metric_task(envelope.model_dump(mode="json"))
        expected = local.run(sample["metric"], sample["input"])
        assert result["outputs"] == serialize_outputs(sample["metric"], expected)
