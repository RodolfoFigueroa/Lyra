"""Remote lookups share immutable native values with local retained access."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import httpx
import pandas as pd
import pytest

from lyra.contracts.inspection import TerminalResultOutput
from lyra.contracts.jobs import JobStatusInfo, TableJobResult
from lyra.contracts.origins import Origin
from lyra.contracts.provider_scope import ProviderScope
from lyra.runtime.result_descriptions import ComputationSelection, ResultDescriptions
from lyra.runtime.results import ResultStore
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.server.job_store import InvalidJobResultError, JobObservation
from lyra.server.results import ResultService
from test_support.publication import publish_result
from tests.result_description_helpers import publish_values
from tests.retained_table_helpers import mixed_result
from tests.sqlite_helpers import Database, database
from tests.table_client_helpers import ClientHarness, arrow_response, client

__all__ = ["client", "database"]


@pytest.mark.parametrize("keys", [(), ("one", "two")])
def test_captured_owners_and_expired_jobs(
    database: Database, tmp_path: Path, keys: tuple[str, ...]
) -> None:
    path = tmp_path / "results.sqlite"
    writer = SQLiteStore(path)
    description = publish_values(writer, keys, spatial=True)
    reference = publish_result(ResultDescriptions(writer), description)
    path.touch()
    jobs = Mock(
        read_job=Mock(
            return_value=observation(
                reference,
                ResultDescriptions(writer).read_complete(reference)[1].execution_id,
            )
        )
    )
    service = ResultService(jobs, path)
    before = deepcopy(database.rows)
    retained = service.read_observation("job")
    assert retained is not None
    assert retained.retained_result is not None
    metadata = retained.retained_result.metadata
    assert metadata.outputs["a"].owner_scope == description.outputs["a"].owner_scope
    assert metadata.outputs["b"].scope == "location"
    assert retained.retained_result.tables["b"].crs.to_epsg() == 4326
    result = service.read_result(retained)
    assert isinstance(result, TableJobResult)
    assert result.execution_id == metadata.execution_id
    assert result.result_ref == metadata.result_ref == reference
    inspection = service.inspect(retained)
    assert isinstance(inspection, TerminalResultOutput)
    assert inspection.execution_id == metadata.execution_id
    assert inspection.result_ref == metadata.result_ref
    assert set(result.output_metadata) == {"a", "b"}
    jobs.read_job.return_value = None
    assert service.read_observation("job") is None
    with ResultStore.open(path) as reader:
        assert reader.read_result(reference).metadata == metadata
        for name, frame in retained.retained_result.tables.items():
            pd.testing.assert_frame_equal(reader.read_table(reference, name), frame)
    assert database.rows == before


@pytest.mark.parametrize("damage", ["malformed", "missing", "backing", "file"])
def test_broken_retained_reference_is_integrity_failure(
    database: Database, tmp_path: Path, damage: str
) -> None:
    path = tmp_path / "results.sqlite"
    writer = SQLiteStore(path)
    reference = publish_result(
        ResultDescriptions(writer), publish_values(writer, ("one",))
    )
    path.touch()
    if damage == "malformed":
        reference = "invalid"
    elif damage == "missing":
        reference = "f" * 32
    elif damage == "backing":
        database.rows[writer.layout.results.name].clear()
    else:
        path.unlink()
    before = deepcopy(database.rows)
    service = ResultService(
        Mock(read_job=Mock(return_value=observation(reference))),
        path,
    )
    with pytest.raises(InvalidJobResultError):
        service.read_observation("job")
    assert database.rows == before
    assert path.exists() is (damage != "file")


def observation(reference: str, execution_id: str = "b" * 32) -> JobObservation:
    now = datetime.now(UTC)
    return JobObservation(
        snapshot=JobStatusInfo(
            job_id="job",
            status="succeeded",
            created_at=now,
            updated_at=now,
            result_ref="a" * 32 if reference == "invalid" else reference,
            execution_id=execution_id,
        ),
        result_ref=reference,
        execution_id=execution_id,
    )


@pytest.mark.parametrize("identity", [None, "c" * 32])
def test_job_execution_must_match_retained_pair(
    database: Database, tmp_path: Path, identity: str | None
) -> None:
    path = tmp_path / "results.sqlite"
    writer = SQLiteStore(path)
    reference = publish_result(ResultDescriptions(writer), publish_values(writer, ()))
    path.touch()
    saved = observation(reference)

    service = ResultService(
        Mock(read_job=Mock(return_value=replace(saved, execution_id=identity))), path
    )
    before = deepcopy(database.rows)
    with pytest.raises(InvalidJobResultError, match="execution"):
        service.read_observation("job")
    assert database.rows == before


@pytest.mark.usefixtures("database")
@pytest.mark.parametrize("empty", [False, True])
def test_public_tables_keep_retained_provider_scopes(
    client: ClientHarness, tmp_path: Path, *, empty: bool
) -> None:
    path = tmp_path / "results.sqlite"
    descriptions, mixed_reference = mixed_result(SQLiteStore(path))
    saved, _, _ = descriptions.read_complete(mixed_reference)
    base = saved.outputs["a"]
    assert base.owner_scope is not None
    references = []
    owners = [
        ("cached", base.owner_scope),
        ("other-provider", ProviderScope(provider_id="districts", code_revision="1")),
        ("other-code", ProviderScope(provider_id="places", code_revision="2")),
        (
            "other-origin",
            base.owner_scope.model_copy(
                update={"origins": frozenset({Origin(id="survey", revision="2")})}
            ),
        ),
    ]
    for key, owner in owners:
        output = base.model_copy(
            update={
                "owner_scope": owner,
                "computations": (
                    ComputationSelection(
                        value_key=key, groups=("empty",) if empty else ("same",)
                    ),
                ),
            }
        )
        references.append(
            publish_result(
                descriptions, saved.model_copy(update={"outputs": {"a": output}})
            )
        )
    path.touch()
    service = ResultService(Mock(spec=[]), path)
    frames = []
    for reference, (_, owner) in zip(references, owners, strict=True):
        description = service.read_description(reference)
        body = b"".join(service.stream_table(reference, "a"))
        client.responses.extend(
            [
                httpx.Response(
                    200,
                    json={"kind": "complete", "description": description.model_dump()},
                ),
                arrow_response(body),
                arrow_response(body),
            ]
        )
        captured = client.resolve(client.client.results.describe(reference))
        frame = client.resolve(client.client.results.table(reference, "a"))
        assert captured.outputs["a"].owner_scope == owner
        assert list(frame) == ["location_id", "row_key", "value"]
        assert frame.location_id.tolist() == ([] if empty else ["same", "same"])
        if not empty:
            frame.loc[0, "location_id"] = "changed"
            frame.loc[0, "row_key"] = "changed"
        frame.attrs["owner_scope"] = {"provider_id": "forged"}
        frames.append(client.resolve(client.client.results.table(reference, "a")))
        assert captured == service.read_description(reference)
    client.close()
    for frame in frames:
        assert frame.location_id.tolist() == ([] if empty else ["same", "same"])
        assert frame.row_key.tolist() == ([] if empty else ["a", "z"])
        assert "owner_scope" not in frame.attrs
