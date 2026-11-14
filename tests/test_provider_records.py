"""Record publication and acquisition with transaction-aware, offline boundaries."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest
from geopandas.testing import assert_geodataframe_equal
from sqlalchemy.sql.dml import Insert

from lyra.contracts.errors import LocationProviderContractError
from lyra.contracts.locations import LocationSelection
from lyra.contracts.origins import Origin
from lyra.contracts.provider_scope import ProviderScope, RecordIdentity
from lyra.runtime.locations import LocationResolver
from lyra.runtime.planning.provider_preparation import PreparedProviderInput
from lyra.runtime.planning.provider_registry import capture_provider
from lyra.runtime.provider_acquisition import acquire_provider_input
from lyra.runtime.provider_records import ProviderRecords, provider_registration
from lyra.runtime.sqlite_store import DamagedDataError, SQLiteStore
from tests.acquisition_helpers import acquisition_fixture
from tests.candidate_helpers import resources as candidate_resources
from tests.provider_schema_helpers import frame
from tests.selector_helpers import ManagedProvider
from tests.sqlite_helpers import Database, database

if TYPE_CHECKING:
    from pathlib import Path

# Reuse the same transaction fake and service-guarded engine fixture.
__all__ = ["database"]


def test_batch_idempotence_rollback_and_typed_read(
    database: Database, tmp_path: Path
) -> None:
    selection, _ = acquisition_fixture(Mock(return_value=frame()))
    provider = selection.provider
    store = SQLiteStore(tmp_path / "records.sqlite")
    records = ProviderRecords(store)
    validated = provider.schema.validate(frame())
    records.publish(provider, validated)
    original = records.lookup(provider, ["a", "b"], required=True)
    assert_geodataframe_equal(original, validated, check_dtype=False)
    changed = validated.copy()
    changed.loc[:, "count"] = 999
    records.publish(provider, changed)
    assert_geodataframe_equal(records.lookup(provider, ["a", "b"]), original)
    assert len(database.rows["stored_results"]) == 2
    original.loc[:, "count"] = 888
    assert records.lookup(provider, ["a"]).iloc[0]["count"] != 888
    newer = replace(
        provider, scope=ProviderScope(provider_id="tracts", code_revision="new")
    )
    database.fail_commit = True
    with pytest.raises(RuntimeError, match="commit"):
        records.publish(newer, validated)
    database.fail_commit = False
    assert records.lookup(newer, ["a", "b"]).empty
    assert len(database.rows["stored_results"]) == 2


@pytest.mark.parametrize(
    "scope",
    [
        ProviderScope(provider_id="other"),
        ProviderScope(provider_id="tracts", code_revision="new"),
        ProviderScope(
            provider_id="tracts",
            origins=frozenset({Origin(id="census", revision="next")}),
        ),
    ],
)
def test_scope_isolation(
    database: Database, tmp_path: Path, scope: ProviderScope
) -> None:
    selection, _ = acquisition_fixture(Mock(return_value=frame()))
    provider = selection.provider
    records = ProviderRecords(SQLiteStore(tmp_path / "records.sqlite"))
    records.publish(provider, provider.schema.validate(frame()))
    other = replace(provider, scope=scope)
    assert records.lookup(other, ["a"]).empty
    records.publish(other, other.schema.validate(frame()))
    assert len(database.rows["stored_results"]) == 4
    assert len(records.lookup(other, ["a"], required=True)) == 1


@pytest.mark.parametrize("damage", ["partition", "row", "null", "geometry", "header"])
def test_damage_is_not_reacquisition(
    database: Database, tmp_path: Path, damage: str
) -> None:
    handler = Mock(return_value=frame())
    selection, resources = acquisition_fixture(handler)
    provider = selection.provider
    store = SQLiteStore(tmp_path / "records.sqlite")
    records = ProviderRecords(store)
    records.publish(provider, provider.schema.validate(frame()))
    table = store.layout.tables[provider_registration(provider).key].table.name
    if damage == "partition":
        database.rows["stored_partitions"].clear()
    elif damage == "row":
        database.rows[table].clear()
    elif damage == "null":
        database.rows[table][0]["geometry"] = None
    elif damage == "geometry":
        database.rows[table][0]["geometry"] = b"broken"
    else:
        database.rows["stored_results"][0]["node_id"] = "wrong"
    resolver = LocationResolver(
        resources,
        providers={"tracts": replace(provider, cache=True)},
        provider_records=records,
    )
    with pytest.raises(DamagedDataError):
        resolver.resolve(
            {"places": LocationSelection(provider="tracts", ids=["a", "b"])}
        )
    handler.assert_not_called()


def test_actual_acquisition_retention_and_reuse_policy(
    database: Database, tmp_path: Path
) -> None:
    handler = Mock(return_value=frame())
    selection, resources = acquisition_fixture(handler)
    records = ProviderRecords(SQLiteStore(tmp_path / "records.sqlite"))
    selections = {"places": LocationSelection(provider="tracts", ids=["a", "b"])}
    with resources.activate():
        resolver = LocationResolver(
            resources,
            providers={"tracts": selection.provider},
            provider_records=records,
            retained_providers=frozenset({"tracts"}),
        )
        resolver.resolve(selections)
        assert handler.call_count == 1
        resolver = LocationResolver(
            resources,
            providers={"tracts": replace(selection.provider, cache=True)},
            provider_records=records,
        )
        resolver.resolve(selections)
        assert handler.call_count == 1
        resolver = LocationResolver(
            resources,
            providers={"tracts": selection.provider},
            provider_records=records,
            retained_providers=frozenset({"tracts"}),
        )
        resolver.resolve(selections)
        assert handler.call_count == 2
        resolver = LocationResolver(
            resources,
            providers={"tracts": replace(selection.provider, cache=True)},
            provider_records=records,
        )
        resolver.resolve(selections)
        assert handler.call_count == 2
    assert len(database.rows["stored_results"]) == 2


def test_empty_and_unresolved(database: Database, tmp_path: Path) -> None:
    handler = Mock(return_value=frame().iloc[:0])
    selection, resources = acquisition_fixture(handler)
    records = ProviderRecords(SQLiteStore(tmp_path / "records.sqlite"))
    with resources.activate():
        result = acquire_provider_input(
            selection, resources, needed_ids=[], records=records
        )
        assert result.frame.empty
        handler.assert_not_called()
        with pytest.raises(LocationProviderContractError, match="match requested"):
            acquire_provider_input(selection, resources, records=records)
    assert not database.rows
    with pytest.raises(DamagedDataError, match="required"):
        records.lookup(selection.provider, ["a"], required=True)


def test_predicate_and_exact_share_records(
    database: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    target = capture_provider("managed", ManagedProvider)
    events = []
    scope, resolver, exact = candidate_resources(monkeypatch, target, frame(), events)
    records = ProviderRecords(SQLiteStore(tmp_path / "records.sqlite"))
    resolver.provider_records = records
    resolver.retained_providers = frozenset({"managed"})
    assert target.query is not None
    with scope.activate():
        resolver.select("managed", target.query.target_relation())
        resolver.select("managed", target.query.target_relation())
    assert events.count("query") == 2
    exact.assert_not_called()
    assert len(database.rows["stored_results"]) == 2
    keys = {
        RecordIdentity(scope=target.scope, location_id=location).key
        for location in ["a", "b"]
    }
    assert {row["value_key"] for row in database.rows["stored_results"]} == keys
    assert len(records.lookup(target, ["a", "b"], required=True)) == 2
    exact_scope = Mock()
    exact_scope.get.return_value.resolve.return_value = frame()
    acquire_provider_input(
        PreparedProviderInput("places", frozenset({"a", "b"}), target),
        exact_scope,
        records=records,
    )
    assert len(database.rows["stored_results"]) == 2


def test_no_match_predicate_publishes_nothing(
    database: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = capture_provider("managed", ManagedProvider)
    scope, resolver, exact = candidate_resources(
        monkeypatch, target, frame().iloc[:0], []
    )
    resolver.provider_records = ProviderRecords(
        SQLiteStore(tmp_path / "records.sqlite")
    )
    resolver.retained_providers = frozenset({"managed"})
    assert target.query is not None
    with scope.activate():
        assert resolver.select("managed", target.query.target_relation()).frame.empty
    exact.assert_not_called()
    assert not database.rows


def test_failed_row_insert_rolls_back_batch(database: Database, tmp_path: Path) -> None:
    selection, _ = acquisition_fixture(Mock(return_value=frame()))
    store = SQLiteStore(tmp_path / "records.sqlite")
    store.initialize((provider_registration(selection.provider),))
    data = store.layout.tables[provider_registration(selection.provider).key]
    execute = database.execute

    def fail_rows(statement: object, parameters: object = None) -> object:
        if isinstance(statement, Insert) and statement.table.name == data.table.name:
            message = "row insertion failure"
            raise RuntimeError(message)
        return execute(statement, parameters)

    database.connection.execute.side_effect = fail_rows
    with pytest.raises(RuntimeError, match="row insertion"):
        ProviderRecords(store).publish(
            selection.provider, selection.provider.schema.validate(frame())
        )
    assert not database.rows.get("stored_results")
    assert not database.rows.get("stored_partitions")
    assert not database.rows.get(data.table.name)
