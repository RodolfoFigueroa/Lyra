"""Interventions pass through ordinary preparation and worker transport."""

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import Mock

import geopandas as gpd
import httpx
import pandas as pd
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from shapely.geometry import Point
from typing_extensions import override

from lyra.api import AsyncLyraClient, DownloadError
from lyra.contracts.accepted import ExecutionRequest, PreparedExecution, WorkerRequest
from lyra.contracts.jobs import JobCreateRequest, JobEnvelope
from lyra.contracts.origins import Origin
from lyra.data.intervention_records import accepted_additions
from lyra.data.interventions import Intervention
from lyra.runtime.execution import GraphExecutionError
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.preparation import PreparationError, prepare_execution
from lyra.sdk import LocationProvider, PluginDefinition, metric
from lyra.server import worker
from lyra.server.main import create_app
from test_support.location_schema import column
from tests.client_helpers import _job_response
from tests.config_helpers import api_credentials
from tests.provider_schema_helpers import declaration, frame
from tests.queue_helpers import read_retained
from tests.retrieval_client_helpers import NativeAPI, native_api
from tests.submission_execution_helpers import submit
from tests.worker_graph_integration_helpers import (
    VALUES,
    install_definitions,
    native_job,
)

if TYPE_CHECKING:
    from lyra.contracts.types import JsonValue

__all__ = ["native_api"]

pytestmark = pytest.mark.usefixtures("worker_state")


class Trees(LocationProvider):
    id = "trees"
    schema = declaration()
    code_revision = "tree-code"
    origins = (Origin(id="survey", revision="2025"),)

    @override
    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
        return frame().loc[lambda df: df.location_id.isin(ids)]

    @override
    def existing_ids(self, ids: list[str]) -> set[str]:
        return {"old"} & set(ids)


class Unsupported(LocationProvider):
    id = "unsupported"
    schema = declaration()

    @override
    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
        return frame().loc[lambda df: df.location_id.isin(ids)]


@metric(name="root", description="Root", outputs={"rows": VALUES})
def root() -> pd.DataFrame:
    return pd.DataFrame({"value": [1]})


@metric(name="private", private=True, description="Private", outputs={"rows": VALUES})
def private_metric() -> pd.DataFrame:
    return root()


def definitions(
    provider: type[LocationProvider] = Trees,
) -> dict[str, PluginDefinition]:
    return {
        "metrics": PluginDefinition(metrics=[root, private_metric]),
        "survey": PluginDefinition(
            resources={"survey_resource": provider, "other": Unsupported}
        ),
    }


def actions() -> dict:
    return {
        "catalog": "trees",
        "id": "proposal",
        "revision": "v1",
        "add": [
            {
                "location_id": f"{i:03}",
                "geometry": "POINT (1.2345678901234567 2)",
                "count": str(2**63 - 1) if i else None,
            }
            for i in range(30)
        ],
        "remove": ["old"],
    }


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_inline_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, transport: str
) -> None:
    _, runtime = install_definitions(tmp_path, monkeypatch, definitions())
    runtime.jobs.enqueue = Mock()
    change = actions()
    expected = deepcopy(change)
    request = {"metric": "root", "interventions": [change]}
    with monkeypatch.context() as guard:
        acquire = Mock(side_effect=AssertionError("preparation acquired a provider"))
        guard.setattr(Trees, "setup", acquire)
        assert asyncio.run(submit(runtime, transport, request))
        acquire.assert_not_called()
    change["add"].clear()
    change["remove"].append("mutated")
    request.clear()
    envelope = runtime.jobs.enqueue.call_args.args[0]
    wire = json.loads(envelope.model_dump_json())
    plan = JobEnvelope.model_validate(wire).accepted.plan
    assert plan.request.submission_payload()["interventions"] == [expected]
    bound = plan.provider_scopes["trees"].contract.provider_scope
    assert bound
    assert bound.code_revision == Trees.code_revision
    assert bound.origins == frozenset(Trees.origins)
    native = native_job(monkeypatch)
    native.id = envelope.job_id
    saved = worker.run_metric_task(wire)
    received = read_retained(str(saved["result_ref"])).report.request
    assert received == plan.request
    restored = accepted_additions(received.interventions[0])
    assert restored is not None
    assert restored.crs == "EPSG:4326"
    assert restored.location_id.tolist() == [f"{i:03}" for i in range(30)]
    assert pd.isna(restored["count"].iloc[0])
    assert restored["count"].iloc[-1] == 2**63 - 1
    assert restored.geometry.iloc[0] == Point(1.2345678901234567, 2)


