from typing import Any, assert_type

import geopandas as gpd
import pytest
from pydantic import ValidationError
from typing_extensions import override

from lyra.contracts.errors import ResourceConfigurationError
from lyra.contracts.resources import IOConfig, ResourceConfig
from lyra.runtime.resources import ResourceScope
from lyra.sdk.locations import LocationProvider
from lyra.sdk.resources import BaseIO
from tests.provider_helpers import FakeProvider


class Settings(IOConfig):
    path: str
    count: int


class ExtendedSettings(Settings):
    enabled: bool


class PlainIO(BaseIO):
    def check_types(self) -> None:
        assert_type(self.config, IOConfig)


class ChildIO(PlainIO):
    def check_types(self) -> None:
        assert_type(self.config, IOConfig)


class CustomIO(BaseIO[Settings]):
    config_model = Settings

    def check_types(self) -> None:
        assert_type(self.config, Settings)
        assert_type(self.config.path, str)
        assert_type(self.config.count, int)


class CustomChildIO(CustomIO):
    def check_types(self) -> None:
        assert_type(self.config, Settings)
        assert_type(self.config.path, str)
        assert_type(self.config.count, int)


class OverrideIO(CustomChildIO):
    config_model = ExtendedSettings


class TypingOnlyIO(BaseIO[Settings]):
    pass


class PlainProvider(LocationProvider):
    id = "settings_provider"
    schema = FakeProvider.schema

    def check_types(self) -> None:
        assert_type(self.config, IOConfig)

    @override
    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
        raise AssertionError


class ChildProvider(PlainProvider):
    def check_types(self) -> None:
        assert_type(self.config, IOConfig)


class CustomProvider(LocationProvider[Settings]):
    config_model = Settings
    id = "settings_provider"
    schema = FakeProvider.schema

    def check_types(self) -> None:
        assert_type(self.config, Settings)
        assert_type(self.config.path, str)
        assert_type(self.config.count, int)

    @override
    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
        raise AssertionError


class CustomChildProvider(CustomProvider):
    def check_types(self) -> None:
        assert_type(self.config, Settings)
        assert_type(self.config.path, str)
        assert_type(self.config.count, int)


class OverrideProvider(CustomChildProvider):
    config_model = ExtendedSettings


class TypingOnlyProvider(LocationProvider[Settings]):
    id = "settings_provider"
    schema = FakeProvider.schema

    @override
    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
        raise AssertionError


@pytest.mark.parametrize(
    "resource",
    [
        BaseIO,
        PlainIO,
        ChildIO,
        PlainProvider,
        ChildProvider,
        TypingOnlyIO,
        TypingOnlyProvider,
    ],
)
def test_default_runtime_model_is_independent_of_generic_argument(
    resource: type[BaseIO[Any]],
) -> None:
    scope = ResourceScope({"resource": resource})
    with scope.activate():
        instance = scope.get("resource")
        assert type(instance.config) is IOConfig
        assert instance.config.model_dump() == {}
    with pytest.raises(ValidationError, match="extra_forbidden"):
        resource.config_model.model_validate({"path": "file"})
    scope = ResourceScope(
        {"resource": resource}, {"resource": ResourceConfig(config={"path": "file"})}
    )
    with scope.activate(), pytest.raises(ResourceConfigurationError):
        scope.get("resource")


@pytest.mark.parametrize(
    "resource",
    [
        CustomIO,
        CustomChildIO,
        CustomProvider,
        CustomChildProvider,
        OverrideIO,
        OverrideProvider,
    ],
)
def test_custom_runtime_model_validation_and_inheritance(
    resource: type[BaseIO[Any]],
) -> None:
    values: dict[str, object] = {"path": "file", "count": 3}
    expected = Settings
    if resource in {OverrideIO, OverrideProvider}:
        values["enabled"] = True
        expected = ExtendedSettings
    scope = ResourceScope(
        {"resource": resource}, {"resource": ResourceConfig(config=values)}
    )
    with scope.activate():
        instance = scope.get("resource")
        assert type(instance.config) is expected
        assert instance.config.model_dump() == values
    for invalid in ({}, values | {"count": "invalid"}, values | {"extra": 1}):
        with pytest.raises(ValidationError):
            resource.config_model.model_validate(invalid)
        scope = ResourceScope(
            {"resource": resource}, {"resource": ResourceConfig(config=invalid)}
        )
        with scope.activate(), pytest.raises(ResourceConfigurationError):
            scope.get("resource")
