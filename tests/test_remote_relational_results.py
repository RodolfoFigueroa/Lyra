"""Remote lookups share immutable native values with local retained access."""

from copy import deepcopy
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
    jobs = Mock(read_job=Mock(return_value=observation(reference)))
    service = ResultService(jobs, path)
    before = deepcopy(database.rows)
    retained = service.read_observation(reference)
    assert retained is not None
    with ResultStore.open(path) as reader:
        local = reader.read_result(reference)
    metadata = local.metadata
    assert metadata.outputs["a"].owner_scope == description.outputs["a"].owner_scope
    assert metadata.outputs["b"].scope == "location"
    assert local.tables["b"].crs.to_epsg() == 4326
    result = service.read_result(retained)
    assert isinstance(result, TableJobResult)
    assert result.job_id == metadata.job_id == reference
    inspection = service.inspect(retained)
    assert isinstance(inspection, TerminalResultOutput)
    assert inspection.job_id == metadata.job_id
    assert set(result.output_metadata) == {"a", "b"}
    jobs.read_job.return_value = None
    assert service.read_observation(reference) is None
    with ResultStore.open(path) as reader:
        assert reader.read_result(reference).metadata == metadata
        for name, frame in local.tables.items():
            pd.testing.assert_frame_equal(reader.read_table(reference, name), frame)
    assert database.rows == before


@pytest.mark.parametrize("damage", ["missing", "backing", "file", "metric"])
def test_broken_retained_reference_is_integrity_failure(
    database: Database, tmp_path: Path, damage: str
) -> None:
    path = tmp_path / "results.sqlite"
    writer = SQLiteStore(path)
    reference = publish_result(
        ResultDescriptions(writer), publish_values(writer, ("one",))
    )
    path.touch()
    if damage == "missing":
        reference = "f" * 32
    elif damage == "backing":
        database.rows[writer.layout.results.name].clear()
    elif damage == "file":
        path.unlink()
    before = deepcopy(database.rows)
    record = observation(reference)
    if damage == "metric":
        record.snapshot.metric = "another"
    service = ResultService(Mock(read_job=Mock(return_value=record)), path)
    observed = service.read_observation(reference)
    assert observed is not None
    for read in (service.read_result, service.inspect):
        with pytest.raises(InvalidJobResultError):
            read(observed)
    assert database.rows == before
    assert path.exists() is (damage != "file")


def observation(job_id: str) -> JobObservation:
    now = datetime.now(UTC)
    return JobObservation(
        snapshot=JobStatusInfo(
            job_id=job_id,
            status="succeeded",
            metric="metric",
            created_at=now,
            updated_at=now,
        ),
    )


def test_successful_queue_record_must_match_request(tmp_path: Path) -> None:
    service = ResultService(
        Mock(read_job=Mock(return_value=observation("a" * 32))), tmp_path
    )
    with pytest.raises(InvalidJobResultError, match="identity"):
        service.read_observation("b" * 32)


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
                    json=description.model_dump(),
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