def test_local_and_private_development_capture_native_values() -> None:
    records = gpd.GeoDataFrame(
        {
            "location_id": [f"{i:03}" for i in range(30)],
            "count": pd.Series([None, *([2**63 - 1] * 29)], dtype="Int64"),
        },
        geometry=[Point(1.2345678901234567, 2)] * 30,
        crs=4326,
    )
    removals = ["old"]
    proposal = Intervention(
        id="proposal", catalog="trees", revision="v1", add=records, remove=removals
    )
    local = LocalRunner.from_definitions(definitions())
    changes = [proposal]
    result = local.run("root", interventions=changes)
    development = local.run_development("private", interventions=changes)
    with pytest.raises(PreparationError, match="Unknown public metric"):
        local.run("private", interventions=changes)
    changes.clear()
    removals.clear()
    records.loc[0, "location_id"] = "changed"
    assert proposal.add is not None
    assert proposal.remove is not None
    proposal.add.loc[0, "count"] = 1
    proposal.remove.clear()
    plan = prepare_execution(
        local.catalog, {"metric": "root", "interventions": [actions()]}
    )
    assert result.report.request == plan.request
    assert development.report.request.interventions == plan.request.interventions


@pytest.mark.parametrize("transport", ["http", "mcp"])
def test_rejected_requests_never_enqueue_or_acquire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, transport: str
) -> None:
    _, runtime = install_definitions(tmp_path, monkeypatch, definitions())
    runtime.jobs.enqueue = Mock()
    acquire = Mock(side_effect=AssertionError("unexpected acquisition"))
    monkeypatch.setattr(LocationProvider, "setup", acquire)
    change = actions()
    for invalid in (
        None,
        [change, change],
        [change | {"catalog": "survey_resource"}],
        [change | {"catalog": "unsupported"}],
        [change | {"add": None}],
        [change | {"remove": None}],
        [change | {"path": "/client/local"}],
        [change | {"add": [{"location_id": "001"}]}],
        [change | {"add": [change["add"][0] | {"count": 7}]}],
        [change | {"add": [change["add"][0] | {"geometry": "POINT (1 2)\x00ignored"}]}],
        [change | {"add": [change["add"][0] | {"geometry": "POINT EMPTY"}]}],
    ):
        assert not asyncio.run(
            submit(runtime, transport, {"metric": "root", "interventions": invalid})
        )
    acquire.assert_not_called()
    runtime.jobs.enqueue.assert_not_called()


@pytest.mark.parametrize(
    "change", ["revision", "origin", "schema", "membership", "record"]
)
def test_worker_rejects_incompatible_unused_targets_before_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    _, runtime = install_definitions(tmp_path, monkeypatch, definitions())
    runtime.jobs.enqueue = Mock()
    # Removal-only requests still pin the target schema.
    value = actions()
    if change != "record":
        value.pop("add")
    assert asyncio.run(
        submit(runtime, "http", {"metric": "root", "interventions": [value]})
    )
    envelope = runtime.jobs.enqueue.call_args.args[0]
    payload = json.loads(envelope.model_dump_json())
    if change == "record":
        payload["accepted"]["plan"]["request"]["interventions"][0]["add"][0][
            "geometry"
        ] = "POINT EMPTY"
    else:

        class Changed(Trees):
            pass

        if change == "revision":
            Changed.code_revision = "different"
        elif change == "origin":
            Changed.origins = (Origin(id="survey", revision="2026"),)
        elif change == "schema":
            Changed.schema = declaration()
            Changed.schema.columns["count"].nullable = False
        else:
            monkeypatch.setattr(Changed, "existing_ids", LocationProvider.existing_ids)
        install_definitions(tmp_path, monkeypatch, definitions(Changed))

    acquire = Mock(
        side_effect=AssertionError("incompatible handoff acquired resources")
    )
    monkeypatch.setattr(LocationProvider, "setup", acquire)
    native_job(monkeypatch).id = envelope.job_id
    with pytest.raises(
        (worker.BoundPlanError, GraphExecutionError),
        match=r"provider scopes|pinned declaration|implementation|existing_ids|usable",
    ):
        worker.run_metric_task(payload)
    acquire.assert_not_called()


