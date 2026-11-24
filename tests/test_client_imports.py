"""Client imports and JSON operations do not load execution or native tables."""

import pytest

from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.planning.discovery import Discovery
from lyra.sdk.plugin import PluginDefinition
from test_support.imports import assert_fresh_imports
from test_support.location_resolution import Provider
from test_support.routing_plugins import provider as route_provider
from test_support.routing_plugins import score_plugin

HIGHER_LAYERS = ("lyra.sdk", "lyra.data", "lyra.runtime", "lyra.server")
NATIVE = (
    "numpy",
    "pandas",
    "geopandas",
    "pyarrow",
    "pandera",
    "shapely",
    "pyproj",
    "ibis",
    "duckdb",
    "psycopg",
)


def test_result_description_contract_without_runtime_dependencies() -> None:
    assert_fresh_imports(
        ["lyra.contracts.results"],
        blocked=HIGHER_LAYERS + NATIVE + ("sqlalchemy", "rq"),
        assertions="""
        from lyra.contracts.results import ResultDescription
        description = ResultDescription.model_validate({
            "result_ref": "a" * 32, "execution_id": "b" * 32,
            "metric_name": "metric", "parameters": {},
            "outputs": {"data": {
                "scope": "unpartitioned", "owner_scope": None,
                "table_schema": {"columns": []},
            }},
        })
        assert description.outputs["data"].owner_scope is None
        serialized = description.model_dump_json()
        assert ResultDescription.model_validate_json(serialized) == description
        assert ResultDescription.model_json_schema()["properties"]["outputs"]
        """,
    )


def test_basic_clients_without_native_dependencies() -> None:
    assert_fresh_imports(
        [
            "lyra.api",
            "lyra.api.client.sync",
            "lyra.api.client.async_",
            "lyra.api.admin_cli",
        ],
        blocked=HIGHER_LAYERS + NATIVE,
        assertions="""
        import asyncio
        import httpx
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from lyra.api import LyraClient, AsyncLyraClient
        from lyra._cli import admin as main
        from tests.client_helpers import (
            FakeSession, FakeAsyncResponse, _result_response,
        )

        try:
            main(["--help"])
        except SystemExit as exc:
            assert exc.code == 0
        with TemporaryDirectory() as directory:
            config = Path(directory) / "lyra.toml"
            config.write_text(Path("config.example.toml").read_text())
            assert main(["config", "validate", str(config)]) == 0
            target = Path(directory) / "result.json"
            payload = _result_response()
            def replies():
                FakeSession.responses = [
                    FakeAsyncResponse(payload=payload),
                    FakeAsyncResponse(chunks=[b'{"raw":', b'true}']),
                ]
            native_client = httpx.Client
            def response(request):
                if request.url.path.endswith("/download"):
                    return httpx.Response(200, content=b'{"raw":true}')
                return httpx.Response(200, json=payload)
            def make_client(**kwargs):
                return native_client(transport=httpx.MockTransport(response), **kwargs)
            with (
                patch("httpx.AsyncClient", FakeSession),
                patch("httpx.Client", make_client),
            ):
                replies()
                with LyraClient("example.test") as client:
                    assert client.results.get("job-1").job_id == "job-1"
                    client.results.download("job-1", target)
                assert target.read_bytes() == b'{"raw":true}'
                async def run():
                    replies()
                    async with AsyncLyraClient("example.test") as client:
                        assert (await client.results.get("job-1")).job_id == "job-1"
                        await client.results.download("job-1", target)
                asyncio.run(run())
                assert target.read_bytes() == b'{"raw":true}'
        assert "lyra.api.client._tables" not in sys.modules
        """,
    )


