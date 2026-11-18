from pathlib import Path
from unittest.mock import Mock

import ibis
import ibis.expr.types as ir
import pytest
from ibis.backends.duckdb import Backend
from pydantic import ValidationError

from lyra.contracts.errors import ResourceConfigurationError
from lyra.runtime.planning.provider_registry import compile_provider_registry
from lyra.runtime.resources import ResourceScope
from lyra.sdk import ParquetLocationProvider, PluginDefinition, ResourceConfig
from lyra.sdk.parquet import ParquetConfig
from lyra.sdk.provider_schema import ProviderSchema
from lyra.sdk.query_schema import compile_query_schema
from lyra.sdk.resources import resource_metadata
from tests.provider_schema_helpers import declaration, frame


class Places(ParquetLocationProvider):
    id = "places"
    schema = declaration()

    def source_for_test(self) -> ir.Table:
        # Expose the shared acquisition source to verify readiness and reuse.
        return self._base_relation()


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> Mock:
    native = Mock(spec=Backend)
    native.con = Mock()
    native.read_parquet.return_value = ibis.table(
        compile_query_schema(ProviderSchema(Places.schema).schema).schema
    )
    monkeypatch.setattr("ibis.backends.duckdb.Backend", Mock(return_value=native))
    return native


def scope_for(path: object) -> ResourceScope:
    return ResourceScope(
        {"places": Places}, {"places": ResourceConfig(config={"path": path})}
    )


@pytest.mark.parametrize("relative", [False, True])
def test_single_resolved_source_and_owned_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: Mock, *, relative: bool
) -> None:
    directory = tmp_path / "working directory"
    directory.mkdir()
    source = directory / "places.parquet"
    frame().to_parquet(source)
    scope = scope_for(source.name if relative else str(source))
    # Relative paths are interpreted at acquisition, not scope construction.
    monkeypatch.chdir(directory)
    backend.reconnect.assert_not_called()
    with scope.activate():
        provider = scope.get("places")
        assert isinstance(provider, Places)
        assert scope.get("places") is provider
        relation = provider.source_for_test()
        assert relation.schema() == backend.read_parquet.return_value.schema()
        assert provider.source_for_test() is relation
        backend.reconnect.assert_called_once()
        backend.read_parquet.assert_called_once_with(str(source))
        backend.disconnect.assert_not_called()
    backend.disconnect.assert_called_once()
    provider.close()
    backend.disconnect.assert_called_once()
    with pytest.raises(RuntimeError, match="not initialized"):
        provider.source_for_test()


def test_discovery_and_unused_provider_do_not_touch_files(
    monkeypatch: pytest.MonkeyPatch, backend: Mock
) -> None:
    unavailable = "/unavailable/places.parquet"
    check = Mock(side_effect=AssertionError("filesystem accessed during discovery"))
    monkeypatch.setattr(Path, "resolve", check)
    monkeypatch.setattr(Path, "stat", check)
    monkeypatch.setattr(Path, "open", check)
    monkeypatch.setattr("pyarrow.parquet.read_schema", check)
    registry = compile_provider_registry(
        {"city": PluginDefinition(resources={"places": Places})}
    )
    assert registry["places"].declaration.query_capable
    assert registry["places"].membership_capable
    assert registry["places"].declaration.membership_capable
    assert registry["places"].query is not None
    metadata = resource_metadata(Places)
    assert metadata["secrets"] == []
    config = ParquetConfig(path=unavailable)
    provider = Places(config, {})
    assert provider.resolve([]).empty
    assert provider.existing_ids([]) == set()
    provider.close()
    with scope_for(unavailable).activate():
        pass
    check.assert_not_called()
    backend.reconnect.assert_not_called()
    backend.read_parquet.assert_not_called()
    backend.disconnect.assert_not_called()


@pytest.mark.parametrize(
    "path",
    [
        None,
        123,
        b"places.parquet",
        Path("places.parquet"),
        ["a.parquet", "b.parquet"],
        {"path": "a.parquet"},
        "",
        " ",
        "a\x00.parquet",
        "*.parquet",
        "a?.parquet",
        "a[12].parquet",
        "https://example.org/a.parquet",
        "s3://bucket/a.parquet",
        "file:///tmp/a.parquet",
        "//server/a.parquet",
    ],
)
def test_invalid_configuration_precedes_backend(path: object, backend: Mock) -> None:
    with pytest.raises(ValidationError):
        ParquetConfig.model_validate({"path": path})
    scope = scope_for(path)
    with pytest.raises(ResourceConfigurationError), scope.activate():
        scope.get("places")
    backend.reconnect.assert_not_called()
    backend.read_parquet.assert_not_called()


