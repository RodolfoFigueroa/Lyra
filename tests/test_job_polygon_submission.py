"""Submission validates selections and queues them without provider access."""

import asyncio
import threading
from collections.abc import Coroutine
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pytest
from fastapi import HTTPException
from redis.exceptions import RedisError

from lyra.contracts.jobs import JobCreateRequest
from lyra.sdk.dependencies import LocationDependency
from lyra.sdk.plugin import PluginDefinition, metric
from lyra.server import job_submission
from lyra.server.routes.jobs import create_job
from test_support.location_resolution import Blocks, Provider, features, selection
from tests.queue_helpers import submitted_input
from tests.worker_graph_integration_helpers import TABLE, install, install_definitions


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "empty",
        "scope",
        "type",
        "geometry",
        "unknown",
        "partial",
        "setup",
        "cleanup",
        "acquisition",
        "factory",
    ],
)
@pytest.mark.parametrize("combined_plugin", [False, True])
def test_submission_lifecycle(
    failure: str | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    combined_plugin: bool,
) -> None:
    diagnostic = "secret credential"
    events = []
    threads = []

    @metric(
        inputs={
            "blocks": LocationDependency(provider=Blocks.id),
            "lots": LocationDependency(provider=Provider.id),
        },
        code_revision="1",
        name="root",
        description="Root",
        outputs={"answer": TABLE},
    )
    def root(blocks: gpd.GeoDataFrame, lots: gpd.GeoDataFrame) -> pd.DataFrame:
        assert not blocks.empty
        assert not lots.empty
        return pd.DataFrame()

    resources = {"locations": Provider, "blocks": Blocks}
    definitions = (
        {"plugin_0": PluginDefinition(metrics=[root], resources=resources)}
        if combined_plugin
        else {
            "plugin_0": PluginDefinition(metrics=[root]),
            "provider": PluginDefinition(resources=resources),
        }
    )
    runtime = install_definitions(tmp_path, monkeypatch, definitions)[1]

    def record(event: str) -> None:
        events.append(event)
        threads.append(threading.get_ident())
        if failure == event:
            raise RuntimeError(diagnostic)

    monkeypatch.setattr(Provider, "setup", lambda _: record("setup"))
    monkeypatch.setattr(Provider, "close", lambda _: record("cleanup"))

    def resolve(_self: Provider, ids: list[str]) -> gpd.GeoDataFrame:
        record("acquisition")
        raise AssertionError(ids)

    monkeypatch.setattr(Provider, "resolve", resolve)
    enqueue = Mock(side_effect=lambda *_, **__: events.append("enqueue"))
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    inputs = {
        "blocks": selection("blocks", "blocks", provider=Blocks.id).model_dump(
            mode="json"
        ),
        "lots": selection("lots", "second").model_dump(mode="json"),
    }
    _alter_input(inputs, failure)
    loop_thread = threading.get_ident()
    request = JobCreateRequest.model_validate(
        {
            "metric": "root",
            "inputs": {k: v for k, v in inputs.items() if k != "parameters"},
            **({"parameters": inputs["parameters"]} if "parameters" in inputs else {}),
        }
    )
    call = create_job(request, catalog=runtime.catalog, jobs=runtime.jobs)
    _assert_submission(call, failure, enqueue, events, inputs)
    assert all(thread != loop_thread for thread in threads)


def _alter_input(inputs: dict, failure: str | None) -> None:
    if failure == "empty":
        inputs["lots"]["ids"] = []
    elif failure == "scope":
        inputs["lots"]["provider"] = "other:units"
    elif failure == "type":
        inputs["lots"]["provider"] = 1
    elif failure == "geometry":
        inputs["lots"] = {
            "data_type": "geojson",
            "value": features(["lots"]).to_dict(),
        }


def _assert_submission(
    call: Coroutine[Any, Any, object],
    failure: str | None,
    enqueue: Mock,
    events: list,
    inputs: dict,
) -> None:
    if failure in {"geometry", "scope", "type"}:
        with pytest.raises(HTTPException) as error:
            asyncio.run(call)
        assert error.value.status_code == 422
        enqueue.assert_not_called()
        assert events == []
    else:
        asyncio.run(call)
        assert events == ["enqueue"]
        (envelope,) = enqueue.call_args.args
        assert submitted_input(envelope) == submitted_input(envelope) == inputs


@pytest.mark.parametrize("enqueue_fails", [False, True])
def test_location_free_submission_never_loads_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, enqueue_fails: bool
) -> None:
    @metric(
        code_revision="1", name="root", description="Root", outputs={"answer": TABLE}
    )
    def root() -> pd.DataFrame:
        return pd.DataFrame()

    runtime = install(tmp_path, monkeypatch, [root])[1]
    forbidden = Mock(
        side_effect=AssertionError("provider acquisition during submission")
    )
    monkeypatch.setattr(Provider, "setup", forbidden)
    enqueue = Mock(side_effect=RedisError if enqueue_fails else None)
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    call = job_submission.submit_job(
        JobCreateRequest(metric="root", inputs={}),
        catalog=runtime.catalog,
        jobs=runtime.jobs,
    )
    if enqueue_fails:
        with pytest.raises(
            job_submission.SubmissionUnavailableError, match="may have been accepted"
        ):
            asyncio.run(call)
    else:
        asyncio.run(call)
        assert submitted_input(enqueue.call_args.args[0]) == {}
    enqueue.assert_called_once()
    forbidden.assert_not_called()
