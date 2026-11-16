"""Concrete output owners and direct origins survive reuse and empty demand."""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import geopandas as gpd
import httpx
import pandas as pd
import pyarrow as pa
import pytest
from starlette.applications import Starlette
from starlette.routing import Mount

from lyra.api import DownloadError
from lyra.contracts.inspection import TerminalResultOutput
from lyra.contracts.jobs import JobStatusInfo, TableJobResult
from lyra.contracts.origins import Origin
from lyra.runtime.local import LocalRunner
from lyra.runtime.results import ResultStore, RunResult
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.sdk import LocationDependency, PluginDefinition, metric
from lyra.server.job_store import JobObservation
from lyra.server.main import create_app
from lyra.server.results import ResultService
from lyra.server.runtime import ApplicationRuntime
from test_support.location_operations import Places
from tests.cache_helpers import SCHEMA
from tests.config_helpers import api_credentials, load_test_config
from tests.mcp_helpers import mcp_headers, tool_call_payload
from tests.metric_execution_helpers import schema
from tests.retrieval_client_helpers import BASE, NativeAPI, native_api
from tests.sqlite_helpers import database

__all__ = ["database", "native_api"]
pytestmark = pytest.mark.usefixtures("database")


def test_concrete_metrics_and_shared_origin_revisions(
    tmp_path: Path, native_api: NativeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "results.sqlite"
    retained = []
    calls = []
    for code_revision, origin_revision in [("1", "1"), ("2", "1"), ("2", "2")]:
        origin = Origin(
            id="census", revision=origin_revision, description="Census records"
        )

        class Blocks(Places):
            id = "blocks"
            origins = (origin,)

        class Tracts(Places):
            id = "tracts"
            origins = (origin,)

        Blocks.code_revision = Tracts.code_revision = code_revision
        metrics = [
            measurement(provider, origin, calls) for provider in (Blocks, Tracts)
        ]
        metrics.append(shared_origin_summary(origin))
        runner = LocalRunner.from_definitions(
            {
                "test": PluginDefinition(
                    metrics=metrics, resources={"blocks": Blocks, "tracts": Tracts}
                )
            },
            result_store_path=path,
        )
        for provider in (Blocks, Tracts):
            for ids, expected in [
                ([], "skipped"),
                (["a"], "executed"),
                (["a", "b"], "mixed"),
                (["a", "b"], "reused"),
            ]:
                calls.clear()
                result = runner.run(
                    f"{provider.id}_measure",
                    inputs={"places": {"provider": provider.id, "ids": ids}},
                )
                report = result.report
                root = next(n for n in report.nodes if n.use_id == report.root_use_id)
                assert root.outcome == expected
                assert root.origins == [origin]
                assert root.code_revision == "measure-1"
                assert root.primary_input == "places"
                assert report.build_revision is None
                assert report.request.inputs["places"].ids == ids
                scope = root.inputs["places"].provider_scope
                assert scope is not None
                assert (scope.provider_id, scope.code_revision, scope.origins) == (
                    provider.id,
                    code_revision,
                    frozenset({origin}),
                )
                for name in ("area", "count"):
                    assert root.outputs[name].owner_scope == scope
                    assert root.outputs[name].scope == "location"
                    assert result.metadata.outputs[name] == root.outputs[name]
                    assert result.tables[name].location_id.tolist() == ids
                provider_node = next(n for n in report.nodes if n.kind == "provider")
                assert provider_node.origins == [origin]
                assert provider_node.code_revision == code_revision
                if expected in {"skipped", "reused"}:
                    assert calls == []
                    assert provider_node.outcome == "skipped"
                else:
                    assert calls == [("a",) if expected == "executed" else ("b",)]
                retained.append(result)
        for expected in ("executed", "reused"):
            result = runner.run(
                "summary",
                inputs={
                    "blocks": {"provider": "blocks", "ids": ["a"]},
                    "tracts": {"provider": "tracts", "ids": ["a"]},
                },
            )
            root = next(
                n for n in result.report.nodes if n.use_id == result.report.root_use_id
            )
            assert root.outcome == expected
            assert root.origins == [origin]
            scopes = [binding.provider_scope for binding in root.inputs.values()]
            assert all(
                scope is not None and scope.origins == frozenset({origin})
                for scope in scopes
            )
            assert {scope.provider_id for scope in scopes if scope is not None} == {
                "blocks",
                "tracts",
            }
            assert len({binding.source_use_id for binding in root.inputs.values()}) == 2
            assert root.outputs["total"].owner_scope is None
            assert result.tables["total"].value.tolist() == [2]
    reports = [result.report for result in retained]
    assert_retained_outputs(path, retained)
    # Later declarations do not rewrite earlier reports' captured descriptions.
    assert next(
        n for n in reports[0].nodes if n.use_id == reports[0].root_use_id
    ).origins == [Origin(id="census", revision="1", description="Census records")]
    assert_remote_outputs(path, retained, native_api, monkeypatch)


def assert_remote_outputs(
    path: Path,
    retained: list[RunResult],
    native: NativeAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_test_config(path.parent)
    config.result_store_path = path
    config.api.public_base_url = BASE
    config.mcp.enabled = True
    path.touch()
    metadata = retained[1].metadata
    now = datetime.now(UTC)
    status = JobStatusInfo(
        job_id="job",
        status="succeeded",
        created_at=now,
        updated_at=now,
        result_ref=metadata.result_ref,
        execution_id=metadata.execution_id,
    )
    jobs = Mock(
        read_job=Mock(
            return_value=JobObservation(
                snapshot=status,
                result_ref=status.result_ref,
                execution_id=status.execution_id,
            )
        ),
        get_job_status_async=AsyncMock(return_value=status),
    )
    app = create_app(config, credentials=api_credentials())
    app.state.runtime = ApplicationRuntime(
        config,
        Mock(spec=[]),
        Mock(),
        Mock(),
        jobs,
        api_credentials(),
        ResultService(jobs, path),
    )
    public = Starlette(routes=[Mount("/public/api", app)])
    with native.serve(public, monkeypatch) as http:
        with native.connect() as client:
            for response in (
                native.resolve(client.jobs.get("job")),
                native.resolve(client.results.get("job")),
                native.resolve(client.results.inspect("job")),
            ):
                assert isinstance(
                    response, JobStatusInfo | TableJobResult | TerminalResultOutput
                )
                assert response.result_ref == metadata.result_ref
                assert response.execution_id == metadata.execution_id
        jobs.read_job.return_value = None
        jobs.get_job_status_async.return_value = None
        with native.connect() as client, pytest.raises(DownloadError, match="404"):
            native.resolve(client.results.get("job"))
        jobs.read_job.side_effect = AssertionError(
            "Durable reads must not observe expired jobs"
        )
        monkeypatch.setattr(
            LocalRunner, "run", Mock(side_effect=AssertionError("No execution"))
        )
        mcp = app.state.mcp_app
        for result in retained:
            assert_remote_run(result, native, path.parent)

        async def check_tools() -> None:
            async with mcp.router.lifespan_context(mcp):
                for result in retained:
                    await assert_scope_tools(http, result)

        native.resolve(check_tools())


def assert_remote_run(result: RunResult, native: NativeAPI, directory: Path) -> None:
    reference, execution_id = result.metadata.result_ref, result.metadata.execution_id
    with native.connect() as client:
        description = native.resolve(client.results.describe(reference))
        report = native.resolve(client.reports.get(execution_id))
        inspection = native.resolve(client.reports.inspect(execution_id))
        assert inspection.kind == "complete"
        assert inspection.report == report == result.report
        assert description.outputs == result.metadata.outputs
        assert description.execution_id == execution_id
        frame = native.resolve(client.results.table(reference, "area"))
        rows = []
        cursor = None
        while True:
            page = native.resolve(
                client.results.rows(reference, "area", limit=1, cursor=cursor)
            )
            rows.extend(page.rows)
            cursor = page.next_cursor
            if cursor is None:
                break
        assert rows == [
            {**row, "value": str(row["value"])} for row in frame.to_dict("records")
        ]
        native.resolve(
            client.results.download_table(reference, "area", directory / "values.any")
        )
        native.resolve(client.reports.download(execution_id, directory / "report.any"))
    expected = result.tables["area"].astype(
        {"location_id": "string", "row_key": "string", "value": "Int64"}
    )
    pd.testing.assert_frame_equal(frame, expected)
    with pa.ipc.open_stream(directory / "values.any") as reader:
        pd.testing.assert_frame_equal(reader.read_all().to_pandas(), expected)
    assert (
        type(report).model_validate_json((directory / "report.any").read_bytes())
        == report
    )
    description.outputs.clear()
    report.nodes.clear()
    frame.attrs["owner_scope"] = {"provider_id": "changed"}
    if not frame.empty:
        frame.loc[0, "location_id"] = "changed"
        frame.loc[0, "value"] = -1
    with native.connect() as client:
        assert (
            native.resolve(client.results.describe(reference)).outputs
            == result.metadata.outputs
        )
        assert native.resolve(client.reports.get(execution_id)) == result.report
        pd.testing.assert_frame_equal(
            native.resolve(client.results.table(reference, "area")), expected
        )


async def assert_scope_tools(http: httpx.AsyncClient, result: RunResult) -> None:
    reference = result.metadata.result_ref
    responses = {}
    for name, arguments in (
        ("lyra_describe_result", {"result_ref": reference}),
        ("lyra_inspect_report", {"execution_id": result.metadata.execution_id}),
        ("lyra_get_result_rows", {"result_ref": reference, "output_name": "area"}),
        ("lyra_download_table", {"result_ref": reference, "output_name": "area"}),
    ):
        response = await http.post(
            BASE + "/mcp/",
            headers=mcp_headers(),
            json=tool_call_payload(name, arguments),
        )
        assert response.status_code == 200
        envelope = response.json()["result"]
        assert not envelope.get("isError")
        payload = envelope["structuredContent"]
        assert json.loads(envelope["content"][0]["text"]) == payload.get(
            "result", payload
        )
        responses[name] = payload
    description = responses["lyra_describe_result"]["result"]["description"]
    assert description["outputs"] == result.metadata.model_dump(mode="json")["outputs"]
    assert responses["lyra_inspect_report"]["result"][
        "report"
    ] == result.report.model_dump(mode="json")
    rows = responses["lyra_get_result_rows"]
    assert rows["next_cursor"] is None
    assert rows["rows"] == [
        {**row, "value": str(row["value"])}
        for row in result.tables["area"].to_dict("records")
    ]
    link = responses["lyra_download_table"]["download_url"]
    assert link.startswith(BASE + "/results/")
    assert (await http.get(link)).status_code == 401
    arrow = await http.get(link, headers=mcp_headers())
    assert arrow.headers["content-type"] == "application/vnd.apache.arrow.stream"
    with pa.ipc.open_stream(arrow.content) as reader:
        assert reader.read_all().to_pylist() == result.tables["area"].to_dict("records")


def assert_retained_outputs(path: Path, retained: list) -> None:
    assert len({result.report.execution_id for result in retained}) == len(retained)
    with ResultStore(SQLiteStore(path, read_only=True)) as reader:
        for result in retained:
            metadata, tables = result.metadata, result.tables
            assert reader.describe(metadata.result_ref).outputs == metadata.outputs
            for name, frame in tables.items():
                pd.testing.assert_frame_equal(
                    reader.read_table(metadata.result_ref, name),
                    frame.astype(
                        {"location_id": "string", "row_key": "string", "value": "Int64"}
                    ),
                )


def measurement(provider: type[Places], origin: Origin, calls: list) -> Callable:
    @metric(
        name=f"{provider.id}_measure",
        description="Concrete measurement",
        code_revision="measure-1",
        cache="per_location",
        origins=[origin, origin],
        inputs={"places": LocationDependency(provider=provider.id, primary=True)},
        outputs={"area": schema(), "count": schema()},
    )
    def measure(places: gpd.GeoDataFrame) -> dict[str, pd.DataFrame]:
        calls.append(tuple(places.location_id))
        result = pd.DataFrame(
            {"location_id": places.location_id, "row_key": "one", "value": 1}
        )
        places.attrs["provider_scope"] = {"provider_id": "forged"}
        places.loc[:, "location_id"] = "forged"
        return {"area": result, "count": result.copy()}

    return measure


def shared_origin_summary(origin: Origin) -> Callable:
    @metric(
        name="summary",
        description="Shared external origin",
        cache="full",
        origins=[origin],
        inputs={
            "blocks": LocationDependency(provider="blocks"),
            "tracts": LocationDependency(provider="tracts"),
        },
        outputs={"total": SCHEMA},
    )
    def summary(blocks: gpd.GeoDataFrame, tracts: gpd.GeoDataFrame) -> pd.DataFrame:
        return pd.DataFrame({"value": [len(blocks) + len(tracts)]})

    return summary
