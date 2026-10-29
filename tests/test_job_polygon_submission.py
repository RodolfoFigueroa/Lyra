"""Server selections resolve atomically before acceptance, without services."""

import asyncio
import threading
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pandas as pd
import pytest
from fastapi import HTTPException
from lyra.sdk import LocationInput, PluginDefinition, UnresolvedLocationIDsError, metric
from lyra.sdk.locations import SelectedLocationProvider
from lyra.sdk.models.job import JobCreateRequest
from lyra.sdk.models.plugin import PluginInfo, PluginManifest
from redis.exceptions import RedisError

from lyra_app import job_submission
from lyra_app.routes.jobs import create_job
from tests.test_location_resolution import SELECTED, Provider, features, selection
from tests.test_worker_graph_integration import TABLE, install


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

    @metric(name="root", description="Root", outputs={"answer": TABLE})
    def root(blocks: LocationInput, lots: LocationInput) -> pd.DataFrame:
        assert blocks.features
        assert lots.features
        return pd.DataFrame()

    runtime = install(tmp_path, monkeypatch, [root])[1]
    provider = PluginDefinition(
        nodes=[root] if combined_plugin else [],
        resources={"locations": Provider},
        location_provider="locations",
    )
    distribution = "plugin_0" if combined_plugin else "provider"
    manifest = provider.manifest(
        plugin=PluginInfo(name=distribution, version="1"),
        factory=f"{distribution}:create",
    )
    runtime.catalog.manifests[distribution] = manifest
    assert provider.location_provider is not None
    runtime.catalog.location_provider = SelectedLocationProvider(
        distribution, provider.location_provider
    )
    original = job_submission.load_validated_plugin_definition

    def load(m: PluginManifest) -> PluginDefinition:
        if m == manifest:
            if failure == "factory":
                raise RuntimeError(diagnostic)
            return provider
        return original(m)

    monkeypatch.setattr(
        job_submission,
        "load_validated_plugin_definition",
        load,
    )

    def record(event: str) -> None:
        events.append(event)
        threads.append(threading.get_ident())
        if failure == event:
            raise RuntimeError(diagnostic)

    monkeypatch.setattr(Provider, "setup", lambda _: record("setup"))
    monkeypatch.setattr(Provider, "close", lambda _: record("cleanup"))

    def resolve(
        _self: Provider, collection: str, unit_type: str, ids: list[str]
    ) -> LocationInput:
        return _resolve_units(record, failure, collection, unit_type, ids)

    monkeypatch.setattr(Provider, "resolve", resolve)
    enqueue = Mock(side_effect=lambda *_: events.append("enqueue"))
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    inputs = {
        "blocks": selection("blocks", "blocks").model_dump(mode="json"),
        "lots": selection("lots", "second").model_dump(mode="json"),
    }
    _alter_input(inputs, failure)
    loop_thread = threading.get_ident()
    request = JobCreateRequest(metric="root", input=inputs)
    call = create_job(request, catalog=runtime.catalog, jobs=runtime.jobs)
    _assert_submission(call, failure, enqueue, events, inputs)
    assert all(thread != loop_thread for thread in threads)


def _resolve_units(
    record: Callable[[str], None],
    failure: str | None,
    collection: str,
    unit_type: str,
    ids: list[str],
) -> LocationInput:
    record(ids[0])
    if ids[0] == "lots":
        if failure == "unknown":
            raise UnresolvedLocationIDsError(collection, unit_type, ids)
        if failure == "partial":
            return features(ids[:1])
        if failure == "acquisition":
            diagnostic = "secret credential"
            raise RuntimeError(diagnostic)
    return features(ids)


def _alter_input(inputs: dict, failure: str | None) -> None:
    if failure == "empty":
        inputs["lots"]["ids"] = []
    elif failure == "scope":
        inputs["lots"]["collection"] = "other:units"
    elif failure == "type":
        inputs["lots"]["unit_type"] = "other"
    elif failure == "geometry":
        inputs["lots"] = {
            "data_type": "geojson",
            "value": features(["lots"]).model_dump(mode="json"),
        }


def _assert_submission(
    call: Coroutine[Any, Any, object],
    failure: str | None,
    enqueue: Mock,
    events: list,
    inputs: dict,
) -> None:
    if failure:
        with pytest.raises(HTTPException) as error:
            asyncio.run(call)
        assert error.value.status_code == (
            503
            if failure in {"setup", "cleanup", "acquisition", "partial", "factory"}
            else 422
        )
        assert "secret credential" not in str(error.value.detail)
        enqueue.assert_not_called()
        if failure in {"empty", "scope", "type", "geometry", "factory"}:
            assert events == []
        if failure == "unknown":
            assert isinstance(error.value.detail, list)
            assert error.value.detail[0]["loc"] == ["lots"]
            assert error.value.detail[0]["ids"] == ["lots", "second"]
    else:
        asyncio.run(call)
        assert events == ["setup", "blocks", "lots", "cleanup", "enqueue"]
        envelope, provenance, _ = enqueue.call_args.args
        assert provenance.input == inputs
        assert envelope.input["blocks"]["collection"] == "test:units"
        assert len(envelope.input["blocks"]["location"]["features"]) == 1


@pytest.mark.parametrize("enqueue_fails", [False, True])
def test_location_free_submission_never_loads_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, enqueue_fails: bool
) -> None:
    @metric(name="root", description="Root", outputs={"answer": TABLE})
    def root() -> pd.DataFrame:
        return pd.DataFrame()

    runtime = install(tmp_path, monkeypatch, [root])[1]
    runtime.catalog.location_provider = SELECTED
    # There is deliberately no provider manifest: touching it fails this test.
    enqueue = Mock(side_effect=RedisError if enqueue_fails else None)
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    call = job_submission.submit_job(
        JobCreateRequest(metric="root", input={}),
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
        assert enqueue.call_args.args[0].input == {}
    enqueue.assert_called_once()
