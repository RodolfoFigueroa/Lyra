"""Complete base-membership validation through the managed execution boundary."""

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, call

import geopandas as gpd
import pandas as pd
import pyarrow as pa
import pytest
from typing_extensions import override

from lyra.contracts.errors import (
    ExecutionPersistenceError,
    LocationAcquisitionError,
    ReportNotFoundError,
)
from lyra.data.interventions import Intervention
from lyra.runtime.execution import GraphExecutionError
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.preparation import PreparationError
from lyra.runtime.results import ResultStore
from lyra.sdk import (
    LocationDependency,
    LocationProvider,
    Origin,
    PluginDefinition,
    metric,
)
from tests.metric_execution_helpers import schema
from tests.provider_schema_helpers import declaration, frame
from tests.sqlite_helpers import Database, database, fail_insert

__all__ = ["database"]


class Places(LocationProvider):
    id = "places"
    schema = declaration()
    cache = True

    @override
    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
        base = frame()
        return base.loc[base.location_id.isin(ids)]


@pytest.fixture
def events() -> Mock:
    trace = Mock()
    trace.membership.side_effect = lambda _catalog, ids: {"a", "b"}.intersection(ids)
    return trace


@pytest.fixture
def catalogs(events: Mock) -> tuple[type[Places], type[Places]]:
    class Survey(Places):
        @override
        def existing_ids(self, ids: list[str]) -> set[str]:
            return events.membership(self.id, ids)

        @override
        def setup(self) -> None:
            events.setup(self.id)

        @override
        def close(self) -> None:
            events.close(self.id)

    class Parks(Survey):
        id = "parks"

    return Survey, Parks


def runner(
    events: Mock, catalogs: tuple[type[Places], type[Places]], path: Path
) -> LocalRunner:
    @metric(
        name="measure",
        description="Measure places",
        cache="per_location",
        inputs={"places": LocationDependency(provider="places", primary=True)},
        outputs={"rows": schema()},
    )
    def measure(places: gpd.GeoDataFrame) -> pd.DataFrame:
        events.metric()
        return pd.DataFrame(
            {"location_id": places.location_id, "row_key": "one", "value": 1}
        )

    survey, parks = catalogs
    return LocalRunner.from_definitions(
        {
            "survey": PluginDefinition(
                metrics=[measure], resources={"survey": survey, "parks": parks}
            )
        },
        result_store_path=path,
    )


@pytest.fixture
def local(
    events: Mock, catalogs: tuple[type[Places], type[Places]], tmp_path: Path
) -> LocalRunner:
    return runner(events, catalogs, tmp_path / "store")


def change(
    catalog: str = "places", *, added: str | None = None, removed: str | None = None
) -> Intervention:
    rows = None
    if added is not None:
        rows = frame().iloc[:1].copy()
        rows["location_id"] = added
    return Intervention(
        id="proposal",
        revision="1",
        catalog=catalog,
        add=rows,
        remove=[removed] if removed else None,
    )


def selected(*ids: str) -> dict:
    return {"places": {"provider": "places", "ids": list(ids)}}


@pytest.mark.parametrize(
    ("added", "removed", "error"),
    [
        ("b", None, "already exist"),
        (None, "absent", "missing"),
        ("b", "b", "already exist"),
        ("absent", "absent", "missing"),
    ],
)
def test_all_actions_check_original_base_outside_selection(
    added: str | None,
    removed: str | None,
    error: str,
    events: Mock,
    local: LocalRunner,
) -> None:
    changes = [change(added=added, removed=removed)]
    # A failed set leaves no proof on retry, including when the actions overlap.
    for _ in range(2):
        with pytest.raises(GraphExecutionError, match=error):
            local.run("measure", inputs=selected("a"), interventions=changes)
    ids = sorted({item for item in (added, removed) if item is not None})
    assert events.membership.call_args_list == [call("places", ids)] * 2
    events.metric.assert_not_called()


