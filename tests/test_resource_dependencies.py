from typing import Annotated, cast
from unittest.mock import Mock

import pandas as pd
import pandera.pandas as pa
import pytest

from lyra.contracts.errors import PluginDefinitionError, ResourceConfigurationError
from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.resources import (
    ResourceScope,
    collect_resources,
    validate_resource_graph,
)
from lyra.sdk.plugin import PluginDefinition, metric
from lyra.sdk.resources import BaseIO, Resource, resource_dependencies


class FileIO(BaseIO):
    pass


TABLE = pa.DataFrameSchema(
    {"value": pa.Column(int, description="Value", metadata={"unit": None})}
)


@metric(
    code_revision="1",
    name="source",
    description="Source",
    outputs={"source_table": TABLE},
)
def source(files: Annotated[FileIO, Resource("files")]) -> pd.DataFrame:
    pytest.fail(f"Handler invoked during inspection: {files}")


@metric(
    code_revision="1", name="root", description="Root", outputs={"root_table": TABLE}
)
def root(files: Annotated[FileIO, Resource("files")]) -> pd.DataFrame:
    pytest.fail(f"Handler invoked during inspection: {files}")


class Database(BaseIO):
    pass


class SpecificDatabase(Database):
    pass


class Reader(BaseIO):
    database: Annotated[Database, Resource("database")]
    ordinary: str
    unrelated: Annotated[int, "metadata"]


class RegionalReader(Reader):
    database: Annotated[Database, Resource("regional")]


class Consumer(BaseIO):
    left: Annotated[Reader, Resource("left")]
    right: Annotated[Reader, Resource("right")]


def test_inherited_and_overridden_declarations() -> None:
    class Inherited(Reader):
        pass

    assert resource_dependencies(Inherited) == {
        "database": (Database, Resource("database"))
    }
    assert resource_dependencies(RegionalReader) == {
        "database": (Database, Resource("regional"))
    }
    assert resource_dependencies(Reader) == {
        "database": (Database, Resource("database"))
    }


def test_service_free_cross_plugin_diamond(monkeypatch: pytest.MonkeyPatch) -> None:
    constructor = Mock(side_effect=AssertionError("constructed"))
    credentials = Mock(side_effect=AssertionError("credentials"))
    monkeypatch.setattr(BaseIO, "__init__", constructor)
    monkeypatch.setattr("lyra.runtime.resources.resolve_secret", credentials)
    definitions = {
        "consumer": PluginDefinition(
            metrics=[root], resources={"consumer": Consumer, "left": Reader}
        ),
        "database": PluginDefinition(
            metrics=[source],
            resources={"files": FileIO, "database": SpecificDatabase, "right": Reader},
        ),
    }
    catalog = compile_live_catalog(definitions)
    assert set(catalog.implementations) == set(definitions)
    providers = collect_resources({k: v.resources for k, v in definitions.items()})
    dependencies = ResourceScope(providers).dependencies
    assert dependencies["consumer"] == {
        "left": (Reader, Resource("left")),
        "right": (Reader, Resource("right")),
    }
    assert dependencies["left"] == dependencies["right"]
    constructor.assert_not_called()
    credentials.assert_not_called()


@pytest.mark.parametrize(
    "annotation",
    [
        Annotated[Database, Resource("")],
        Annotated[Database, Resource(" database")],
        Annotated[Database, Resource("database ")],
        Annotated[Database, Resource(cast("str", None))],
        Annotated[Database, Resource("a"), Resource("b")],
        Annotated[str, Resource("database")],
    ],
)
def test_invalid_annotations(annotation: object) -> None:
    resource = type("Invalid", (BaseIO,), {"__annotations__": {"database": annotation}})
    with pytest.raises(ValueError, match="Resource"):
        resource_dependencies(resource)
    with pytest.raises(PluginDefinitionError, match="Resource"):
        PluginDefinition(metrics=[source], resources={"invalid": resource})


@pytest.mark.parametrize("name", ["", " spaced", "spaced ", None, 7])
def test_invalid_registration_names(name: object) -> None:
    with pytest.raises(ResourceConfigurationError, match="Invalid resource"):
        ResourceScope({cast("str", name): Database})


