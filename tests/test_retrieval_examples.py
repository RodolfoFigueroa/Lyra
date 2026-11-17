"""Execute published durable retrieval workflows against isolated HTTP routes."""

from __future__ import annotations

import runpy
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pandas as pd
import pyarrow as pa
import pytest
from starlette.applications import Starlette
from starlette.routing import Mount

from lyra.contracts.reports import ExecutionReport
from lyra.server.main import create_app
from lyra.server.results import ResultService
from lyra.server.runtime import ApplicationRuntime
from tests.arrow_download_helpers import Retained, retained
from tests.cache_helpers import SCHEMA
from tests.config_helpers import api_credentials, load_test_config
from tests.location_input_arrow_helpers import RetainedInput, retained_input
from tests.retained_table_helpers import retained_service
from tests.retrieval_client_helpers import BASE, NativeAPI, native_api
from tests.sqlite_helpers import database
from tests.tutorial_helpers import published_python

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["database", "native_api", "retained", "retained_input"]
pytestmark = pytest.mark.usefixtures("database")


@pytest.mark.parametrize("native_api", ["sync"], indirect=True)
def test_reconnect_example(
    native_api: NativeAPI, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_test_config(tmp_path)
    config.api.public_base_url = BASE
    frame = pd.DataFrame({"value": pd.array([1, 2], dtype="Int64")})
    service, reference = retained_service(config.result_store_path, SCHEMA, frame)
    report = service.read_report(service.read_description(reference).execution_id)
    monkeypatch.setattr(
        service.jobs, "read_job", Mock(side_effect=AssertionError("Expired job"))
    )
    app = create_app(config, credentials=api_credentials())
    app.state.runtime = ApplicationRuntime(
        config, Mock(spec=[]), Mock(), Mock(), service.jobs, api_credentials(), service
    )
    public = Starlette(routes=[Mount("/public/api", app)])
    for name, value in {
        "LYRA_SERVER": BASE.removeprefix("https://"),
        "LYRA_AGENT_API_KEY": "agent-secret",
        "LYRA_RESULT_REF": reference,
        "LYRA_EXECUTION_ID": report.execution_id,
        "LYRA_OUTPUT_NAME": "rows",
    }.items():
        monkeypatch.setenv(name, value)
    path = tmp_path / "read_retained.py"
    path.write_text(published_python("use/python-client.md", path.name))
    monkeypatch.chdir(tmp_path)
    with native_api.serve(public, monkeypatch):
        values = runpy.run_path(str(path))
    pd.testing.assert_frame_equal(values["table"], frame)
    assert values["report"] == report
    assert (
        ExecutionReport.model_validate_json((tmp_path / "report.json").read_bytes())
        == report
    )
    with pa.ipc.open_stream(tmp_path / "output.arrow") as reader:
        pd.testing.assert_frame_equal(reader.read_all().to_pandas(), frame)


@pytest.mark.parametrize("native_api", ["sync"], indirect=True)
@pytest.mark.parametrize(
    ("retained", "filename"),
    [
        ((False, "populated"), "iterate_rows.py"),
        ((False, "empty"), "iterate_rows.py"),
        ((False, "oversized-first"), "recover_rows.py"),
        ((False, "oversized-after"), "recover_rows.py"),
    ],
    indirect=["retained"],
)
def test_row_examples(
    retained: Retained,
    native_api: NativeAPI,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
) -> None:
    path = tmp_path / filename
    path.write_text(published_python("use/results.md", filename))
    monkeypatch.chdir(tmp_path)
    with native_api.serve(retained.public, monkeypatch), native_api.connect() as client:
        values = runpy.run_path(
            str(path),
            init_globals={
                "client": client,
                "result_ref": retained.reference,
                "output_name": "rows",
            },
        )
    if filename == "recover_rows.py":
        with pa.ipc.open_stream(tmp_path / "complete.arrow") as reader:
            pd.testing.assert_frame_equal(reader.read_all().to_pandas(), retained.frame)
    else:
        assert values["cursor"] is None
        assert len(values["page"].rows) == len(retained.frame)


@pytest.mark.parametrize("native_api", ["sync"], indirect=True)
@pytest.mark.parametrize("retained_input", ["populated", "empty"], indirect=True)
def test_location_input_example(
    retained_input: RetainedInput,
    native_api: NativeAPI,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_test_config(tmp_path)
    config.api.public_base_url = BASE
    config.result_store_path = retained_input.store.path
    jobs = Mock(spec=[])
    service = ResultService(jobs, retained_input.store.path)
    app = create_app(config, credentials=api_credentials())
    app.state.runtime = ApplicationRuntime(
        config, Mock(spec=[]), Mock(), Mock(), jobs, api_credentials(), service
    )
    public = Starlette(routes=[Mount("/public/api", app)])
    path = tmp_path / "read_location_input.py"
    path.write_text(published_python("use/results.md", path.name))
    monkeypatch.chdir(tmp_path)
    with native_api.serve(public, monkeypatch), native_api.connect() as client:
        values = runpy.run_path(
            str(path),
            init_globals={
                "client": client,
                "result_ref": retained_input.reference,
                "input_name": "places",
            },
        )
    pd.testing.assert_frame_equal(values["frame"], retained_input.frame)
    assert len(values["page"].rows) == len(retained_input.frame)
    with pa.ipc.open_stream(tmp_path / "input.arrow") as reader:
        assert reader.read_all().num_rows == len(retained_input.frame)