@pytest.mark.parametrize("ids", [(), ("a",), ("new",)])
def test_invalid_unused_target_blocks_cached_or_empty_work(
    ids: tuple[str, ...],
    events: Mock,
    local: LocalRunner,
    database: Database,
) -> None:
    inputs = selected(*ids)
    relevant = change(added="new", removed="b")
    local.run("measure", inputs=inputs, interventions=[relevant])
    events.reset_mock()
    changes = [relevant, change("parks", removed="absent")]
    with pytest.raises(GraphExecutionError, match="missing"):
        local.run("measure", inputs=inputs, interventions=changes)
    events.membership.assert_any_call("parks", ["absent"])
    events.metric.assert_not_called()
    assert len(database.rows["final_results"]) == 1


def test_retained_records_still_require_validation_for_each_catalog(
    events: Mock, local: LocalRunner
) -> None:
    local.run("measure", inputs=selected("a", "b"))
    changes = [
        change(added="new", removed="b"),
        change("parks", added="new", removed="a"),
    ]
    events.reset_mock()
    local.run("measure", inputs=selected("a"), interventions=changes)
    assert sorted(events.membership.call_args_list) == [
        call("parks", ["a", "new"]),
        call("places", ["b", "new"]),
    ]
    events.reset_mock()
    local.run("measure", inputs=selected("a"), interventions=changes[::-1])
    events.membership.assert_not_called()
    revised = replace(changes[1], revision="2")
    mixed = local.run(
        "measure", inputs=selected("a"), interventions=[revised, changes[0]]
    )
    assert {
        v.scope.provider_id: v.membership_lookup
        for v in mixed.report.intervention_validations
    } == {"parks": "executed", "places": "skipped"}
    events.membership.assert_called_once_with("parks", ["a", "new"])


@pytest.mark.parametrize(
    "identity", ["provider", "code", "origin", "set_id", "set_revision"]
)
def test_reopened_store_reuses_only_matching_validations(
    identity: str,
    tmp_path: Path,
    events: Mock,
    catalogs: tuple[type[Places], type[Places]],
) -> None:
    path = tmp_path / "store"
    proposal = change("parks", added="new", removed="b")
    inputs = selected("a")
    first = runner(events, catalogs, path)
    first.run("measure", inputs=inputs)
    events.reset_mock()
    # The computation is already retained, but the unused catalog still needs a lookup.
    fresh = first.run("measure", inputs=inputs, interventions=[proposal])
    (validation,) = fresh.report.intervention_validations
    assert validation.membership_lookup == "executed"
    assert validation.scope.base == first.catalog.provider_registry["parks"].scope
    assert (
        validation.scope.intervention == fresh.report.request.interventions[0].identity
    )
    assert {n.kind: n.outcome for n in fresh.report.nodes} == {
        "metric": "reused",
        "provider": "skipped",
    }
    assert events.mock_calls == [
        call.setup("parks"),
        call.membership("parks", ["b", "new"]),
        call.close("parks"),
    ]

    events.reset_mock()
    reused = runner(events, catalogs, path).run(
        "measure", inputs=inputs, interventions=[proposal]
    )
    (skipped,) = reused.report.intervention_validations
    assert skipped.scope == validation.scope
    assert skipped.membership_lookup == "skipped"
    assert not events.mock_calls

    parks = catalogs[1]
    if identity == "provider":
        parks.id = "woods"
        proposal = replace(proposal, catalog="woods")
    elif identity == "code":
        parks.code_revision = "2"
    elif identity == "origin":
        parks.origins = (Origin(id="survey", revision="2"),)
    elif identity == "set_id":
        proposal = replace(proposal, id="another")
    else:
        proposal = replace(proposal, revision="2")
    updated = runner(events, catalogs, path).run(
        "measure", inputs=inputs, interventions=[proposal]
    )
    (changed,) = updated.report.intervention_validations
    assert changed.membership_lookup == "executed"
    assert changed.scope != validation.scope
    events.membership.assert_called_once_with(parks.id, ["b", "new"])


