"""Behavioral checks for executable capability authoring examples."""

import importlib
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pandas as pd
import pytest

from lyra.api.client.endpoints import RequestSpec, validate_response
from lyra.api.client.sync import LyraClient
from lyra.data.results import serialize_outputs
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.discovery import Discovery
from lyra.runtime.planning.preparation import PreparationError


@pytest.fixture
def example(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[1] / "examples/location-plugin")
    )
    return importlib.import_module("location_plugin.capabilities")


@pytest.fixture
def runner(example: ModuleType, tmp_path: Path) -> LocalRunner:
    return LocalRunner.from_definitions(
        {"city": example.create_plugin()}, artifact_dir=tmp_path
    )


@pytest.mark.parametrize(("edition", "score"), [("standard", 1.0), ("double", 2.0)])
def test_routing_preserves_method_and_granularity(
    example: ModuleType, runner: LocalRunner, edition: str, score: float
) -> None:
    inputs = {
        "streets": example.selection("block", "b2", "b1"),
        "surroundings": example.selection("street", "s1"),
    }
    result = runner.run(
        f"street_rating_{edition}",
        inputs=inputs,
    )["scores"]
    assert isinstance(result, pd.DataFrame)
    assert result.to_dict("records") == [
        {"id": identifier, "score": score} for identifier in ("s1", "s2", "s3")
    ]
    provenance = runner.inspect()
    assert provenance is not None
    assert provenance.accepted is not None
    assert provenance.accepted.inputs[0].binding.operations == ("member_streets",)
    assert [(item.name, item.count) for item in provenance.execution.native_inputs] == [
        ("streets", 3),
        ("surroundings", 1),
    ]
    assert any(
        item.node == f"context_score_{edition}" and item.outcome == "completed"
        for item in provenance.execution.observations
    )
    assert (
        sum(
            item.node == "member_streets" and item.outcome == "completed"
            for item in provenance.execution.observations
        )
        == 1
    )
    scopes = {
        item.contract.provider_scope.provider_id: item.contract.provider_scope
        for item in provenance.accepted.revisions.selection
        if item.contract.provider_scope is not None
    }
    assert scopes[example.BLOCKS].code_revision == "city-correction-1"
    assert {
        (origin.id, origin.revision) for origin in scopes[example.STREETS].origins
    } == {("city-survey", "2025")}
    inspection = Discovery(runner.catalog).inspect(f"street_rating_{edition}")
    assert inspection.bindings["streets"].provider_scope == scopes[example.STREETS]
    assert (
        inspection.outputs["scores"].granularity
        == example.STREET_SCORES.metadata["granularity"]
    )
    native = {**inputs, "streets": example.selection("street", "s1", "s2", "s3")}
    pd.testing.assert_frame_equal(
        result, runner.run(f"street_rating_{edition}", inputs=native)["scores"]
    )


def test_selection_rules_and_empty_intermediates(
    example: ModuleType, runner: LocalRunner
) -> None:
    discovery = Discovery(runner.catalog)
    assert {item.name for item in discovery.search()} == {
        "block_count",
        "street_rating_standard",
        "street_rating_double",
    }
    inputs = {
        "streets": example.selection("block", "b-empty"),
        "surroundings": example.selection("block", "b1"),
    }
    with pytest.raises(PreparationError, match="Unknown public metric"):
        runner.run(
            "street_rating",
            inputs=inputs,
        )
    with pytest.raises(PreparationError):
        runner.run(
            "context_score_standard",
            inputs=inputs,
        )
    assert runner.run(
        "street_rating_standard",
        inputs=inputs,
    )["scores"].empty
    inputs = {
        "streets": example.selection("block", "b1"),
        "surroundings": example.selection("block", "b-empty"),
    }
    assert runner.run(
        "street_rating_double",
        inputs=inputs,
    )["scores"].score.tolist() == [
        0.0,
        0.0,
    ]
    assert runner.run(
        "block_count", inputs={"blocks": example.selection("block", "b1")}
    )["scores"].score.tolist() == [1.0]
    with pytest.raises(PreparationError, match="provider"):
        runner.run("block_count", inputs={"blocks": example.selection("street", "s1")})


def test_run_wide_invalidation(example: ModuleType, runner: LocalRunner) -> None:
    inputs = {
        "streets": example.selection("block", "b1"),
        "surroundings": example.selection("street", "s1"),
    }
    for revision, outcome in [
        (None, "completed"),
        (None, "cache_hit"),
        ("refresh", "completed"),
        ("refresh", "cache_hit"),
        (None, "cache_hit"),
    ]:
        runner.run(
            "street_rating_standard",
            inputs=inputs,
            cache_revision=revision,
        )
        provenance = runner.inspect()
        assert provenance is not None
        assert provenance.accepted is not None
        assert provenance.accepted.cache_revision == revision
        for node in ("member_streets", "street_rating_standard"):
            assert any(
                item.node == node and item.outcome == outcome
                for item in provenance.execution.observations
            )


def test_remote_consumer_with_in_process_transport(
    runner: LocalRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote = importlib.import_module("location_plugin.remote")
    discovery = Discovery(runner.catalog)
    seen: list[RequestSpec[Any]] = []
    result: dict[str, Any] = {}
    statuses = iter(("queued", "running", "succeeded"))

    def respond(spec: RequestSpec[Any]) -> object:
        seen.append(spec)
        if spec.path == "metrics":
            body: object = [
                item.model_dump(mode="json")
                for item in discovery.search("street_rating")
            ]
        elif spec.path == "metrics/street_rating_double":
            body = discovery.inspect("street_rating_double").model_dump(mode="json")
        elif spec.path == "jobs":
            request = spec.json_body
            assert request is not None
            assert request["metric"] == "street_rating_double"
            outputs = runner.run(
                request["metric"],
                inputs=request["inputs"],
                parameters=request.get("parameters"),
                cache_revision=request["cache_revision"],
            )
            result.update(
                kind="table",
                job_id="example-job",
                status="succeeded",
                outputs=serialize_outputs("street_rating", outputs),
                output_metadata={
                    key: value.model_dump(mode="json")
                    for key, value in discovery.inspect(
                        "street_rating_double"
                    ).outputs.items()
                },
            )
            body = {
                "job_id": "example-job",
                "metric": "street_rating",
                "status": "queued",
                "links": {
                    "self": "/jobs/example-job",
                    "result": "/jobs/example-job/result",
                },
            }
        elif spec.path == "jobs/example-job":
            body = {
                "job_id": "example-job",
                "metric": "street_rating",
                "status": next(statuses),
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        else:
            assert spec.path == "jobs/example-job/result"
            body = result
        return validate_response(
            spec,
            202 if spec.method == "POST" else 200,
            json.dumps(body),
            "application/json",
            None,
        )

    def request(
        transport: object,
        spec: RequestSpec[Any],
        *,
        request_timeout: float | None = None,
    ) -> object:
        del transport, request_timeout
        return respond(spec)

    monkeypatch.setattr("lyra.api.client.sync.SyncHTTPTransport.request", request)
    with LyraClient("example.invalid") as client:
        output = remote.street_rating(client, metric="street_rating_double")
    assert output.outputs["scores"] == [
        {"id": identifier, "score": 2.0} for identifier in ("s1", "s2", "s3")
    ]
    assert seen[1].params is None
    assert sum(spec.path == "jobs/example-job" for spec in seen) == 3
    assert seen[-1].authenticated
