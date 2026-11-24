"""Public row traversal over isolated storage and actual transport envelopes."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx
import pandas as pd
import pyarrow as pa
import pytest
from jsonschema import validate

from lyra.contracts.errors import ResultNotFoundError, RowTooLargeError
from lyra.contracts.results import ResultRowPage
from lyra.contracts.retrieval_paths import (
    location_input_download_path,
    location_input_rows_path,
    table_download_path,
    table_rows_path,
)
from lyra.server.response_budget import MAX_RETRIEVAL_BYTES, http_response_budget
from lyra.server.result_rows import RowPageContext
from tests.arrow_download_helpers import BASE, Retained, retained
from tests.mcp_helpers import mcp_headers, tool_call_payload, tool_payload
from tests.retrieval_client_helpers import NativeAPI, native_api
from tests.sqlite_helpers import Database, database
from tests.table_client_helpers import ClientHarness, client

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["client", "database", "native_api", "retained"]


@pytest.mark.parametrize(
    "retained",
    [(True, "populated"), (False, "empty"), (False, "large"), (False, "mixed")],
    indirect=True,
)
def test_public_traversal(retained: Retained) -> None:
    expected = retained.service.read_rows(
        retained.reference,
        retained.output_name,
        10000,
        context=RowPageContext(BASE, http_response_budget(limit=10000000)),
    ).rows

    async def run() -> None:
        app = retained.app.state.mcp_app
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=retained.public)
            ) as http,
        ):
            url = retained.url.replace("/download", "/rows")
            assert (await http.get(url)).status_code == 401
            assert (
                await http.get(url, headers=mcp_headers("wrong"))
            ).status_code == 403
            definitions = (
                await http.post(
                    BASE + "/mcp/",
                    headers=mcp_headers(),
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                )
            ).json()["result"]["tools"]
            tool = next(t for t in definitions if t["name"] == "lyra_get_result_rows")
            assert tool["annotations"]["readOnlyHint"]
            for mode in ("http", "mcp"):
                observed = []
                cursor = None
                for _ in range(len(expected) + 1):
                    arguments = {
                        "job_id": retained.reference,
                        "output_name": retained.output_name,
                        "limit": 100,
                    }
                    if cursor is not None:
                        arguments["cursor"] = cursor
                    if mode == "http":
                        response = await http.get(
                            url,
                            params={
                                k: v
                                for k, v in arguments.items()
                                if k in {"limit", "cursor"}
                            },
                            headers=mcp_headers(),
                        )
                        assert response.status_code == 200
                        payload = response.json()
                    else:
                        validate(arguments, tool["inputSchema"])
                        response = await http.post(
                            BASE + "/mcp/",
                            headers=mcp_headers(),
                            json=tool_call_payload("lyra_get_result_rows", arguments),
                        )
                        payload = tool_payload(response)
                        validate(payload, tool["outputSchema"])
                    assert len(response.content) <= MAX_RETRIEVAL_BYTES
                    page = ResultRowPage.model_validate(payload)
                    assert len(page.rows) <= 100
                    observed.extend(page.rows)
                    cursor = page.next_cursor
                    if cursor is None:
                        break
                else:
                    pytest.fail("Traversal failed to terminate")
                assert observed == expected

    asyncio.run(run())


@pytest.mark.parametrize(
    ("parameter", "value"),
    [
        ("limit", "x"),
        ("limit", "1.5"),
        ("limit", "true"),
        ("limit", 0),
        ("limit", -1),
        ("cursor", ""),
        ("cursor", "bad"),
    ],
)
def test_pagination_errors(
    retained: Retained, parameter: str, value: str | int
) -> None:
    async def run() -> None:
        app = retained.app.state.mcp_app
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=retained.public),
                headers=mcp_headers(),
            ) as http,
        ):
            response = await http.get(
                retained.url.replace("/download", "/rows"), params={parameter: value}
            )
            assert response.status_code == 422
            error = response.json()["detail"]
            assert error["code"] == "invalid_pagination"
            assert error["parameter"] == parameter
            response = await http.post(
                BASE + "/mcp/",
                json=tool_call_payload(
                    "lyra_get_result_rows",
                    {
                        "job_id": retained.reference,
                        "output_name": "rows",
                        parameter: value,
                    },
                ),
            )
            result = response.json()["result"]
            assert result["isError"]
            if parameter == "limit" and isinstance(value, str):
                assert "structuredContent" not in result
            else:
                assert result["structuredContent"]["error"] == error

    asyncio.run(run())


@pytest.mark.parametrize(
    "retained", [(False, "oversized-first"), (False, "oversized-after")], indirect=True
)
def test_oversized_recovery(retained: Retained) -> None:
    frame = retained.frame
    reference = retained.reference
    prefix = (
        []
        if len(str(frame.iloc[0]["label"])) > 10000
        else frame.iloc[:2].to_dict("records")
    )

    async def run() -> None:
        app = retained.app.state.mcp_app
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=retained.public),
                headers=mcp_headers(),
            ) as http,
        ):
            for mode in ("http", "mcp"):
                cursor = None
                for _ in range(2 if prefix else 1):
                    arguments = {"job_id": reference, "output_name": "rows"}
                    if cursor is not None:
                        arguments["cursor"] = cursor
                    if mode == "http":
                        response = await http.get(
                            f"{BASE}/results/{reference}/tables/rows/rows",
                            params={} if cursor is None else {"cursor": cursor},
                        )
                        payload = response.json()
                    else:
                        response = await http.post(
                            BASE + "/mcp/",
                            json=tool_call_payload("lyra_get_result_rows", arguments),
                        )
                        payload = tool_payload(response)
                    assert len(response.content) <= MAX_RETRIEVAL_BYTES
                    if "rows" in payload:
                        assert len(payload["rows"]) == len(prefix)
                        cursor = payload["next_cursor"]
                        assert cursor is not None
                    else:
                        error = payload["detail" if mode == "http" else "error"]
                        assert error["code"] == "row_too_large"
                        link = error["download_url"]
                        assert link.startswith(BASE)
                        assert (
                            await http.get(
                                link, headers={"Authorization": "Bearer wrong"}
                            )
                        ).status_code == 403
                        arrow = await http.get(link)
                        assert arrow.status_code == 200
                        pd.testing.assert_frame_equal(
                            pa.ipc.open_stream(arrow.content).read_all().to_pandas(),
                            frame,
                        )

    asyncio.run(run())


@pytest.mark.parametrize(
    "retained", [(False, "oversized-first"), (False, "oversized-after")], indirect=True
)
def test_native_oversized_continuation_and_recovery(
    retained: Retained,
    native_api: NativeAPI,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    reference = retained.reference
    with native_api.serve(retained.public, monkeypatch), native_api.connect() as client:
        cursor = None
        if len(str(retained.frame.iloc[0]["label"])) < 10000:
            page = native_api.resolve(client.results.rows(reference, "rows"))
            assert len(page.rows) == 2
            assert page.rows[0]["label"] == retained.frame.iloc[0]["label"]
            assert page.rows[1]["label"] == retained.frame.iloc[1]["label"]
            cursor = page.next_cursor
            assert cursor is not None
        for _ in range(2):
            with pytest.raises(RowTooLargeError) as caught:
                native_api.resolve(
                    client.results.rows(reference, "rows", cursor=cursor)
                )
            error = caught.value
            assert (error.job_id, error.output_name, error.download_url) == (
                reference,
                "rows",
                retained.url,
            )
        path = tmp_path / "recovered.unusual"
        native_api.resolve(client.results.download_table(reference, "rows", path))
        frame = native_api.resolve(client.results.table(reference, "rows"))
    pd.testing.assert_frame_equal(frame, retained.frame, check_exact=True)
    with pa.ipc.open_stream(path) as reader:
        pd.testing.assert_frame_equal(
            reader.read_all().to_pandas(), frame, check_exact=True
        )


def test_python_page_contract(client: ClientHarness) -> None:
    page = {
        "rows": [
            {
                "integer": "9007199254740993",
                "geometry": "POINT EMPTY",
                "text": " 001 ",
                "null": None,
                "bool": True,
            }
        ],
        "next_cursor": "opaque",
    }
    for cursor in (None, "opaque"):
        client.responses.append(httpx.Response(200, json=page))
        result = client.resolve(
            client.client.results.rows("a" * 32, "rows", cursor=cursor)
        )
        assert result.model_dump() == page
        assert client.requests[-1].url.params.get("cursor") == cursor
        assert client.requests[-1].url.params["limit"] == "100"


@pytest.mark.parametrize(
    "damage",
    ["reference", "missing", "output", "integrity", "cursor-result", "cursor-output"],
)
def test_lookup_and_bound_cursor_errors(
    retained: Retained, database: Database, damage: str
) -> None:
    reference, output = retained.reference, "rows"
    params = {}
    code, status = "invalid_pagination", 422
    if damage.startswith("cursor"):
        page = retained.service.read_rows(
            reference, output, 1, context=RowPageContext(BASE, http_response_budget())
        )
        params["cursor"] = page.next_cursor
        if damage == "cursor-result":
            reference = "0" * 32
        else:
            output = "another"
    elif damage == "reference":
        reference = "bad"
    elif damage in {"missing", "output"}:
        if damage == "missing":
            reference = "0" * 32
        else:
            output = "another"
        code, status = "result_not_found", 404
    else:
        database.rows["storage_registrations"].clear()
        code, status = "result_integrity_error", 500

    async def run() -> None:
        app = retained.app.state.mcp_app
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=retained.public),
                headers=mcp_headers(),
            ) as http,
        ):
            response = await http.get(
                f"{BASE}/results/{reference}/tables/{output}/rows", params=params
            )
            assert response.status_code == status
            error = response.json()["detail"]
            if damage == "reference":
                assert error[0]["loc"] == ["path", "job_id"]
            else:
                assert error["code"] == code
                assert error["job_id"] == reference
                assert error["output_name"] == output
            if damage.startswith("cursor"):
                assert error["parameter"] == "cursor"
            response = await http.post(
                BASE + "/mcp/",
                json=tool_call_payload(
                    "lyra_get_result_rows",
                    {"job_id": reference, "output_name": output, **params},
                ),
            )
            if damage == "reference":
                assert response.json()["result"]["isError"]
                assert not response.json()["result"].get("structuredContent")
            else:
                assert tool_payload(response)["error"] == error

    asyncio.run(run())


@pytest.mark.parametrize("name", ["unknown", "", "a/b", "%2F"])
def test_unknown_names_reach_domain_lookup(
    retained: Retained,
    native_api: NativeAPI,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    name: str,
) -> None:
    ref = retained.reference
    with (
        native_api.serve(retained.public, monkeypatch) as http,
        native_api.connect() as api,
    ):
        for path, call, is_input in (
            (table_rows_path, lambda: api.results.rows(ref, name), False),
            (
                table_download_path,
                lambda: api.results.download_table(ref, name, tmp_path / "out"),
                False,
            ),
            (
                location_input_rows_path,
                lambda: api.results.location_input_rows(ref, name),
                True,
            ),
            (
                location_input_download_path,
                lambda: api.results.download_location_input(
                    ref, name, tmp_path / "out"
                ),
                True,
            ),
        ):
            response = native_api.runner.run(
                http.get(f"{BASE}/{path(ref, name)}", headers=mcp_headers())
            )
            assert response.status_code == 404
            detail = response.json()["detail"]
            assert detail["code"] == "result_not_found"
            assert detail["input_name" if is_input else "output_name"] == name
            with pytest.raises(ResultNotFoundError) as caught:
                native_api.resolve(call())
            assert caught.value.job_id == ref
            assert caught.value.output_name == (None if is_input else name)
            assert caught.value.input_name == (name if is_input else None)


@pytest.mark.anyio
async def test_newline_in_name_reaches_retrieval_route(retained: Retained) -> None:
    # Model a reverse proxy supplying the deployment prefix in the ASGI scope.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=retained.app, root_path="/public/api"),
        base_url=BASE + "/",
        headers=mcp_headers(),
    ) as http:
        response = await http.get(table_rows_path(retained.reference, "line\nbreak"))
    assert response.status_code == 404
    assert response.json()["detail"]["output_name"] == "line\nbreak"
