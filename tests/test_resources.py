import logging
from pathlib import Path
from typing import Annotated, ClassVar
from unittest.mock import Mock

import pandas as pd
import pytest
from lyra.sdk import (
    BaseIO,
    Dependency,
    IOConfig,
    LocalRunner,
    PluginDefinition,
    Resource,
    ResourceConfig,
    SecretReference,
    TableOutput,
    metric,
)
from lyra.sdk.resources import (
    ResourceConfigurationError,
    ResourceScope,
    resource_invocation,
)


class FileSettings(IOConfig):
    path: Path


class FileIO(BaseIO[FileSettings]):
    config_model = FileSettings
    events: ClassVar[list[str]] = []

    def setup(self) -> None:
        self.events.append("setup")

    def close(self) -> None:
        self.events.append("close")

    def read(self) -> pd.DataFrame:
        with self.operation("read"):
            return pd.DataFrame({"value": [self.config.path.read_text()]})


TABLE = TableOutput(columns=[])


@metric(
    name="resource_source", description="Read a file", outputs={"source_table": TABLE}
)
def source(files: Annotated[FileIO, Resource("files")]) -> pd.DataFrame:
    return files.read()


@metric(
    name="resource_root",
    description="Reuse the reader",
    inputs={"table": Dependency("source_table")},
    outputs={"answer": TABLE},
)
def root(
    table: pd.DataFrame, files: Annotated[FileIO, Resource("files")]
) -> pd.DataFrame:
    assert files.read().equals(table)
    return table


def test_shared_cross_plugin_resource_and_fresh_runs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "input.txt"
    path.write_text("hello")
    FileIO.events.clear()
    runner = LocalRunner.from_definitions(
        {
            "a": PluginDefinition(nodes=[source], resources={"files": FileIO}),
            "b": PluginDefinition(nodes=[root], resources={"files": FileIO}),
        },
        resource_config={"files": ResourceConfig(config={"path": path})},
    )
    with caplog.at_level(logging.INFO):
        for _ in range(2):
            assert runner.run("resource_root", {})["answer"].value.tolist() == ["hello"]
    assert FileIO.events == ["setup", "close", "setup", "close"]
    events = [
        record.structured_fields
        for record in caplog.records
        if hasattr(record, "structured_fields")
        and isinstance(record.structured_fields, dict)
    ]
    assert [event["node"] for event in events] == [
        "resource_source",
        "resource_root",
    ] * 2
    assert events[0]["job_id"] != events[2]["job_id"]
    assert all(event["duration_seconds"] >= 0 for event in events)


def test_secret_resolution_and_sanitized_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEST_RESOURCE_SECRET", "private-token")
    path = tmp_path / "secret"
    path.write_text("private-token\n")
    assert SecretReference(env="TEST_RESOURCE_SECRET").resolve() == "private-token"
    assert SecretReference(file=path).resolve() == "private-token"
    with pytest.raises(ValueError, match="exactly one"):
        SecretReference(env="x", file=path)
    with (
        ResourceScope({"files": FileIO}).activate(),
        pytest.raises(ResourceConfigurationError) as error,
    ):
        ResourceScope({"files": FileIO}).get("files")
    assert "files" in str(error.value)
    assert "private-token" not in str(error.value)


def test_cleanup_partial_initialization_and_original_failure() -> None:
    events = []
    original = RuntimeError("setup failed")

    class BrokenIO(BaseIO[IOConfig]):
        def setup(self) -> None:
            events.append(self.resource_name)
            raise original

        def close(self) -> None:
            events.append(self.resource_name + " closed")
            message = "cleanup failed"
            raise ValueError(message)

    scope = ResourceScope({"broken": BrokenIO})
    with pytest.raises(RuntimeError) as error, scope.activate():
        scope.get("broken")
    assert error.value is original
    assert events == ["broken", "broken closed"]
    assert not scope.instances


def test_operation_preserves_results_exceptions_and_ignores_logging_failure() -> None:
    io = BaseIO(IOConfig(), {})
    context = Mock(job_id="job", node="node")
    context.logger.info.side_effect = RuntimeError("logging failed")
    value = pd.DataFrame({"x": [1]})
    error = ValueError("operation failed")
    with ResourceScope({}).activate(), resource_invocation(context):
        with io.operation("read"):
            result = value
        assert result is value
        with (
            pytest.raises(ValueError, match="operation failed") as caught,
            io.operation("read"),
        ):
            raise error
    assert caught.value is error
    assert context.logger.info.call_count == 2


def test_unused_resources_and_distinct_bindings(tmp_path: Path) -> None:
    settings = ResourceConfig(config={"path": tmp_path})
    FileIO.events.clear()
    scope = ResourceScope(
        {"a": FileIO, "b": FileIO, "unused": FileIO}, {"a": settings, "b": settings}
    )
    with scope.activate():
        first = scope.get("a")
        assert scope.get("a") is first
        assert scope.get("b") is not first
    assert FileIO.events == ["setup", "setup", "close", "close"]


def test_incompatible_override_rejected() -> None:
    with pytest.raises(ResourceConfigurationError, match="override"):
        ResourceScope({"files": FileIO}, overrides={"files": BaseIO})


def test_reverse_cleanup_continues_after_failure() -> None:
    closed: list[str] = []

    class ClosingIO(BaseIO[IOConfig]):
        def close(self) -> None:
            closed.append(self.resource_name)
            if self.resource_name == "second":
                message = "close failed"
                raise RuntimeError(message)

    scope = ResourceScope({"first": ClosingIO, "second": ClosingIO})
    with pytest.raises(RuntimeError, match="close failed"), scope.activate():
        assert scope.get("first") is not scope.get("second")
    assert closed == ["second", "first"]


def test_remote_client_fake_and_credentials_are_initialized_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = Mock()
    client.download.return_value = b"content"
    monkeypatch.setenv("IO_TEST_TOKEN", "private-token")

    class RemoteIO(BaseIO[IOConfig]):
        secret_names = ("token",)

        def setup(self) -> None:
            client.authenticate(self.secrets["token"])

        def download(self) -> bytes:
            with self.operation("download"):
                return client.download()

        def close(self) -> None:
            client.close(self.resource_name)

    scope = ResourceScope(
        {"remote": RemoteIO},
        {
            "remote": ResourceConfig(
                secrets={"token": SecretReference(env="IO_TEST_TOKEN")}
            )
        },
    )
    client.authenticate.assert_not_called()
    with scope.activate():
        resource = scope.get("remote")
        assert isinstance(resource, RemoteIO)
        assert resource.download() == b"content"
        assert scope.get("remote") is resource
    client.authenticate.assert_called_once_with("private-token")
    client.close.assert_called_once_with("remote")