def test_plan_requires_consistent_target_and_record_schema() -> None:
    catalog = LocalRunner.from_definitions(definitions()).catalog
    plan = prepare_execution(catalog, {"metric": "root", "interventions": [actions()]})
    for problem in ("absent", "extra", "schema", "duplicate"):
        payload = plan.model_dump(mode="json")
        targets = payload["intervention_targets"]
        values = payload["request"]["interventions"]
        if problem == "absent":
            targets.clear()
        elif problem == "extra":
            targets["other"] = targets["trees"]
        elif problem == "schema":
            values[0]["addition_schema"]["columns"][0]["nullable"] = True
        else:
            values.append(values[0])
        with pytest.raises(ValidationError, match="pinned"):
            PreparedExecution.model_validate(payload)


def test_worker_request_owns_target_declarations() -> None:
    catalog = LocalRunner.from_definitions(definitions()).catalog
    plan = prepare_execution(catalog, {"metric": "root", "interventions": [actions()]})
    queued = WorkerRequest.from_plan(plan)
    restored = WorkerRequest.model_validate_json(queued.model_dump_json())
    assert restored.intervention_targets == plan.intervention_targets
    plan.intervention_targets["trees"].resource = "changed"
    assert queued.intervention_targets["trees"].resource == "survey_resource"


@pytest.mark.parametrize("change", ["resource", "extra", "legacy", "legacy_plain"])
def test_worker_checks_accepted_target_bindings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    plugins = definitions()
    _, runtime = install_definitions(tmp_path, monkeypatch, plugins)
    runtime.jobs.enqueue = Mock()
    changes = [] if change == "legacy_plain" else [actions()]
    assert asyncio.run(
        submit(runtime, "http", {"metric": "root", "interventions": changes})
    )
    envelope = runtime.jobs.enqueue.call_args.args[0]
    wire = json.loads(envelope.model_dump_json())
    queued = wire["accepted"]["plan"]
    if change == "resource":
        plugins["survey"] = PluginDefinition(resources={"replacement": Trees})
        install_definitions(tmp_path, monkeypatch, plugins)
    elif change == "extra":
        queued["intervention_targets"]["extra"] = queued["intervention_targets"][
            "trees"
        ]
    else:
        del queued["intervention_targets"]
        assert JobEnvelope.model_validate(wire).accepted.plan.metric == "root"
    native = native_job(monkeypatch)
    native.id = envelope.job_id
    if change == "legacy_plain":
        assert worker.run_metric_task(wire)["result_ref"]
        return
    store = Mock(side_effect=AssertionError("opened result store"))
    monkeypatch.setattr(worker, "SQLiteStore", store)
    with pytest.raises(
        worker.BoundPlanError, match="extra" if change == "extra" else "trees"
    ):
        worker.run_metric_task(wire)
    assert native.meta["failure"]["error"]["type"] == "bound_plan"
    store.assert_not_called()


@pytest.mark.parametrize("model", [ExecutionRequest, JobCreateRequest])
def test_acceptance_captures_current_actions_from_request_models(
    model: type[ExecutionRequest | JobCreateRequest],
) -> None:
    catalog = LocalRunner.from_definitions(definitions()).catalog
    change = actions()
    additions = change.pop("add")
    request = model.model_validate({"metric": "root", "interventions": [change]})
    value = request.interventions[0]
    value.add.extend(additions)
    plan = prepare_execution(catalog, request)
    value.add.clear()
    value.remove.clear()
    assert plan.request.interventions[0].add == additions
    assert plan.request.interventions[0].remove == ["old"]