@pytest.mark.parametrize(
    "configuration",
    [{}, {"path": "a.parquet", "columns": {"id": "location_id"}}],
)
def test_configuration_requires_only_supported_fields(configuration: dict) -> None:
    with pytest.raises(ValidationError):
        ParquetConfig.model_validate(configuration)


def test_symlink_cannot_expand_to_a_backend_glob(tmp_path: Path, backend: Mock) -> None:
    source = tmp_path / "places[12].parquet"
    source.touch()
    link = tmp_path / "places.parquet"
    link.symlink_to(source)
    scope = scope_for(str(link))
    with scope.activate(), pytest.raises(ValueError, match="glob patterns"):
        scope.get("places")
    backend.reconnect.assert_not_called()
    backend.read_parquet.assert_not_called()


@pytest.mark.parametrize("kind", ["directory", "missing", "unreadable"])
def test_file_availability_checked_only_at_acquisition(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: Mock
) -> None:
    source = tmp_path / "places.parquet"
    if kind == "directory":
        source.mkdir()
    elif kind == "unreadable":
        source.touch()
        monkeypatch.setattr(Path, "open", Mock(side_effect=PermissionError("denied")))
    scope = scope_for(str(source))
    expected = ValueError if kind == "directory" else OSError
    with scope.activate(), pytest.raises(expected):
        scope.get("places")
    backend.reconnect.assert_not_called()
    backend.read_parquet.assert_not_called()
    backend.disconnect.assert_not_called()


@pytest.mark.parametrize("failure", [None, "connect", "extension", "read"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_native_setup_and_cleanup_failure_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str | None,
    *,
    cleanup_fails: bool,
) -> None:
    source = tmp_path / "places.parquet"
    frame().to_parquet(source)
    primary = RuntimeError("primary failure")
    cleanup = ValueError("cleanup failure")
    connection = Mock()
    connect = Mock(return_value=connection)
    extensions = Mock()
    read = Mock(
        return_value=ibis.table(
            compile_query_schema(ProviderSchema(Places.schema).schema).schema
        )
    )
    monkeypatch.setattr("duckdb.connect", connect)
    monkeypatch.setattr(Backend, "_load_extensions", extensions)
    monkeypatch.setattr(Backend, "read_parquet", read)
    if failure == "connect":
        connect.side_effect = primary
    elif failure == "extension":
        extensions.side_effect = primary
    elif failure == "read":
        read.side_effect = primary
    if cleanup_fails:
        connection.close.side_effect = cleanup
    scope = scope_for(str(source))
    provider = None

    def acquire() -> None:
        nonlocal provider
        with scope.activate():
            provider = scope.get("places")
            assert scope.get("places") is provider

    expected = primary if failure else cleanup if cleanup_fails else None
    if expected is None:
        acquire()
    else:
        with pytest.raises(type(expected)) as caught:
            acquire()
        assert caught.value is expected
    connect.assert_called_once()
    if failure != "connect":
        extensions.assert_called_once_with(["spatial"])
    assert connection.close.call_count == (0 if failure == "connect" else 1)
    if provider is not None:
        provider.close()
        connection.close.assert_called_once()


def test_partial_setup_cannot_be_reused(tmp_path: Path, backend: Mock) -> None:
    source = tmp_path / "places.parquet"
    frame().to_parquet(source)
    backend.read_parquet.side_effect = RuntimeError("read failed")
    scope = scope_for(str(source))
    with scope.activate():
        with pytest.raises(RuntimeError, match="read failed"):
            scope.get("places")
        with pytest.raises(ResourceConfigurationError, match="did not complete"):
            scope.get("places")
        provider = scope.instances["places"]
        assert isinstance(provider, Places)
        with pytest.raises(RuntimeError, match="not initialized"):
            provider.source_for_test()
        backend.disconnect.assert_not_called()
    backend.disconnect.assert_called_once()
