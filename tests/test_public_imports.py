from importlib import import_module

import pytest
from pydantic import BaseModel, ValidationError

from lyra import api, sdk
from lyra.api.client.async_ import AsyncLyraClient
from lyra.api.client.sync import JobHandle, LyraClient
from lyra.api.exceptions import MetricRunError
from lyra.api.options import RunOptions
from lyra.contracts.jobs import JobCreateRequest, TableJobResult
from lyra.runtime.results import RunResult
from lyra.utils.date import get_date_range
from test_support.imports import assert_fresh_imports


def test_root_version_and_cli_load_without_feature_imports() -> None:
    assert_fresh_imports(
        ["lyra", "lyra.version", "lyra._cli"],
        blocked=tuple(
            f"lyra.{name}"
            for name in (
                "api",
                "contracts",
                "data",
                "runtime",
                "sdk",
                "server",
                "utils",
            )
        ),
        assertions="""
        from importlib.metadata import version
        from pathlib import Path
        import lyra
        assert Path(lyra.__file__).resolve() == Path("src/lyra/__init__.py").resolve()
        assert lyra.version.APP_VERSION == version("lyra")
        """,
    )


def test_client_conveniences_resolve_to_canonical_definitions() -> None:
    assert api.LyraClient is LyraClient
    assert api.AsyncLyraClient is AsyncLyraClient
    assert api.JobHandle is JobHandle
    assert api.RunOptions is RunOptions
    assert api.MetricRunError is MetricRunError


def test_models_and_utilities_are_available_from_supported_imports() -> None:
    request = JobCreateRequest(metric="example", inputs={})
    assert JobCreateRequest.model_validate_json(request.model_dump_json()) == request
    assert isinstance(RunResult, type)
    assert "outputs" in TableJobResult.model_fields
    assert get_date_range(2, 2024) == ("2024-02-01", "2024-02-29")


@pytest.mark.parametrize(
    ("name", "module"),
    [
        ("GraphExecutionError", "lyra.runtime.execution"),
        ("GraphExecutor", "lyra.runtime.execution"),
        ("LocalRunner", "lyra.runtime.local"),
    ],
)
def test_execution_interfaces_are_available_from_runtime(
    name: str, module: str
) -> None:
    assert isinstance(getattr(import_module(module), name), type)


def test_authoring_facade_exports_canonical_declarations() -> None:
    supported_imports = {
        "lyra.contracts.dependencies": ("Input",),
        "lyra.contracts.resources": ("IOConfig", "ResourceConfig", "SecretReference"),
        "lyra.sdk.context": ("RunContext",),
        "lyra.sdk.dependencies": ("Dependency", "LocationDependency"),
        "lyra.sdk.location_schema": ("LocationOutputSchema",),
        "lyra.sdk.locations": ("LocationProvider",),
        "lyra.sdk.parameters": ("MetricParameters",),
        "lyra.sdk.plugin": (
            "NodeDescription",
            "NodeImplementation",
            "PluginDefinition",
            "location_operation",
            "metric",
        ),
        "lyra.sdk.resources": ("BaseIO", "Resource"),
    }
    for module, names in supported_imports.items():
        for name in names:
            assert getattr(sdk, name) is getattr(import_module(module), name)


@pytest.mark.parametrize(
    "name",
    ["PluginInfo", "MetricManifest", "LocationOperationManifest", "PluginManifest"],
)
def test_manifest_models_are_available_from_contracts(name: str) -> None:
    assert issubclass(getattr(import_module("lyra.contracts.plugins"), name), BaseModel)


@pytest.mark.parametrize(
    "payload",
    [
        {"outputs": {"x": []}, "output_metadata": {}},
        {"outputs": {"x": "[]"}, "output_metadata": {"x": {"columns": []}}},
        {"index": ["a"], "columns": ["x"], "data": [[1]]},
    ],
)
def test_transport_table_rejects_malformed_payloads(payload: dict) -> None:
    with pytest.raises(ValidationError):
        TableJobResult.model_validate({"job_id": "transport", **payload})


def test_date_helpers_load_without_native_or_earth_engine_modules() -> None:
    assert_fresh_imports(
        ["lyra.utils", "lyra.utils.date"],
        blocked=(
            "lyra.utils.geometry",
            "lyra.utils.ee",
            "ee",
            "lyra.data",
            "geopandas",
            "numpy",
            "pandas",
            "pyarrow",
            "shapely",
            "pyproj",
        ),
        assertions="""
        from lyra.utils.date import get_date_range
        assert get_date_range(2, 2024) == ("2024-02-01", "2024-02-29")
        """,
    )


def test_geometry_helper_loads_without_earth_engine() -> None:
    assert_fresh_imports(["lyra.utils.geometry"], blocked=("lyra.utils.ee", "ee"))


@pytest.mark.parametrize(
    "first",
    [
        "lyra.sdk",
        "lyra.runtime.locations",
        "lyra.runtime.planning.node_declarations",
        "lyra.runtime.planning.provider_registry",
        "lyra.runtime.planning.native_bindings",
    ],
)
def test_declarations_compile_in_fresh_import_orders(first: str) -> None:
    assert_fresh_imports(
        [first, "lyra.sdk"],
        blocked=("lyra.server",),
        assertions="""
        from lyra.sdk import BaseIO, PluginDefinition
        from lyra.runtime.planning.native_bindings import bind_native_definitions
        from lyra.runtime.planning.provider_registry import compile_provider_registry
        definition = PluginDefinition(resources={"plain": BaseIO})
        definitions = bind_native_definitions({"test": definition})
        assert definitions["test"].providers == ()
        assert compile_provider_registry(definitions) == {}
        """,
    )