def test_multiple_unused_targets_preserve_omitted_and_typed_empty_additions() -> None:
    class Parks(Trees):
        id = "parks"

    plugins = definitions()
    plugins["parks"] = PluginDefinition(resources={"parks_resource": Parks})
    catalog = LocalRunner.from_definitions(plugins).catalog
    removal = {"id": "remove", "revision": "1", "remove": ["old"]}
    request = {
        "metric": "root",
        "interventions": [
            removal | {"catalog": "trees"},
            removal | {"catalog": "parks", "add": []},
        ],
    }
    plan = prepare_execution(catalog, request)
    restored = PreparedExecution.model_validate_json(plan.model_dump_json())
    assert (
        restored.request.submission_payload()["interventions"]
        == request["interventions"]
    )
    assert prepare_execution(catalog, restored.request.submission_payload()) == plan
    trees, parks = restored.request.interventions
    assert accepted_additions(trees) is None
    empty = accepted_additions(parks)
    assert empty is not None
    assert empty.empty
    assert empty.crs == "EPSG:4326"
    assert str(empty["count"].dtype) == "Int64"


def test_target_override_must_honor_the_accepted_base() -> None:
    class DifferentTrees(Trees):
        code_revision = "different"

    local = LocalRunner.from_definitions(
        definitions(), resource_overrides={"survey_resource": DifferentTrees}
    )
    value = Intervention(id="remove", catalog="trees", revision="1", remove=["old"])
    with pytest.raises(GraphExecutionError, match="implementation differs"):
        local.run("root", interventions=[value])


@pytest.fixture
def intervention_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    class Survey(Trees):
        schema = declaration()
        schema.columns.update(
            {
                "height": column("Float64", nullable=True),
                "label": column("string", nullable=True),
                "enabled": column("boolean", nullable=True),
                "surveyed": column("bool", nullable=True),
            }
        )

    _, runtime = install_definitions(tmp_path, monkeypatch, definitions(Survey))
    runtime.jobs.enqueue = Mock()
    app = create_app(runtime.config, credentials=api_credentials())
    app.root_path = "/public/api"
    app.state.runtime = runtime
    return app


def survey_proposal() -> Intervention:
    data = frame()
    data["geometry"] = gpd.GeoSeries(
        [Point(1.2345678901234567, 2), Point(3, 4)], crs=4326
    )
    data["count"] = pd.Series([-(2**63), 2**63 - 1], dtype="Int64")
    data["height"] = pd.Series([1.2345678901234567, None], dtype="Float64")
    data["label"] = pd.Series([None, None], dtype="string")
    data["enabled"] = pd.Series([True, None], dtype="boolean")
    data["surveyed"] = pd.Series([False, None], dtype="boolean")
    data = pd.concat([data] * 16, ignore_index=True)
    data["location_id"] = pd.Series(
        [f"{i:03}" for i in range(len(data))], dtype="string"
    )
    data.index = pd.Index([99] * len(data), name="ignored")
    return Intervention(id="p", catalog="trees", revision="1", add=data, remove=["old"])


@pytest.mark.parametrize("empty", [False, True])
def test_client_worker_retains_native_request(
    intervention_app: FastAPI,
    native_api: NativeAPI,
    monkeypatch: pytest.MonkeyPatch,
    *,
    empty: bool,
) -> None:
    change = survey_proposal()
    assert change.add is not None
    original = change.add.iloc[:0] if empty else change.add
    if empty:
        original = original.astype({"surveyed": bool})
    changes = [replace(change, add=original)]
    with (
        native_api.serve(intervention_app, monkeypatch),
        native_api.connect() as client,
    ):
        job = native_api.resolve(client.jobs.create("root", interventions=changes))

    envelope = intervention_app.state.runtime.jobs.enqueue.call_args.args[0]
    native_job(monkeypatch).id = job.job_id
    result = worker.run_metric_task(json.loads(envelope.model_dump_json()))
    accepted = read_retained(str(result["result_ref"])).report.request.interventions[0]
    assert (accepted.id, accepted.catalog, accepted.revision, accepted.remove) == (
        "p",
        "trees",
        "1",
        ["old"],
    )
    restored = accepted_additions(accepted)
    assert restored is not None
    pd.testing.assert_frame_equal(
        restored,
        original.reset_index(drop=True)[list(restored.columns)],
        check_exact=True,
    )
    assert restored.geometry.to_wkb().tolist() == original.geometry.to_wkb().tolist()