def test_preparation_still_checks_submissions_after_validation(
    tmp_path: Path, events: Mock, catalogs: tuple[type[Places], type[Places]]
) -> None:
    path = tmp_path / "store"
    local = runner(events, catalogs, path)
    proposal = change("parks", added="new")
    inputs = selected()
    local.run("measure", inputs=inputs, interventions=[proposal])
    events.reset_mock()
    with pytest.raises(PreparationError):
        local.run("measure", inputs=inputs, interventions=[proposal, proposal])
    assert proposal.add is not None
    bad = replace(proposal, add=proposal.add.drop(columns="count"))
    with pytest.raises(PreparationError):
        local.run("measure", inputs=inputs, interventions=[bad])

    class Unsupported(Places):
        id = "parks"

    with pytest.raises(PreparationError, match="existing_ids"):
        runner(events, (catalogs[0], Unsupported), path).run(
            "measure", inputs=inputs, interventions=[proposal]
        )

    class Renamed(Places):
        id = "woods"

    with pytest.raises(PreparationError, match="Unknown intervention catalog"):
        runner(events, (catalogs[0], Renamed), path).run(
            "measure", inputs=inputs, interventions=[proposal]
        )
    assert not events.mock_calls


@pytest.mark.parametrize("stage", ["lookup", "publication"])
def test_failure_aborts_and_retry_requires_lookup(
    stage: str,
    events: Mock,
    local: LocalRunner,
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cause = OSError("injected failure")
    changes = [change(added="new")]
    with monkeypatch.context() as failures:
        if stage == "lookup":
            failures.setattr(events.membership, "side_effect", cause)
            with pytest.raises(GraphExecutionError) as failed:
                local.run("measure", inputs=selected("a"), interventions=changes)
            assert isinstance(failed.value.__cause__, LocationAcquisitionError)
            assert failed.value.__cause__.__cause__ is cause
        else:
            fail_insert(database, failures, "intervention_validations", cause)
            with pytest.raises(ExecutionPersistenceError) as failed_write:
                local.run("measure", inputs=selected("a"), interventions=changes)
            assert failed_write.value.__cause__ is cause
    assert not database.rows.get("final_results")
    events.metric.assert_not_called()
    events.close.assert_called_once_with("places")
    events.reset_mock()
    local.run("measure", inputs=selected("a"), interventions=changes)
    assert events.mock_calls == [
        call.setup("places"),
        call.membership("places", ["new"]),
        call.metric(),
        call.close("places"),
    ]


@pytest.mark.usefixtures("database")
def test_published_exact_work_and_ownership(local: LocalRunner, events: Mock) -> None:
    changes = [change(added="new", removed="b"), change("parks", removed="a")]
    for ids, lookup, metric_work, records in [
        ((), "executed", "skipped", "skipped"),
        ((), "skipped", "skipped", "skipped"),
        (("new",), "skipped", "executed", "skipped"),
        (("new", "a"), "skipped", "mixed", "executed"),
        (("a", "new"), "skipped", "reused", "skipped"),
    ]:
        events.reset_mock()
        result = local.run("measure", inputs=selected(*ids), interventions=changes)
        report = result.report
        validations = {v.scope.provider_id: v for v in report.intervention_validations}
        assert (
            validations["places"].membership_lookup
            == validations["parks"].membership_lookup
            == lookup
        )
        assert events.membership.called == (lookup == "executed")
        assert events.metric.called == (metric_work in {"executed", "mixed"})
        if not ids:
            attempt = local.inspect()
            assert attempt is not None
            assert attempt.execution.native_inputs == ()
        assert {n.kind: n.outcome for n in report.nodes} == {
            "metric": metric_work,
            "provider": records,
        }
        root = next(n for n in report.nodes if n.use_id == report.root_use_id)
        scope = validations["places"].scope
        assert (
            root.inputs["places"].provider_scope
            == root.outputs["rows"].owner_scope
            == scope
        )
        assert result.metadata.outputs["rows"].owner_scope == scope
        assert result.metadata.location_inputs["places"].provider_scope == scope
        assert set(result.tables["rows"].location_id) == set(ids)

        result.metadata.store_path.touch()
        with ResultStore.open(result.metadata.store_path) as reader:
            restored = reader.read_result(report.result_ref)
            for stream, role in (
                (reader.stream_table(report.result_ref, "rows"), b"owner_scope"),
                (
                    reader.stream_location_input(report.result_ref, "places"),
                    b"provider_scope",
                ),
            ):
                with pa.ipc.open_stream(b"".join(stream)) as arrow:
                    assert json.loads(arrow.schema.metadata[role]) == scope.model_dump(
                        mode="json"
                    )
                    assert sorted(
                        arrow.read_all()["location_id"].to_pylist()
                    ) == sorted(ids)
            assert restored.report == reader.read_report(report.execution_id) == report
            assert restored.metadata == result.metadata
            by_catalog = {
                change.catalog: change
                for change in restored.report.request.interventions
            }
            accepted, unused = by_catalog["places"], by_catalog["parks"]
            assert accepted.add == [
                {"location_id": "new", "geometry": "POINT (0 0)", "count": "1"}
            ]
            assert accepted.remove == ["b"]
            assert unused.add == []
            assert unused.remove == ["a"]
            for value in (accepted, unused):
                geometry = next(
                    c for c in value.addition_schema.columns if c.name == "geometry"
                )
                assert geometry.parameters["crs"] == "EPSG:4326"


@pytest.mark.parametrize(
    "table", ["stored_results", "input_selections", "final_results"]
)
def test_failed_result_publication_keeps_only_completed_work(
    local: LocalRunner,
    events: Mock,
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
    table: str,
) -> None:
    changes = [change(added="new")]
    cause = OSError(f"{table} publication failed")
    with monkeypatch.context() as failures:
        fail_insert(database, failures, table, cause)
        with pytest.raises(ExecutionPersistenceError) as caught:
            local.run("measure", inputs=selected("new"), interventions=changes)
    assert caught.value.__cause__ is cause
    assert not database.rows.get("final_results")
    events.reset_mock()
    result = local.run("measure", inputs=selected("new"), interventions=changes)
    assert events.mock_calls == ([] if table == "final_results" else [call.metric()])
    assert result.report.intervention_validations[0].membership_lookup == "skipped"
    assert next(n for n in result.report.nodes if n.kind == "metric").outcome == (
        "reused" if table == "final_results" else "executed"
    )
    result.metadata.store_path.touch()
    assert isinstance(caught.value.execution_id, str)
    with (
        ResultStore.open(result.metadata.store_path) as reader,
        pytest.raises(ReportNotFoundError),
    ):
        reader.read_report(caught.value.execution_id)


@pytest.mark.usefixtures("database")
def test_downloads_keep_intervention_scope_per_result(local: LocalRunner) -> None:
    references = []
    for revision in ("first", "second"):
        intervention = replace(change(removed="b"), revision=revision)
        result = local.run(
            "measure", inputs=selected("a"), interventions=[intervention]
        )
        references.append((result.report.result_ref, revision))
    path = result.metadata.store_path
    path.touch()
    with ResultStore.open(path) as reader:
        for reference, revision in [*references, references[0]]:
            for chunks, role in (
                (reader.stream_table(reference, "rows"), b"owner_scope"),
                (reader.stream_location_input(reference, "places"), b"provider_scope"),
            ):
                with pa.ipc.open_stream(b"".join(chunks)) as stream:
                    assert (
                        json.loads(stream.schema.metadata[role])["intervention"][
                            "revision"
                        ]
                        == revision
                    )
