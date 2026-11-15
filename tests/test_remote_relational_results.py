"""Remote lookups share immutable native values with local retained access."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest

from lyra.contracts.inspection import TerminalResultOutput
from lyra.contracts.jobs import JobStatusInfo, TableJobResult
from lyra.runtime.result_descriptions import ResultDescriptions
from lyra.runtime.results import ResultStore
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.server.job_store import InvalidJobResultError, JobObservation
from lyra.server.results import ResultService
from test_support.publication import publish_result
from tests.result_description_helpers import publish_values
from tests.sqlite_helpers import Database, database

__all__ = ["database"]


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
    inspection = service.inspect(retained)
    assert isinstance(inspection, TerminalResultOutput)
    assert inspection.execution_id == metadata.execution_id
    assert set(result.output_metadata) == {"a", "b"}
    jobs.read_job.return_value = None
    assert service.read_observation("job") is None
    with ResultStore.open(path) as reader:
        assert reader.read_result(reference).metadata == metadata
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
            job_id="job", status="succeeded", created_at=now, updated_at=now
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
