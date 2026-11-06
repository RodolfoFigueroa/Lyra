from importlib import import_module

import pytest
from pydantic import ValidationError

from lyra import api, sdk
from lyra.api.client.async_ import AsyncLyraClient
from lyra.api.client.sync import JobHandle, LyraClient
from lyra.api.exceptions import MetricRunError
from lyra.api.options import RunOptions
from lyra.contracts.jobs import JobCreateRequest, TableJobResult
from lyra.data import Locations, read_location_artifact
from lyra.sdk.plugin import PluginDefinition
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
    assert api.parse_result_ref("lyra://results/example") == "example"


def test_models_and_utilities_are_available_from_owning_modules() -> None:
    assert JobCreateRequest.__module__ == "lyra.contracts.jobs"
    assert Locations.__module__ == "lyra.data.locations"
    assert read_location_artifact.__module__ == "lyra.data.artifact_reader"
    assert "outputs" in TableJobResult.model_fields
    assert callable(get_date_range)


@pytest.mark.parametrize(
    ("name", "module"),
    [
        ("GraphExecutionError", "lyra.runtime.execution"),
        ("GraphExecutor", "lyra.runtime.execution"),
        ("LocalRunner", "lyra.runtime.local"),
    ],
)
def test_execution_interfaces_are_owned_by_runtime(name: str, module: str) -> None:
    assert getattr(import_module(module), name).__module__ == module
    assert not hasattr(sdk, name)


def test_authoring_facade_exports_canonical_declarations() -> None:
    owners = {
        "lyra.contracts.dependencies": ("Input",),
        "lyra.contracts.resources": ("IOConfig", "ResourceConfig", "SecretReference"),
        "lyra.sdk.context": ("LocationContext", "RunContext"),
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
    assert set(sdk.__all__) == {name for names in owners.values() for name in names}
    for owner, names in owners.items():
        for name in names:
            assert getattr(sdk, name) is getattr(import_module(owner), name)


def test_definition_exposes_only_the_authoring_side_of_invocation() -> None:
    for method in (
        "prepare_parameters",
        "prepare_resolved_invocation",
        "invoke",
        "invoke_operation",
        "normalize_result",
        "normalize_location_result",
        "validate_cached_locations",
    ):
        assert not hasattr(PluginDefinition, method)


@pytest.mark.parametrize(
    "name",
    ["FileOutput", "FractionOfLocationArea", "BatchInput", "BatchItem", "PluginResult"],
)
def test_retired_authoring_interfaces_are_absent(name: str) -> None:
    assert not hasattr(sdk, name)
    assert not hasattr(import_module("lyra.sdk.plugin"), name)


def test_manifest_models_have_one_unversioned_interface() -> None:
    models = import_module("lyra.contracts.plugins")
    assert not hasattr(models, "FileOutput")
    assert not hasattr(models, "OutputSpec")
    assert not hasattr(import_module("lyra.contracts.jobs"), "RowIdentityMetadata")
    for name in (
        "PluginInfo",
        "MetricManifest",
        "LocationOperationManifest",
        "PluginManifest",
    ):
        assert getattr(models, name).__module__ == models.__name__
    with pytest.raises(ModuleNotFoundError, match="models"):
        import_module("lyra.sdk.models.plugin_v4")
    assert not hasattr(PluginDefinition, "compiled_manifest")
    for name in ("from_dataframe", "from_series", "from_mapping"):
        assert not hasattr(TableJobResult, name)


@pytest.mark.parametrize(
    "payload",
    [
        {"outputs": {"x": []}, "output_metadata": {}},
        {"outputs": {"x": "[]"}, "output_metadata": {"x": {"columns": []}}},
        {"index": ["a"], "columns": ["x"], "data": [[1]]},
    ],
)
def test_transport_table_validation_survives_constructor_removal(payload: dict) -> None:
    with pytest.raises(ValidationError):
        TableJobResult.model_validate({"job_id": "transport", **payload})


@pytest.mark.parametrize(
    ("module", "names"),
    [
        (
            "dependencies",
            (
                "Input",
                "DependencyManifest",
                "LocationDependencyManifest",
                "LiteralArgument",
            ),
        ),
        ("locations", ("LocationCollection", "LocationProviderManifest")),
    ],
)
def test_moved_contracts_are_not_exported_from_mixed_sdk_modules(
    module: str, names: tuple[str, ...]
) -> None:
    owner = import_module(f"lyra.sdk.{module}")
    for name in names:
        assert not hasattr(owner, name)


@pytest.mark.parametrize(
    "module",
    [
        "lyra.sdk.editions",
        "lyra.sdk.models",
        "lyra.sdk.models.location",
        "lyra.sdk.models.location_payload",
        "lyra.sdk.models.plugin",
        *(
            f"lyra.sdk.{name}"
            for name in (
                "artifacts",
                "cache",
                "execution",
                "local",
                "ipc_codec",
                "location_artifacts",
                "location_resolution",
                "artifact_reader",
                "location_exports",
                "graph",
                "catalog",
                "computation",
                "revisions",
                "preparation",
                "_execution_plan",
                "discovery",
            )
        ),
    ],
)
def test_vacated_modules_are_absent(module: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        import_module(module)


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