def test_describe_clients_without_runtime_or_native_dependencies() -> None:
    assert_fresh_imports(
        ["lyra.api"],
        blocked=HIGHER_LAYERS + NATIVE + ("sqlalchemy", "rq"),
        assertions="""
        import asyncio
        from functools import partial
        from unittest.mock import patch
        import httpx
        from lyra.api import LyraClient, AsyncLyraClient
        from lyra.contracts.results import ResultDescription

        description = ResultDescription(
            result_ref="a" * 32, execution_id="b" * 32,
            metric_name="metric", parameters={"default": None}, outputs={},
        )
        for preview in (False, True):
            def handle(request):
                if request.url.path.endswith("/download"):
                    return httpx.Response(200, json=description.model_dump())
                payload = {
                    "kind": "preview", "result_ref": description.result_ref,
                    "execution_id": description.execution_id,
                    "omitted": ["description"],
                    "download_url": "https://example.test/description/download",
                } if preview else {
                    "kind": "complete", "description": description.model_dump(),
                }
                return httpx.Response(200, json=payload)
            transport = httpx.MockTransport(handle)
            with (
                patch("httpx.Client", partial(httpx.Client, transport=transport)),
                patch("httpx.AsyncClient", partial(
                    httpx.AsyncClient, transport=transport,
                )),
            ):
                with LyraClient("example.test") as client:
                    result = client.results.describe(description.result_ref)
                assert result == description
                async def run():
                    async with AsyncLyraClient("example.test") as client:
                        result = await client.results.describe(description.result_ref)
                    assert result == description
                asyncio.run(run())
        assert "lyra.api.client._tables" not in sys.modules
        """,
    )


def test_conversion_without_execution_or_arrow() -> None:
    assert_fresh_imports(
        ["lyra.api"],
        blocked=(*HIGHER_LAYERS, "pandera"),
        unavailable=("pyarrow",),
        assertions="""
        from lyra.api.client._tables import decode_tables
        from lyra.contracts.jobs import TableJobResult
        from tests.client_helpers import _result_response
        payload = _result_response()
        payload["outputs"]["sites"] = {"type": "FeatureCollection", "features": [{
            "type": "Feature", "geometry": {"type": "Point", "coordinates": [3, 4]},
            "properties": {"id": 1},
        }]}
        payload["output_metadata"]["result"] = {"columns": [{
            "name": "value", "dtype": "int64", "unit": None, "description": "Value"
        }]}
        payload["output_metadata"]["sites"] = {"geometry": "geometry", "columns": [
            {"name": "id", "dtype": "int64", "unit": None, "description": "ID"},
            {"name": "geometry", "dtype": "geometry", "unit": None,
             "description": "Geometry", "parameters": {"crs": "EPSG:4326"}}
        ]}
        result = TableJobResult.model_validate(payload)
        tables = decode_tables(result)
        assert list(tables) == list(result.outputs)
        assert tables["result"].to_dict("records") == result.outputs["result"]
        sites = tables["sites"]
        assert sites.crs.to_epsg() == 4326 and sites.geometry.iloc[0].x == 3
        assert not any(n == "pyarrow" or n.startswith("pyarrow.") for n in sys.modules)
        """,
    )


@pytest.mark.parametrize("dependency", ["pandas", "geopandas"])
@pytest.mark.parametrize("failure", ["missing", "transitive", "broken"])
def test_conversion_import_errors(dependency: str, failure: str) -> None:
    assert_fresh_imports(
        ["lyra.api"],
        blocked=HIGHER_LAYERS,
        assertions=f"""
        dependency = {dependency!r}
        failure = {failure!r}
        if dependency == "geopandas":
            import pandas
        error = (ImportError("broken installation") if failure == "broken" else
                 ModuleNotFoundError("missing dependency", name=(dependency
                     if failure == "missing" else "unrelated_dependency")))
        class Missing(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == dependency:
                    raise error
        sys.meta_path.insert(0, Missing())
        try:
            importlib.import_module("lyra.api.client._tables")
        except ImportError as exc:
            if failure == "missing":
                assert "lyra[client,tables]" in str(exc)
                assert dependency in str(exc).lower()
                assert exc.__cause__ is error
            else:
                assert exc is error
        else:
            raise AssertionError("Conversion should require its dependency")
        """,
    )


