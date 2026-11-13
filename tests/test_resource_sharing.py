from collections.abc import Mapping
from typing import Annotated, ClassVar
from unittest.mock import Mock, call

import geopandas as gpd
import pytest

from lyra.contracts.resources import IOConfig, ResourceConfig, SecretReference
from lyra.runtime.resources import ResourceScope
from lyra.sdk.resources import BaseIO, Resource
from tests.provider_helpers import FakeProvider


class Settings(IOConfig):
    label: str


class RecordingIO(BaseIO[Settings]):
    config_model = Settings
    secret_names = ("token",)
    events: ClassVar[list[tuple[str, str]]] = []

    def __init__(self, config: Settings, secrets: Mapping[str, str]) -> None:
        super().__init__(config, secrets)
        self.ready = False
        self.events.append(("construct", config.label))

    def setup(self) -> None:
        self.ready = True
        self.events.append(("setup", self.resource_name))

    def read(self) -> str:
        assert self.ready
        return f"{self.config.label}:{self.secrets['token']}"

    def close(self) -> None:
        assert self.ready
        self.events.append(("close", self.resource_name))
        self.ready = False


class ReaderIO(RecordingIO):
    database: Annotated[RecordingIO, Resource("database")]

    def setup(self) -> None:
        assert self.database.read()
        super().setup()

    def read(self) -> str:
        return f"{super().read()}/{self.database.read()}"

    def close(self) -> None:
        assert self.database.read()
        super().close()


class InheritedReader(ReaderIO):
    pass


class RegionalReader(ReaderIO):
    database: Annotated[RecordingIO, Resource("regional")]


class ConsumerIO(RecordingIO):
    left: Annotated[ReaderIO, Resource("left")]
    right: Annotated[ReaderIO, Resource("right")]

    def setup(self) -> None:
        assert self.left.read()
        assert self.right.read()
        super().setup()

    def read(self) -> str:
        return f"{super().read()}/{self.left.read()}/{self.right.read()}"

    def close(self) -> None:
        assert self.left.read()
        assert self.right.read()
        super().close()


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    recorded: list[tuple[str, str]] = []
    monkeypatch.setattr(RecordingIO, "events", recorded)
    return recorded


def configuration(*names: str) -> dict[str, ResourceConfig]:
    return {
        name: ResourceConfig(
            config={"label": name},
            secrets={"token": SecretReference(env=name.upper())},
        )
        for name in names
    }


@pytest.mark.parametrize("direct_first", [True, False])
def test_diamond_sharing_and_dependency_safe_cleanup(
    *,
    direct_first: bool,
    events: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names = ("database", "left", "right", "consumer")
    for name in names:
        monkeypatch.setenv(name.upper(), f"{name}-secret")
    scope = ResourceScope(
        {
            "database": RecordingIO,
            "left": ReaderIO,
            "right": InheritedReader,
            "consumer": ConsumerIO,
        },
        configuration(*names),
    )
    previous = None
    for _ in range(2):
        with scope.activate():
            direct = scope.get("database") if direct_first else None
            consumer = scope.get("consumer")
            assert isinstance(consumer, ConsumerIO)
            database = scope.get("database")
            assert consumer.left.database is consumer.right.database is database
            assert direct is None or direct is database
            assert scope.get("left") is consumer.left
            assert scope.get("consumer") is consumer
            assert consumer is not previous
            previous = consumer
            assert consumer.read() == (
                "consumer:consumer-secret/left:left-secret/database:database-secret/"
                "right:right-secret/database:database-secret"
            )
        # Each registration is owned once, and dependencies outlive borrowers.
        for name in names:
            assert events.count(("construct", name)) == 1
            assert events.count(("setup", name)) == 1
            assert events.count(("close", name)) == 1
        for borrower, dependency in (
            ("consumer", "left"),
            ("consumer", "right"),
            ("left", "database"),
            ("right", "database"),
        ):
            assert events.index(("setup", dependency)) < events.index(
                ("construct", borrower)
            )
            assert events.index(("close", borrower)) < events.index(
                ("close", dependency)
            )
        events.clear()
        # A separate execution scope must allocate fresh instances.
        scope = ResourceScope(scope.providers, scope.configuration)


def test_overridden_bindings_are_lazy_and_independently_configured(
    events: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    credentials = Mock(side_effect=lambda reference: reference.env.lower())
    monkeypatch.setattr("lyra.runtime.resources.resolve_secret", credentials)
    scope = ResourceScope(
        {
            "reader": ReaderIO,
            "database": RecordingIO,
            "regional": RecordingIO,
            "unused": ReaderIO,
        },
        configuration("reader", "database", "regional", "unused"),
        overrides={"reader": RegionalReader},
    )
    assert events == []
    credentials.assert_not_called()
    with scope.activate():
        reader = scope.get("reader")
        assert isinstance(reader, RegionalReader)
        assert reader.read() == "reader:reader/regional:regional"
        assert scope.get("regional") is reader.database
        assert ("construct", "database") not in events
        assert credentials.call_args_list == [
            call(SecretReference(env="REGIONAL")),
            call(SecretReference(env="READER")),
        ]
        database = scope.get("database")
        assert isinstance(database, RecordingIO)
        assert database is not reader.database
        assert database.read() == "database:database"
    assert ("construct", "unused") not in events
    assert call(SecretReference(env="UNUSED")) not in credentials.call_args_list


class ComposedProvider(FakeProvider):
    database: Annotated[RecordingIO, Resource("database")]

    def setup(self) -> None:
        assert self.database.read()
        super().setup()

    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
        assert self.database.read()
        return super().resolve(ids)

    def close(self) -> None:
        assert self.database.read()
        super().close()


def test_provider_uses_shared_dependency(
    events: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE", "database-secret")
    monkeypatch.setenv("PROVIDER", "fake-token")
    monkeypatch.setattr(ComposedProvider, "events", [])
    scope = ResourceScope(
        {"provider": ComposedProvider, "database": RecordingIO},
        configuration("provider", "database"),
    )
    with scope.activate():
        provider = scope.get("provider")
        assert isinstance(provider, ComposedProvider)
        assert provider.database is scope.get("database")
        result = provider.resolve(["001"])
        assert result.label.tolist() == ["provider"]
    assert ComposedProvider.events == ["setup", "close"]
    assert events == [
        ("construct", "database"),
        ("setup", "database"),
        ("close", "database"),
    ]
