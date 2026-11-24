"""Complete metric hits avoid acquisition while preserving computation scope."""

from pathlib import Path
from typing import Annotated
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pytest
from typing_extensions import override

from lyra.contracts.resources import IOConfig
from lyra.runtime.execution import GraphExecutionError
from lyra.runtime.local import LocalRunner
from lyra.runtime.locations import LocationResolver
from lyra.runtime.metric_tables import MetricTables
from lyra.runtime.results import RunResult
from lyra.runtime.sqlite_store import DamagedDataError, Header
from lyra.sdk import Dependency, LocationDependency, PluginDefinition, metric
from lyra.sdk.resources import BaseIO, Resource
from lyra.sdk.storage_schema import empty_frame
from tests.frame_assertions import assert_logical_equal
from tests.metric_execution_helpers import schema
from tests.provider_execution_helpers import RESULT, provider_execution_definitions
from tests.sqlite_helpers import database

__all__ = ["database"]
pytestmark = pytest.mark.usefixtures("database")


@pytest.mark.parametrize("location_scoped", [False, True])
@pytest.mark.parametrize("empty", [False, True])
def test_complete_hit_precedes_acquisition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    location_scoped: bool,
    empty: bool,
) -> None:
    events = []
    declaration = schema() if location_scoped else RESULT

    @metric(name="source", description="Source", outputs={"value": RESULT})
    def source() -> pd.DataFrame:
        events.append("source")
        return pd.DataFrame({"total": [1]})

    @metric(
        name="complete",
        description="Complete",
        cache="full",
        inputs={
            "places": LocationDependency(provider="target", primary=location_scoped),
            "value": Dependency("value", producer="source"),
        },
        outputs={"a": declaration, "b": declaration},
    )
    def complete(places: gpd.GeoDataFrame, value: pd.DataFrame) -> dict:
        events.append(tuple(sorted(places.location_id)))
        result = (
            empty_frame(declaration)
            if empty
            else (
                pd.DataFrame(
                    {
                        "location_id": places.location_id,
                        "row_key": "one",
                        "value": pd.array([1] * len(places), dtype="Int64"),
                    }
                )
                if location_scoped
                else value
            )
        )
        return {"a": result, "b": result}

    definitions = provider_execution_definitions(events)
    definitions["complete"] = PluginDefinition(metrics=[complete, source])
    runner = LocalRunner.from_definitions(
        definitions, result_store_path=tmp_path / "values"
    )

    def run(ids: list[str]) -> RunResult:
        return runner.run(
            "complete", inputs={"places": {"provider": "target", "ids": ids}}
        )

    first = run(["a", "b"])
    events.clear()
    with monkeypatch.context() as patch:
        patch.setattr(
            LocationResolver,
            "resolve",
            Mock(side_effect=RuntimeError("acquisition")),
        )
        again = run(["b", "a"])
        assert events == []
        for name in ("a", "b"):
            assert_logical_equal(first.tables[name], again.tables[name])
        assert first.metadata.result_ref != again.metadata.result_ref
        with pytest.raises(GraphExecutionError, match="acquisition"):
            run(["b", "c"])
    run(["b", "c"])
    assert ("b", "c") in events


@pytest.mark.parametrize("failure", ["handler", "resource"])
@pytest.mark.parametrize("intermediate", [False, True])
def test_downstream_hit_bypasses_uncached_dependency(
    tmp_path: Path, failure: str, *, intermediate: bool
) -> None:
    broken = False
    calls = []

    class Support(BaseIO[IOConfig]):
        @override
        def setup(self) -> None:
            if broken and failure == "resource":
                message = "resource failure"
                raise RuntimeError(message)

    @metric(
        name="source",
        description="Source",
        outputs={"value": RESULT},
        cache="full" if intermediate else "off",
        inputs={"places": LocationDependency(provider="target")},
    )
    def source(
        places: gpd.GeoDataFrame,
        support: Annotated[BaseIO, Resource("support")],
    ) -> pd.DataFrame:
        assert isinstance(support, Support)
        if broken and failure == "handler":
            message = "handler failure"
            raise RuntimeError(message)
        assert places.empty
        calls.append("source")
        return pd.DataFrame({"total": [1]})

    @metric(
        name="complete",
        description="Complete",
        cache="off" if intermediate else "full",
        outputs={"value": RESULT},
        inputs={
            "places": LocationDependency(provider="target"),
            "value": Dependency("value", producer="source"),
        },
    )
    def complete(places: gpd.GeoDataFrame, value: pd.DataFrame) -> pd.DataFrame:
        calls.append(tuple(places.location_id))
        return value

    definitions = provider_execution_definitions([])
    definitions["complete"] = PluginDefinition(
        metrics=[complete, source], resources={"support": Support}
    )
    runner = LocalRunner.from_definitions(
        definitions, result_store_path=tmp_path / "values"
    )
    empty = {"places": {"provider": "target", "ids": []}}
    assert runner.run("complete", inputs=empty).tables["value"].total.tolist() == [1]
    assert calls == ["source", ()]
    broken = True
    runner.run("complete", inputs=empty)
    assert calls == (["source", (), ()] if intermediate else ["source", ()])
    with pytest.raises(GraphExecutionError, match=failure):
        runner.run("complete", inputs={"places": {"provider": "target", "ids": ["a"]}})


@pytest.mark.parametrize("root", ["source", "consumer"])
def test_publication_race_uses_fresh_output_in_current_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, root: str
) -> None:
    values = iter([1, 2])

    @metric(
        name="source",
        description="Concurrent producer",
        outputs={"value": RESULT},
        cache="full",
    )
    def source() -> pd.DataFrame:
        return pd.DataFrame({"total": [next(values)]})

    @metric(
        name="consumer",
        description="Observe producer value",
        outputs={"value": RESULT},
        inputs={"value": Dependency("value", producer="source")},
    )
    def consumer(value: pd.DataFrame) -> pd.DataFrame:
        return value

    runner = LocalRunner.from_definitions(
        {"race": PluginDefinition(metrics=[source, consumer])},
        result_store_path=tmp_path / "race.sqlite",
    )
    lookup = MetricTables.lookup

    def interleave(
        tables: MetricTables, header: Header, *, expected_partitions: tuple[str, ...]
    ) -> dict | None:
        result = lookup(tables, header, expected_partitions=expected_partitions)
        if header.node_id == "source" and result is None:
            # Another run commits after this run decided to compute this key.
            monkeypatch.setattr(MetricTables, "lookup", lookup)
            assert runner.run("source").tables["value"].total.tolist() == [1]
        return result

    monkeypatch.setattr(MetricTables, "lookup", interleave)
    assert runner.run(root).tables["value"].total.tolist() == [2]
    assert runner.run("source").tables["value"].total.tolist() == [1]


def test_hit_checks_output_names_against_current_declaration(tmp_path: Path) -> None:
    calls = []

    def run(name: str) -> RunResult:
        @metric(name="count", description="Count", cache="full", outputs={name: RESULT})
        def count() -> pd.DataFrame:
            calls.append(name)
            return pd.DataFrame({"total": [1]})

        runner = LocalRunner.from_definitions(
            {"test": PluginDefinition(metrics=[count])},
            result_store_path=tmp_path / "names.sqlite",
        )
        return runner.run("count")

    assert set(run("first").tables) == {"first"}
    with pytest.raises(GraphExecutionError) as error:
        run("second")
    assert isinstance(error.value.__cause__, DamagedDataError)
    assert calls == ["first"]
