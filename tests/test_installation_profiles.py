"""Verify feature closures from the universal lock without installing packages."""

import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]

import pytest

from test_support.imports import assert_fresh_imports

BASE = {"jsonschema", "pydantic", "typing-extensions"}
CLIENT = {"aiofiles", "aiohttp"}
TABLES = {"geopandas", "numpy", "pandas", "pyproj", "shapely"}
DATA = TABLES | {"pyarrow", "pyogrio"}
SDK = DATA | {"pandera"}
RUNTIME = SDK | {"filelock"}
SERVER = RUNTIME | {"fastapi", "mcp", "redis", "rq", "starlette", "uvicorn"}
EARTH_ENGINE = DATA | {"earthengine-api"}


@pytest.mark.parametrize(
    ("extras", "feature_packages"),
    [
        ((), set()),
        (("client",), CLIENT),
        (("tables",), TABLES),
        (("client", "tables"), CLIENT | TABLES),
        (("data",), DATA),
        (("sdk",), SDK),
        (("runtime",), RUNTIME),
        (("server",), SERVER),
        (("earth-engine",), EARTH_ENGINE),
    ],
)
def test_locked_feature_closure(
    extras: tuple[str, ...], feature_packages: set[str]
) -> None:
    uv = shutil.which("uv")
    assert uv is not None
    command = [
        uv,
        "export",
        "--offline",
        "--frozen",
        "--no-dev",
        "--no-emit-project",
        "--no-header",
        "--no-hashes",
        "--no-annotate",
    ]
    for extra in extras:
        command.extend(("--extra", extra))
    result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        command,
        capture_output=True,
        text=True,
        check=True,
    )
    names = {line.split("==", 1)[0] for line in result.stdout.splitlines()}
    assert BASE | feature_packages <= names
    # GeoPandas can select pyogrio transitively for the tables extra.
    allowed = feature_packages | ({"pyogrio"} if "tables" in extras else set())
    assert names & (CLIENT | SERVER | EARTH_ENGINE) <= allowed


@pytest.mark.parametrize(
    ("command", "dependency", "extra"),
    [
        ("plugin([])", "pandera", "sdk"),
        ("admin([])", "aiohttp", "client"),
        ("run_module('lyra.server.main', run_name='__main__')", "fastapi", "server"),
        (
            "run_module('lyra.server.worker_launcher', run_name='__main__')",
            "rq",
            "server",
        ),
        ("run_module('lyra.server.artifacts', run_name='__main__')", "redis", "server"),
    ],
)
def test_command_reports_absent_extra(
    command: str, dependency: str, extra: str
) -> None:
    assert_fresh_imports(
        ["lyra._cli"],
        blocked=("lyra.utils.ee",),
        unavailable=(dependency,),
        assertions=f"""
        from lyra._cli import admin, plugin
        from runpy import run_module
        try:
            {command}
        except SystemExit as exc:
            assert "lyra[{extra}]" in str(exc) and "{dependency}" in str(exc)
        else:
            raise AssertionError("Command should require its extra")
        """,
    )


@pytest.mark.parametrize(
    ("module", "missing", "extra"),
    [
        ("lyra.runtime.local", "filelock", "runtime"),
        ("lyra.utils.geometry", "geopandas", "data"),
        ("lyra.utils.ee", "ee", "earth-engine"),
    ],
)
def test_feature_reports_absent_extra(module: str, missing: str, extra: str) -> None:
    assert_fresh_imports(
        [],
        blocked=(),
        unavailable=(missing,),
        assertions=f"""
        try:
            importlib.import_module({module!r})
        except ImportError as exc:
            assert "lyra[{extra}]" in str(exc), str(exc)
        else:
            raise AssertionError("Feature should require its extra")
        """,
    )


@pytest.mark.parametrize(
    ("command", "module"),
    [
        ("plugin([])", "lyra.sdk.plugin_cli"),
        ("admin([])", "lyra.api.admin_cli"),
        ("run_module('lyra.server.main', run_name='__main__')", "uvicorn"),
    ],
)
def test_command_propagates_unrelated_import(command: str, module: str) -> None:
    assert_fresh_imports(
        ["lyra._cli"],
        blocked=(),
        assertions=f"""
        from lyra._cli import admin, plugin
        from runpy import run_module
        error = ModuleNotFoundError("broken implementation", name="unrelated")
        class Broken(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == {module!r}:
                    raise error
        sys.meta_path.insert(0, Broken())
        try:
            {command}
        except ModuleNotFoundError as exc:
            assert exc is error
        else:
            raise AssertionError("Expected the original implementation error")
        """,
    )


def test_earth_engine_helpers_import_without_initialization() -> None:
    assert_fresh_imports(
        [],
        blocked=("lyra.runtime", "lyra.server", "lyra.sdk", "pandera"),
        assertions="""
        from unittest.mock import patch
        with (
            patch('google.auth.default') as credentials,
            patch('ee.Initialize') as initialize,
        ):
            from lyra.utils.ee import convert_polygon_to_ee
            initialize.assert_not_called()
            credentials.assert_not_called()
        """,
    )
