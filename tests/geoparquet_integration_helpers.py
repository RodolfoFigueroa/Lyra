"""Retained export assertions shared by execution integration scenarios."""

import json
from collections.abc import Awaitable
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar
from unittest.mock import AsyncMock, Mock

import geopandas as gpd
import httpx
import pandas as pd
import pyarrow as pa
import pytest
from geopandas.testing import assert_geodataframe_equal
from pydantic import BaseModel
from starlette.applications import Starlette
from starlette.routing import Mount

from lyra.api import DownloadError
from lyra.api.client.async_ import _ResultsResource as AsyncResults
from lyra.api.client.sync import _ResultsResource as SyncResults
from lyra.contracts.inspection import TerminalResultOutput
from lyra.contracts.jobs import JobStatusInfo, TableJobResult
from lyra.runtime.local import LocalRunner
from lyra.runtime.results import ResultStore, RunResult
from lyra.server.job_store import JobObservation
from lyra.server.main import create_app
from lyra.server.results import ResultService
from lyra.server.runtime import ApplicationRuntime
from tests.config_helpers import api_credentials, load_test_config
from tests.mcp_helpers import mcp_headers, tool_call_payload
from tests.retrieval_client_helpers import BASE, NativeAPI

_T = TypeVar("_T", bound=BaseModel | pd.DataFrame | None)


def assert_remote_outputs(
    path: Path,
    retained: list[RunResult],
    native: NativeAPI,
    monkeypatch: pytest.MonkeyPatch,
    *,
    exports_only: bool = False,
) -> None:
    config = load_test_config(path.parent)
    config.result_store_path = path
    config.api.public_base_url = BASE
    config.mcp.enabled = True
    path.touch()
    metadata = retained[1].metadata
    now = datetime.now(UTC)
    status = JobStatusInfo(
        job_id=metadata.job_id,
        status="succeeded",
        created_at=now,
        updated_at=now,
    )
    jobs = Mock(
        read_job=Mock(
            return_value=JobObservation(
                snapshot=status,
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
                native.resolve(client.jobs.get(metadata.job_id)),
                native.resolve(client.results.get(metadata.job_id)),
                native.resolve(client.results.inspect(metadata.job_id)),
            ):
                assert isinstance(
                    response, JobStatusInfo | TableJobResult | TerminalResultOutput
                )
                assert response.job_id == metadata.job_id
        jobs.read_job.return_value = None
        jobs.get_job_status_async.return_value = None
        with native.connect() as client, pytest.raises(DownloadError, match="404"):
            native.resolve(client.results.get(metadata.job_id))
        jobs.read_job.side_effect = AssertionError(
            "Durable reads must not observe expired jobs"
        )
        monkeypatch.setattr(
            LocalRunner, "run", Mock(side_effect=AssertionError("No execution"))
        )
        mcp = app.state.mcp_app
        if exports_only:
            for result in retained:
                with native.connect() as client:
                    assert_exports(
                        client.results, result.metadata.job_id, path.parent, native
                    )
                    assert (
                        native.resolve(client.reports.get(result.metadata.job_id))
                        == result.report
                    )
            return
        for result in retained:
            assert_remote_run(result, native, path.parent)

        async def check_tools() -> None:
            async with mcp.router.lifespan_context(mcp):
                for result in retained:
                    await assert_scope_tools(http, result)

        native.resolve(check_tools())


def assert_remote_run(result: RunResult, native: NativeAPI, directory: Path) -> None:
    reference, job_id = result.metadata.job_id, result.metadata.job_id
    with native.connect() as client:
        assert_exports(client.results, reference, directory, native)
        description = native.resolve(client.results.describe(reference))
        report = native.resolve(client.reports.get(job_id))
        inspection = native.resolve(client.reports.inspect(job_id))
        assert inspection.kind == "complete"
        assert inspection.report == report == result.report
        assert description.outputs == result.metadata.outputs
        assert description.job_id == job_id
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
        native.resolve(client.reports.download(job_id, directory / "report.any"))
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
        assert native.resolve(client.reports.get(job_id)) == result.report
        pd.testing.assert_frame_equal(
            native.resolve(client.results.table(reference, "area")), expected
        )


async def assert_scope_tools(http: httpx.AsyncClient, result: RunResult) -> None:
    reference = result.metadata.job_id
    responses = {}
    for name, arguments in (
        ("lyra_describe_result", {"job_id": reference}),
        ("lyra_inspect_report", {"job_id": result.metadata.job_id}),
        ("lyra_get_result_rows", {"job_id": reference, "output_name": "area"}),
        ("lyra_download_table", {"job_id": reference, "output_name": "area"}),
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


def assert_exports(
    access: ResultStore | SyncResults | AsyncResults,
    reference: str,
    directory: Path,
    native: NativeAPI | None = None,
) -> None:
    """Retained identities survive caller mutation on every interface."""

    def resolve(value: _T | Awaitable[_T]) -> _T:
        if native:
            return native.resolve(value)
        assert not isinstance(value, Awaitable)
        return value

    description = resolve(access.describe(reference))
    for kind, selections in (
        ("metric_output", description.outputs),
        ("location_input", description.location_inputs),
    ):
        for name, selected in selections.items():
            if selected.table_schema.geometry is None:
                continue
            read = (
                (access.read_table if isinstance(access, ResultStore) else access.table)
                if kind == "metric_output"
                else (
                    access.read_location_input
                    if isinstance(access, ResultStore)
                    else access.location_input
                )
            )
            expected = resolve(read(reference, name))
            edited = resolve(read(reference, name))
            edited.attrs["lyra"] = {"job_id": "forged"}
            if not edited.empty:
                edited["geometry"] = None
            changed = resolve(access.describe(reference))
            (changed.outputs if kind == "metric_output" else changed.location_inputs)[
                name
            ].table_schema.columns.clear()
            destination = directory / "selection.parquet"
            export = (
                access.export_geoparquet
                if kind == "metric_output"
                else access.export_location_input_geoparquet
            )
            assert resolve(export(reference, name, destination)) is None
            actual = gpd.read_parquet(destination)
            keys = [
                key
                for key in ("source_location_id", "location_id", "row_key")
                if key in expected
            ]

            def ordered(
                frame: gpd.GeoDataFrame, keys: list[str] = keys
            ) -> gpd.GeoDataFrame:
                return (frame.sort_values(keys) if keys else frame).reset_index(
                    drop=True
                )

            assert_geodataframe_equal(
                ordered(actual), ordered(expected), check_dtype=False
            )
            assert actual.attrs == {
                "lyra": {
                    "kind": kind,
                    "job_id": reference,
                    "output_name" if kind == "metric_output" else "input_name": name,
                    "description": selected.model_dump(mode="json"),
                }
            }
            assert (
                resolve(access.describe(actual.attrs["lyra"]["job_id"])) == description
            )
            actual.attrs.clear()
            actual.to_parquet(directory / "ordinary.parquet", index=False)
            assert_geodataframe_equal(
                gpd.read_parquet(directory / "ordinary.parquet"), actual
            )
