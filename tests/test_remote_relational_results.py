"""Remote lookups share immutable native values with local retained access."""

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest

from lyra.contracts.jobs import JobStatusInfo, TableJobResult
from lyra.runtime.result_descriptions import ResultDescriptions
from lyra.runtime.results import ResultStore
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.server.job_store import InvalidJobResultError, JobObservation
from lyra.server.results import ResultService
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
    reference = ResultDescriptions(writer).publish(description)
    path.touch()
    jobs = Mock(read_job=Mock(return_value=observation(reference)))
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
    reference = ResultDescriptions(writer).publish(publish_values(writer, ("one",)))
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
        Mock(read_job=Mock(return_value=observation(reference))), path
    )
    with pytest.raises(InvalidJobResultError):
        service.read_observation("job")
    assert database.rows == before
    assert path.exists() is (damage != "file")


def observation(reference: str) -> JobObservation:
    now = datetime.now(UTC)
    return JobObservation(
        snapshot=JobStatusInfo(
            job_id="job", status="succeeded", created_at=now, updated_at=now
        ),
        result_ref=reference,
    )
