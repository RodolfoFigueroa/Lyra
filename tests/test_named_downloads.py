"""Exact-name Arrow lookups over isolated storage and actual transport envelopes."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pyarrow as pa
import pytest
from starlette.applications import Starlette
from starlette.routing import Mount

from lyra.contracts.errors import ResultNotFoundError
from lyra.contracts.retrieval_paths import (
    location_input_download_path,
    table_download_path,
)
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.server.main import create_app
from tests.arrow_download_helpers import BASE, Retained, retained
from tests.config_helpers import api_credentials, load_test_config
from tests.location_input_helpers import publish_inputs
from tests.location_input_helpers import retained as retained_selections
from tests.mcp_helpers import mcp_headers, tool_call_payload, tool_payload
from tests.provider_schema_helpers import frame
from tests.retrieval_client_helpers import NativeAPI, native_api
from tests.runtime_helpers import make_runtime
from tests.sqlite_helpers import database

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["database", "native_api", "retained"]


@pytest.mark.usefixtures("database")
@pytest.mark.parametrize("name", ["..", "a/b", "%2F", "line\nbreak", "雪 /%?#"])
@pytest.mark.parametrize("parameter", ["output_name", "input_name"])
def test_named_downloads_return_selected_values(
    native_api: NativeAPI,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    name: str,
    parameter: str,
) -> None:
    config = load_test_config(tmp_path)
    config.api.public_base_url = BASE
    config.mcp.enabled = True
    with SQLiteStore(config.result_store_path) as store:
        direct, _ = retained_selections(store)
        ref = publish_inputs(store, {name: direct}, output_name=name)
    config.result_store_path.touch()
    app = create_app(config, credentials=api_credentials())
    app.state.runtime = make_runtime(config)
    public = Starlette(routes=[Mount("/public/api", app)])
    mcp = app.state.mcp_app
    with native_api.serve(public, monkeypatch) as http, native_api.connect() as api:
        if parameter == "output_name":
            tool, download = "lyra_download_table", api.results.download_table
            expected = {"location_id": ["one"], "row_key": ["r"], "value": [1]}
        else:
            tool, download = (
                "lyra_download_location_input",
                api.results.download_location_input,
            )
            expected = {
                "location_id": ["a", "b"],
                "count": [1, None],
                "geometry": frame().geometry.to_wkb().tolist(),
            }

        async def fetch_link() -> bytes:
            async with mcp.router.lifespan_context(mcp):
                response = await http.post(
                    BASE + "/mcp/",
                    headers=mcp_headers(),
                    json=tool_call_payload(tool, {"job_id": ref, parameter: name}),
                )
            link = tool_payload(response)
            assert link[parameter] == name
            url = link["download_url"]
            assert url.startswith(BASE + "/results/")
            assert (await http.get(url)).status_code == 401
            response = await http.get(url, headers=mcp_headers())
            assert response.status_code == 200
            return response.content

        body = native_api.runner.run(fetch_link())
        assert pa.ipc.open_stream(body).read_all().to_pydict() == expected
        path = tmp_path / "download.arrow"
        native_api.resolve(download(ref, name, path))
        assert pa.ipc.open_stream(path).read_all().to_pydict() == expected


@pytest.mark.parametrize(
    "name", ["population", "", "..", "a/b", "%2F", "line\nbreak", "雪 /%?#"]
)
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
            (
                table_download_path,
                lambda: api.results.download_table(ref, name, tmp_path / "out"),
                False,
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
            assert detail["input_name"] == (name if is_input else None)
            assert detail["output_name"] == (None if is_input else name)
            omitted = native_api.runner.run(
                http.get(str(response.request.url).split("?")[0], headers=mcp_headers())
            )
            assert omitted.status_code == 422
            with pytest.raises(ResultNotFoundError) as caught:
                native_api.resolve(call())
            assert caught.value.job_id == ref
            assert caught.value.output_name == (None if is_input else name)
            assert caught.value.input_name == (name if is_input else None)