@pytest.mark.parametrize("provider", [object, object(), None])
def test_invalid_registration_classes(provider: object) -> None:
    with pytest.raises(ResourceConfigurationError, match="Invalid resource"):
        ResourceScope({"database": cast("type[BaseIO]", provider)})
    with pytest.raises(ResourceConfigurationError, match="Invalid resource"):
        ResourceScope(
            {"database": Database},
            overrides={"database": cast("type[BaseIO]", provider)},
        )


@pytest.mark.parametrize(
    "providers",
    [
        {"reader": Reader},
        {"reader": Reader, "database": BaseIO},
        {"reader": Reader, "database": Reader},
        {"consumer": Consumer, "left": Reader, "right": Reader, "database": Consumer},
    ],
)
def test_invalid_graph_fails_before_acquisition(
    providers: dict[str, type[BaseIO]], monkeypatch: pytest.MonkeyPatch
) -> None:
    constructor = Mock(side_effect=AssertionError("constructed"))
    credentials = Mock(side_effect=AssertionError("credentials"))
    monkeypatch.setattr(BaseIO, "__init__", constructor)
    monkeypatch.setattr("lyra.runtime.resources.resolve_secret", credentials)
    with pytest.raises(ResourceConfigurationError, match="Missing or incompatible"):
        ResourceScope(providers)
    with pytest.raises(ResourceConfigurationError, match="Missing or incompatible"):
        compile_live_catalog(
            {"test": PluginDefinition(metrics=[root], resources=providers)}
        )
    constructor.assert_not_called()
    credentials.assert_not_called()


@pytest.mark.parametrize("length", [1, 2, 4])
def test_unused_cycles_fail_before_acquisition(
    length: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    providers = {
        f"r{i}": type(
            f"R{i}",
            (BaseIO,),
            {
                "__annotations__": {
                    "dependency": Annotated[BaseIO, Resource(f"r{(i + 1) % length}")]
                }
            },
        )
        for i in range(length)
    }
    constructor = Mock(side_effect=AssertionError("constructed"))
    credentials = Mock(side_effect=AssertionError("credentials"))
    monkeypatch.setattr(BaseIO, "__init__", constructor)
    monkeypatch.setattr("lyra.runtime.resources.resolve_secret", credentials)
    with pytest.raises(ResourceConfigurationError, match="cycle"):
        compile_live_catalog(
            {
                "test": PluginDefinition(
                    metrics=[source], resources={"files": FileIO, **providers}
                )
            }
        )
    with pytest.raises(ResourceConfigurationError, match="cycle"):
        ResourceScope(providers)
    constructor.assert_not_called()
    credentials.assert_not_called()


def test_effective_override_dependencies() -> None:
    providers = {"reader": Reader, "database": Database, "regional": SpecificDatabase}
    scope = ResourceScope(providers, overrides={"reader": RegionalReader})
    assert scope.dependencies["reader"] == {
        "database": (Database, Resource("regional"))
    }
    with pytest.raises(ResourceConfigurationError, match="regional"):
        ResourceScope(
            {"reader": Reader, "database": Database},
            overrides={"reader": RegionalReader},
        )

    class CyclicReader(Reader):
        self_reference: Annotated[BaseIO, Resource("reader")]

    with pytest.raises(ResourceConfigurationError, match="cycle"):
        ResourceScope(providers, overrides={"reader": CyclicReader})


def test_registration_conflicts_and_identical_declarations() -> None:
    assert collect_resources({"a": {"db": Database}, "b": {"db": Database}}) == {
        "db": Database
    }
    with pytest.raises(ResourceConfigurationError, match="Conflicting"):
        collect_resources({"a": {"db": Database}, "b": {"db": SpecificDatabase}})


def test_forward_reference_declaration() -> None:
    class Forward(BaseIO):
        database: "Annotated[Database, Resource('database')]"

    assert validate_resource_graph({"forward": Forward, "database": Database})[
        "forward"
    ] == {"database": (Database, Resource("database"))}


def test_incompatible_direct_binding_from_another_plugin() -> None:
    definitions = {
        "consumer": PluginDefinition(metrics=[root]),
        "registrations": PluginDefinition(
            metrics=[source], resources={"files": Database}
        ),
    }
    with pytest.raises(
        PluginDefinitionError, match="Missing or incompatible resource: files"
    ):
        compile_live_catalog(definitions)