@pytest.mark.parametrize("empty", [False, True])
def test_clients_validate_frames_before_post(
    intervention_app: FastAPI,
    native_api: NativeAPI,
    monkeypatch: pytest.MonkeyPatch,
    *,
    empty: bool,
) -> None:
    value = survey_proposal()
    assert value.add is not None
    data = value.add.iloc[:0] if empty else value.add
    with (
        native_api.serve(intervention_app, monkeypatch),
        native_api.connect() as client,
    ):
        for invalid in (
            data.drop(columns="label"),
            data.assign(extra=1),
            data.to_crs(3857),
            gpd.GeoDataFrame(data.to_dict("list"), geometry="geometry"),
            data.assign(label=data.geometry).set_geometry("label"),
            data.assign(count=data["count"].astype("string")),
            data.assign(enabled=data.enabled.fillna(value=False).astype(bool)),
        ):
            with pytest.raises(
                ValueError,
                match=r"columns|CRS|active geometry|requires Int64|requires boolean",
            ):
                native_api.resolve(
                    client.jobs.create(
                        "root", interventions=[replace(value, add=invalid)]
                    )
                )
    assert {req.method for req in native_api.requests} == {"GET"}
    intervention_app.state.runtime.jobs.enqueue.assert_not_called()


def test_clients_propagate_authoritative_validation_errors(
    intervention_app: FastAPI, native_api: NativeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = survey_proposal()
    assert value.add is not None
    value.add["geometry"] = gpd.GeoSeries(
        [Point()] * len(value.add), index=value.add.index, crs=4326
    )
    with (
        native_api.serve(intervention_app, monkeypatch),
        native_api.connect() as client,
        pytest.raises(DownloadError, match="HTTP 422"),
    ):
        native_api.resolve(client.jobs.create("root", interventions=[value]))
    assert [req.method for req in native_api.requests] == ["GET", "POST"]
    intervention_app.state.runtime.jobs.enqueue.assert_not_called()


@pytest.mark.parametrize("cancel", [False, True])
def test_async_submission_owns_values_while_discovering(
    intervention_app: FastAPI, monkeypatch: pytest.MonkeyPatch, *, cancel: bool
) -> None:
    async def exercise() -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        discovery = intervention_app.state.runtime.catalog.discovery.inspect("root")
        requests = []

        async def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "GET":
                entered.set()
                await release.wait()
                return httpx.Response(200, json=discovery.model_dump(mode="json"))
            return httpx.Response(202, json=_job_response())

        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            partial(httpx.AsyncClient, transport=httpx.MockTransport(respond)),
        )
        value = survey_proposal()
        changes = [value]
        ids: list[JsonValue] = ["001"]
        weights: list[JsonValue] = [42]
        async with AsyncLyraClient("test") as client:
            task = asyncio.create_task(
                client.jobs.submit(
                    "root",
                    inputs={"places": {"ids": ids}},
                    parameters={"weights": weights},
                    interventions=changes,
                )
            )
            await entered.wait()
            changes.clear()
            ids.clear()
            weights.clear()
            assert value.add is not None
            assert value.remove is not None
            value.add["height"] = 0
            value.remove.clear()
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert [req.method for req in requests] == ["GET"]
            else:
                release.set()
                await task
                body = json.loads(requests[-1].content)
                assert body["inputs"] == {"places": {"ids": ["001"]}}
                assert body["parameters"] == {"weights": [42]}
                assert body["interventions"][0]["remove"] == ["old"]
                assert (
                    body["interventions"][0]["add"][0]["height"].hex()
                    == (1.2345678901234567).hex()
                )
        with pytest.raises(RuntimeError, match="closed"):
            await client.jobs.create("root", interventions=[survey_proposal()])

    asyncio.run(exercise())
