"""Managed local runs own persistent storage and detached retained results."""

from pathlib import Path
from typing import Any, Literal
from unittest.mock import Mock

import pytest

from lyra.runtime.execution import GraphExecutionError
from lyra.runtime.local import LocalRunner
from lyra.runtime.metric_tables import MetricTables
from lyra.runtime.result_descriptions import ResultDescriptions
from lyra.runtime.sqlite_store import SQLiteStore
from tests.local_persistence_helpers import definition
from tests.sqlite_helpers import Database, database

__all__ = ["database"]


@pytest.mark.parametrize("constructor", ["installed", "definitions"])
@pytest.mark.parametrize("path_kind", ["default", "relative", "absolute"])
def test_paths_are_captured_lazily(
    constructor: str,
    path_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    database: Database,
) -> None:
    monkeypatch.chdir(tmp_path)
    plugin = definition([])
    loader = Mock(return_value=plugin)
    monkeypatch.setattr("lyra.runtime.local.load_installed_plugin_definition", loader)
    expected = tmp_path / (
        ".lyra/results.sqlite3" if path_kind == "default" else "custom/results.sqlite3"
    )
    kwargs: dict[str, Any] = (
        {}
        if path_kind == "default"
        else {
            "result_store_path": expected
            if path_kind == "absolute"
            else Path("custom/results.sqlite3")
        }
    )
    runner = (
        LocalRunner(["test"], **kwargs)
        if constructor == "installed"
        else LocalRunner.from_definitions({"test": plugin}, **kwargs)
    )
    database.engine.connect.assert_not_called()
    assert not expected.parent.exists()
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    result = runner.run("root")
    assert result.metadata.store_path == expected
    assert result.metadata.result_ref
    assert expected.parent.is_dir()
    assert not (other / ".lyra").exists()
    database.engine.dispose.assert_called_once()


@pytest.mark.parametrize("private", [False, True])
@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("cache", ["full", "off"])
def test_consecutive_runs_retain_distinct_detached_results(
    *, private: bool, empty: bool, cache: Literal["full", "off"], database: Database
) -> None:
    calls = []
    runner = LocalRunner.from_definitions(
        {"test": definition(calls, cache=cache, private=private, empty=empty)}
    )
    run = runner.run_development if private else runner.run
    first = run("root")
    items = first.metadata.parameters["items"]
    assert isinstance(items, list)
    items.append(99)
    first.tables["answer"]["value"] = 99
    second = run("root")
    assert first.metadata.result_ref != second.metadata.result_ref
    assert second.metadata.parameters == {"items": [1]}
    assert second.tables["answer"].value.tolist() == ([] if empty else [1])
    assert len(calls) == (1 if cache == "full" else 2)
    with SQLiteStore(second.metadata.store_path) as store:
        description, tables = ResultDescriptions(store).read(first.metadata.result_ref)
    assert description.parameters == {"items": [1]}
    assert tables["answer"].value.tolist() == ([] if empty else [1])
    assert len(database.rows["final_results"]) == 2


@pytest.mark.parametrize("failure", ["value", "description"])
def test_required_publication_failure_closes_store(
    failure: str, monkeypatch: pytest.MonkeyPatch, database: Database
) -> None:
    runner = LocalRunner.from_definitions({"test": definition([], cache="off")})
    writer = MetricTables if failure == "value" else ResultDescriptions
    monkeypatch.setattr(
        writer, "publish", Mock(side_effect=RuntimeError("publication failure"))
    )
    with pytest.raises(GraphExecutionError):
        runner.run("root")
    database.engine.dispose.assert_called_once()
    assert not database.rows.get("final_results")
    provenance = runner.inspect()
    assert provenance is not None
    assert not provenance.execution.succeeded


def test_disabling_cache_preserves_retained_results(
    tmp_path: Path, database: Database
) -> None:
    calls = []
    path = tmp_path / "results.sqlite3"
    enabled = LocalRunner.from_definitions(
        {"test": definition(calls)}, result_store_path=path
    )
    first = enabled.run("root")
    disabled = LocalRunner.from_definitions(
        {"test": definition(calls, cache="off")}, result_store_path=path
    )
    second = disabled.run("root")
    assert len(calls) == 2
    third = enabled.run("root")
    assert len(calls) == 2
    assert (
        len(
            {
                first.metadata.result_ref,
                second.metadata.result_ref,
                third.metadata.result_ref,
            }
        )
        == 3
    )
    with SQLiteStore(path) as store:
        _, retained = ResultDescriptions(store).read(first.metadata.result_ref)
    assert retained["answer"].value.tolist() == [1]
    assert len(database.rows["final_results"]) == 3
