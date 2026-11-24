"""Execute published durable retrieval workflows against isolated HTTP routes."""

from __future__ import annotations

import runpy
from typing import TYPE_CHECKING
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pyarrow as pa
import pytest
from geopandas.testing import assert_geodataframe_equal
from starlette.applications import Starlette
from starlette.routing import Mount

from lyra.contracts.reports import ExecutionReport
from lyra.runtime.location_inputs import InputBindings, LocationInputs
from lyra.runtime.result_descriptions import ResultDescriptions
from lyra.runtime.results import ResultStore
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.server.main import create_app
from lyra.server.results import ResultService
from lyra.server.runtime import ApplicationRuntime
from test_support.publication import publish_result
from tests.cache_helpers import SCHEMA
from tests.config_helpers import api_credentials, load_test_config
from tests.location_input_arrow_helpers import RetainedInput, retained_input
from tests.location_input_helpers import retained as retained_locations
from tests.result_description_helpers import publish_values
from tests.retained_table_helpers import retained_service
from tests.retrieval_client_helpers import BASE, NativeAPI, native_api
from tests.sqlite_helpers import database
from tests.tutorial_helpers import published_python

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["database", "native_api", "retained_input"]
pytestmark = pytest.mark.usefixtures("database")


@pytest.mark.parametrize("native_api", ["sync"], indirect=True)
def test_reconnect_example(
    native_api: NativeAPI, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_test_config(tmp_path)
    config.api.public_base_url = BASE
    frame = pd.DataFrame({"value": pd.array([1, 2], dtype="Int64")})
    service, reference = retained_service(config.result_store_path, SCHEMA, frame)
    report = service.read_report(service.read_description(reference).job_id)
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
        "LYRA_JOB_ID": reference,
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
                "job_id": retained_input.reference,
                "input_name": "places",
            },
        )
    pd.testing.assert_frame_equal(values["frame"], retained_input.frame)
    with pa.ipc.open_stream(tmp_path / "input.arrow") as reader:
        assert reader.read_all().num_rows == len(retained_input.frame)


@pytest.mark.parametrize("native_api", ["local", "sync", "async"], indirect=True)
@pytest.mark.parametrize("input_kind", ["provider", "correspondence"])
@pytest.mark.parametrize("empty", [False, True])
def test_geoparquet_examples(
    native_api: NativeAPI,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    input_kind: str,
    *,
    empty: bool,
) -> None:
    config = load_test_config(tmp_path)
    config.api.public_base_url = BASE
    reference = publish_export_example(
        config.result_store_path, input_kind, empty=empty
    )
    service = ResultService(Mock(spec=[]), config.result_store_path)
    app = create_app(config, credentials=api_credentials())
    app.state.runtime = ApplicationRuntime(
        config, Mock(spec=[]), Mock(), Mock(), service.jobs, api_credentials(), service
    )
    public = Starlette(routes=[Mount("/public/api", app)])
    mode = "remote" if native_api.mode == "sync" else native_api.mode
    filename = f"export_{mode}.py"
    path = tmp_path / filename
    path.write_text(published_python("use/location-exports.md", filename))
    monkeypatch.chdir(tmp_path)
    examples = runpy.run_path(str(path))
    with native_api.serve(public, monkeypatch):
        arguments = (
            (config.result_store_path,)
            if mode == "local"
            else (BASE.removeprefix("https://"), "agent-secret")
        )
        assert (
            native_api.resolve(
                examples[f"export_{mode}"](*arguments, reference, "a", "places")
            )
            is None
        )
    assert_export_examples(config.result_store_path, reference, tmp_path)


def publish_export_example(path: Path, input_kind: str, *, empty: bool) -> str:
    storage = SQLiteStore(path)
    direct, correspondence = retained_locations(storage)
    selection = direct if input_kind == "provider" else correspondence
    if empty:
        selection = selection.model_copy(update={"records": (), "operations": ()})
    input_ref = LocationInputs(storage).publish(selection)
    values = publish_values(storage, () if empty else ("one", "two"), spatial=True)
    values = values.model_copy(
        update={
            "location_inputs": InputBindings(
                names=("places",), selections={"places": (input_ref,)}
            )
        }
    )
    reference = publish_result(ResultDescriptions(storage), values)
    storage.path.touch()
    return reference


def assert_export_examples(store_path: Path, reference: str, tmp_path: Path) -> None:
    with ResultStore.open(store_path) as store:
        description = store.describe(reference)
        expected_output = store.read_table(reference, "a")
        expected_input = store.read_location_input(reference, "places")
        expected_report = store.read_report(description.job_id)
    read_path = tmp_path / "read_export.py"
    read_path.write_text(published_python("use/location-exports.md", read_path.name))
    restored = runpy.run_path(str(read_path))
    for name, expected, selected, kind, name_field, selected_name in (
        (
            "output",
            expected_output,
            description.outputs["a"],
            "metric_output",
            "output_name",
            "a",
        ),
        (
            "locations",
            expected_input,
            description.location_inputs["places"],
            "location_input",
            "input_name",
            "places",
        ),
    ):
        actual = restored[name]
        assert isinstance(expected, gpd.GeoDataFrame)
        assert_geodataframe_equal(
            ordered_export(actual), ordered_export(expected), check_dtype=False
        )
        assert actual.attrs["lyra"] == {
            "kind": kind,
            "job_id": reference,
            name_field: selected_name,
            "description": selected.model_dump(mode="json"),
        }
    follow_path = tmp_path / "follow_export.py"
    follow_path.write_text(
        published_python("use/location-exports.md", follow_path.name)
    )
    assert runpy.run_path(str(follow_path))["follow_export"](
        store_path, restored["metadata"]
    ) == (description, expected_report)


def ordered_export(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    identifiers = [
        name
        for name in ("location_id", "source_location_id", "row_key")
        if name in frame.columns
    ]
    return frame.sort_values(identifiers).reset_index(drop=True)
