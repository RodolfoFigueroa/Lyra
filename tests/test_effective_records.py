"""Immutable provider records and mixed selections in the isolated typed store."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from lyra.contracts.interventions import AcceptedIntervention
from lyra.contracts.origins import Origin
from lyra.contracts.provider_scope import ProviderScope, RecordReferences
from lyra.data.intervention_records import accepted_additions
from lyra.data.interventions import Intervention
from lyra.runtime.planning.provider_registry import RegisteredProvider
from lyra.runtime.provider_records import ProviderRecords
from lyra.runtime.sqlite_store import DamagedDataError, SQLiteStore
from lyra.sdk.interventions import accept_interventions
from tests.acquisition_helpers import acquisition_fixture
from tests.provider_schema_helpers import frame
from tests.sqlite_helpers import Database, database

__all__ = ["database"]


def proposal(
    provider: RegisteredProvider,
    count: int,
    *,
    name: str = "proposal",
    revision: str = "1",
) -> AcceptedIntervention:
    additions = frame()
    additions["location_id"] = ["001", "002"]
    additions.loc[0, "count"] = count
    value = Intervention(
        catalog=provider.scope.provider_id,
        id=name,
        revision=revision,
        add=additions,
        remove=["a", "b"],
    )
    return accept_interventions([value], {value.catalog: provider.schema})[0]


def test_effective_identity_is_independent_of_submission_order() -> None:
    prepared, _ = acquisition_fixture(Mock())
    provider = prepared.provider
    base = provider.scope
    additions = frame()
    additions["location_id"] = ["001", "002"]
    first = Intervention(
        catalog=base.provider_id,
        id="plan",
        revision="1",
        add=additions,
        remove=["a", "b"],
    )
    other = replace(first, catalog="parks")
    schemas = {first.catalog: provider.schema, other.catalog: provider.schema}

    def identities(changes: list[Intervention]) -> dict:
        return {
            change.catalog: change.effective_scope(
                base.model_copy(update={"provider_id": change.catalog})
            )
            for change in accept_interventions(changes, schemas)
        }

    original = identities([first, other])
    reversed_rows = additions.iloc[::-1].set_axis([87, 43])
    reordered = identities(
        [other, replace(first, add=reversed_rows, remove=["b", "a"])]
    )
    assert original == reordered
    assert (
        original[first.catalog].identity_payload
        == reordered[first.catalog].identity_payload
    )
    assert original[first.catalog] != base
    assert original[first.catalog].base == base
    with pytest.raises(ValidationError, match="catalog"):
        proposal(provider, 0).effective_scope(ProviderScope(provider_id="parks"))


@pytest.mark.parametrize("change", ["catalog", "set", "revision", "code", "origin"])
@pytest.mark.usefixtures("database")
def test_added_values_survive_reopen_under_independent_owners(
    tmp_path: Path, change: str
) -> None:
    handler = Mock()
    prepared, _ = acquisition_fixture(handler)
    provider = prepared.provider
    other = replace(
        provider,
        scope=provider.scope.model_copy(
            update={
                "catalog": {"provider_id": "parks"},
                "code": {"code_revision": "v2"},
                "origin": {"origins": frozenset({Origin(id="census", revision="new")})},
            }.get(change, {})
        ),
    )
    first = proposal(provider, -(2**63))
    second = proposal(
        other,
        2**63 - 1,
        name="alternative" if change == "set" else "proposal",
        revision="2" if change == "revision" else "1",
    )
    path = tmp_path / "store"
    with SQLiteStore(path) as store:
        store.initialize()
        records = ProviderRecords(store)
        for registered, accepted in ((provider, first), (other, second)):
            rows = accepted_additions(accepted)
            assert rows is not None
            references = RecordReferences(
                accepted.effective_scope(registered.scope), accepted.added_ids
            )
            assert records.lookup(registered, ["001"], references=references).empty
            records.publish(registered, rows, references=references)

    with SQLiteStore(path) as store:
        records = ProviderRecords(store)
        for registered, accepted, expected in (
            (provider, first, -(2**63)),
            (other, second, 2**63 - 1),
        ):
            references = RecordReferences(
                accepted.effective_scope(registered.scope), accepted.added_ids
            )
            restored = records.lookup(
                registered, ["001", "002"], references=references, required=True
            ).set_index("location_id")
            assert restored.loc["001", "count"] == expected
            assert restored["count"].isna().loc["002"]
            assert str(restored["count"].dtype) == "Int64"
            assert restored.crs == "EPSG:4326"
            with store.engine.connect() as connection:
                for key in references.by_key(["001", "002"]):
                    assert (
                        store.header(connection, key, required=True).owner_scope
                        == references.scope
                    )
            assert restored.geometry.to_wkt().loc["002"] == "POINT (1 1)"
            assert records.lookup(registered, ["001"]).empty
    handler.assert_not_called()


def test_damaged_added_record_is_never_read_under_another_scope(
    tmp_path: Path,
    database: Database,
) -> None:
    prepared, _ = acquisition_fixture(Mock())
    provider = prepared.provider
    change = proposal(provider, 4)
    references = RecordReferences(
        change.effective_scope(provider.scope), change.added_ids
    )
    rows = accepted_additions(change)
    assert rows is not None
    path = tmp_path / "store"
    with SQLiteStore(path) as store:
        ProviderRecords(store).publish(provider, rows, references=references)
    header = database.rows["stored_results"][0]
    header["owner_scope"] = provider.scope.model_dump_json()
    with SQLiteStore(path) as reopened:
        with pytest.raises(DamagedDataError, match="header scope"):
            ProviderRecords(reopened).lookup(
                provider, ["001", "002"], references=references
            )
        with pytest.raises(DamagedDataError, match="header scope"):
            ProviderRecords(reopened).publish(provider, rows, references=references)
