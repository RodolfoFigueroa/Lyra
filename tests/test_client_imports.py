"""Client imports and JSON operations do not load execution or native tables."""

import pytest

from test_support.imports import assert_fresh_imports

HIGHER_LAYERS = ("lyra.sdk", "lyra.data", "lyra.runtime", "lyra.server")
NATIVE = ("numpy", "pandas", "geopandas", "pyarrow", "pandera", "shapely", "pyproj")


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
            with patch("aiohttp.ClientSession", FakeSession):
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
        payload["output_metadata"]["sites"] = {"columns": []}
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