def test_arrow_download_without_native_dependencies() -> None:
    assert_fresh_imports(
        ["lyra.api"],
        blocked=HIGHER_LAYERS + NATIVE,
        assertions="""
        import asyncio
        from functools import partial
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        import httpx
        from lyra.api import LyraClient, AsyncLyraClient
        from lyra.contracts.errors import ResultNotFoundError

        body = b'unchanged stream bytes'
        def respond(request):
            if '/missing/' in request.url.path:
                return httpx.Response(404, json={'detail': {
                    'code': 'result_not_found', 'message': 'Absent',
                    'result_ref': 'a' * 32, 'output_name': 'missing',
                }})
            return httpx.Response(200, content=body, headers={
                'Content-Type': 'application/vnd.apache.arrow.stream',
            })
        mock = httpx.MockTransport(respond)
        with (
            TemporaryDirectory() as directory,
            patch('httpx.Client', partial(httpx.Client, transport=mock)),
            patch('httpx.AsyncClient', partial(httpx.AsyncClient, transport=mock)),
        ):
            path = Path(directory) / 'table.anything'
            with LyraClient('example.test') as client:
                assert client.results.download_table('a' * 32, 'rows', path) is None
                try:
                    client.results.table('a' * 32, 'missing')
                except ResultNotFoundError:
                    pass
                else:
                    raise AssertionError('Expected lookup failure')
            assert path.read_bytes() == body
            async def run():
                async with AsyncLyraClient('example.test') as client:
                    outcome = await client.results.download_table(
                        'a' * 32, 'rows', path
                    )
                    assert outcome is None
            asyncio.run(run())
            assert path.read_bytes() == body
        assert 'lyra.api.client._arrow' not in sys.modules
        """,
    )


def test_arrow_conversion_missing_dependency() -> None:
    assert_fresh_imports(
        ["lyra.api"],
        blocked=(*HIGHER_LAYERS, "pandera"),
        unavailable=("pyarrow",),
        assertions="""
        try:
            importlib.import_module('lyra.api.client._arrow')
        except ImportError as exc:
            assert 'lyra[client,data]' in str(exc)
            assert 'pyarrow' in str(exc)
        else:
            raise AssertionError('Arrow conversion requires pyarrow')
        """,
    )


def test_submission_without_native_dependencies() -> None:
    assert_fresh_imports(
        ["lyra.api", "lyra.data.interventions"],
        blocked=("lyra.sdk", "lyra.runtime", "lyra.server", *NATIVE),
        assertions="""
        import asyncio
        from functools import partial
        from unittest.mock import patch
        import httpx
        from lyra.api import AsyncLyraClient, LyraClient
        from lyra.data.interventions import Intervention
        from tests.client_helpers import _job_response
        change = Intervention(id="p", revision="1", catalog="places", remove=["001"])
        def handle(request):
            assert request.method == "POST"
            return httpx.Response(202, json=_job_response())
        transport = httpx.MockTransport(handle)
        with patch("httpx.Client", partial(httpx.Client, transport=transport)):
            with LyraClient("example.test") as client:
                for changes in (None, [], [change]):
                    client.jobs.create("metric", interventions=changes)
        async def run():
            async with AsyncLyraClient("example.test") as client:
                for changes in (None, [], [change]):
                    await client.jobs.create("metric", interventions=changes)
        configured = partial(httpx.AsyncClient, transport=transport)
        with patch("httpx.AsyncClient", configured):
            asyncio.run(run())
        """,
    )


def test_additions_submission_with_tables_extra() -> None:
    catalog = compile_live_catalog(
        {
            "metrics": score_plugin(target=Provider.id),
            "providers": PluginDefinition(
                resources={"locations": Provider, "reference": route_provider("other")}
            ),
        }
    )
    detail = Discovery(catalog).inspect("score").model_dump(mode="json")
    assert_fresh_imports(
        ["lyra.api"],
        blocked=("lyra.sdk", "lyra.runtime", "lyra.server", "pandera"),
        unavailable=("pyarrow",),
        assertions=f"""
        import json
        from functools import partial
        from unittest.mock import patch
        import geopandas as gpd
        import httpx
        from shapely.geometry import Point
        from lyra.api import LyraClient
        from lyra.data.interventions import Intervention
        from tests.client_helpers import _job_response
        detail = {detail!r}
        def handle(request):
            if request.method == "GET":
                return httpx.Response(200, json=detail)
            row, = json.loads(request.content)["interventions"][0]["add"]
            assert row == dict(location_id="001", original="01", geometry="POINT (1 2)")
            return httpx.Response(202, json=_job_response())
        frame = gpd.GeoDataFrame(dict(location_id=["001"], original=["01"]),
                                 geometry=[Point(1, 2)], crs=4326)
        change = Intervention(id="p", revision="1", catalog="test_units", add=frame)
        transport = httpx.MockTransport(handle)
        with patch("httpx.Client", partial(httpx.Client, transport=transport)):
            with LyraClient("example.test") as client:
                assert client.jobs.create("score", interventions=[change]).job_id
        """,
    )
