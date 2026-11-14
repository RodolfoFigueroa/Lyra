"""Typed mixed graphs across local, worker, cache, and presentation boundaries."""

from pathlib import Path

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from pandera.engines.geopandas_engine import Geometry
from typing_extensions import override

from lyra.api.client._tables import decode_tables  # ruff: ignore[import-private-name]
from lyra.contracts.dependencies import Input
from lyra.contracts.jobs import TableJobResult
from lyra.data.results import serialize_outputs
from lyra.runtime.execution import GraphExecutionError
from lyra.runtime.local import LocalRunner
from lyra.runtime.sqlite_store import DamagedDataError
from lyra.sdk.dependencies import Dependency, LocationDependency
from lyra.sdk.location_schema import LocationOutputSchema
from lyra.sdk.plugin import PluginDefinition, location_operation, metric
from lyra.server import worker
from test_support.location_resolution import Provider, frame_features, selection
from test_support.native_providers import native_schema
from tests.graph_execution_helpers import Label
from tests.queue_helpers import accepted_envelope, run_and_read
from tests.worker_graph_integration_helpers import install_definitions, native_job


def column(dtype: object, *, nullable: bool = False) -> pa.Column:
    if dtype == "geometry":
        return pa.Column(
            Geometry(crs=4326),
            checks=pa.Check(lambda s: s.is_valid, name="valid_geometry"),
            description="Observation",
            metadata={"unit": None},
        )
    return pa.Column(
        dtype, nullable=nullable, description="Observation", metadata={"unit": None}
    )


TABLE = pa.DataFrameSchema(
    {
        "id": column(str),
        "count": column("Int64", nullable=True),
        "observed": column(pd.StringDtype(), nullable=True),
    },
    strict=True,
)
SPATIAL = TABLE.add_columns({"geometry": column("geometry")})


def definitions_for_graph(
    *, empty: bool
) -> tuple[dict[str, PluginDefinition], list[str], list[bool]]:
    calls: list[str] = []
    accepted = [True]
    checked = pa.DataFrameSchema(
        TABLE.columns,
        strict=True,
        checks=pa.Check(lambda _: accepted[0], name="current_source"),
    )

    @metric(
        cache="full",
        code_revision="1",
        name="source",
        description="Source",
        outputs={"rows": checked, "empty": TABLE},
    )
    def source() -> dict[str, pd.DataFrame]:
        calls.append("source")
        frame = pd.DataFrame(
            {
                "id": pd.Series([] if empty else ["b", "a"], dtype="str"),
                "count": pd.Series([] if empty else [None, 7], dtype="Int64"),
                "observed": pd.Series(
                    [] if empty else [None, "2026-01-01T00:00:00.000Z"],
                    dtype="string",
                ),
            }
        )
        frame.index = pd.Index(range(10, 10 + len(frame)), name="incidental")
        return {"rows": frame.iloc[:, ::-1], "empty": frame.iloc[:0]}

    class TypedProvider(Provider):
        schema = native_schema(LocationOutputSchema(SPATIAL), "EPSG:4326")

        @override
        def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
            result = frame_features(ids).drop(columns="original")
            result["count"] = pd.Series(
                [None if item == "b" else 7 for item in ids], dtype="Int64"
            )
            result["observed"] = pd.Series(
                [None if item == "b" else "2026-01-01T00:00:00.000Z" for item in ids],
                dtype="string",
            )
            return result

    @location_operation(
        cache="full",
        code_revision="1",
        name="places",
        description="Places",
        inputs={"location": LocationDependency(provider=TypedProvider.id)},
        target_provider=TypedProvider.id,
    )
    def places(location: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        calls.append("places")
        assert location.location_id.tolist() == ["b", "a"]
        return {} if empty else {key: (key,) for key in location.location_id}

    @metric(
        cache="full",
        code_revision="1",
        name="root",
        description="Root",
        outputs={"answer": TABLE, "vacant": TABLE},
        inputs={
            "location": LocationDependency(provider=TypedProvider.id),
            "rows": Dependency("rows", producer="source"),
            "vacant": Dependency("empty", producer="source"),
            "spatial": Dependency(
                "spatial_rows",
                producer="spatial",
            ).bind(
                location=LocationDependency("places").bind(location=Input("location"))
            ),
        },
    )
    def root(
        location: gpd.GeoDataFrame,
        rows: pd.DataFrame,
        vacant: pd.DataFrame,
        spatial: pd.DataFrame,
        parameters: Label,
    ) -> dict[str, pd.DataFrame]:
        calls.append("root")
        assert location.location_id.tolist() == ["b", "a"]
        pd.testing.assert_frame_equal(rows, spatial)
        pd.testing.assert_frame_equal(vacant, rows.iloc[:0])
        assert list(rows.columns) == ["id", "count", "observed"]
        assert isinstance(rows.dtypes.iloc[0], pd.StringDtype)
        assert rows.dtypes.tolist()[1:] == [
            pd.Int64Dtype(),
            pd.StringDtype(),
        ]
        if parameters.label == "bad_consumer":
            return {"answer": rows[["undeclared"]], "vacant": vacant}
        return {"answer": rows, "vacant": vacant}

    @metric(
        cache="full",
        code_revision="1",
        name="spatial",
        description="Spatial",
        inputs={"location": LocationDependency(provider=TypedProvider.id)},
        outputs={"spatial_rows": TABLE},
    )
    def spatial(location: gpd.GeoDataFrame) -> pd.DataFrame:
        calls.append("spatial")
        assert location.crs is not None
        assert location.crs.to_epsg() == 4326
        assert location.geometry.x.tolist() == [1.0] * len(location)
        assert location.source_location_id.tolist() == location.location_id.tolist()
        return pd.DataFrame(
            location.drop(columns=["geometry", "source_location_id"])
        ).rename(columns={"location_id": "id"})

    definitions = {
        "graph": PluginDefinition(
            metrics=[source, root, spatial], location_operations=[places]
        ),
        "provider": PluginDefinition(resources={"locations": TypedProvider}),
    }
    return definitions, calls, accepted


@pytest.mark.parametrize("entry", ["local", "worker"])
@pytest.mark.parametrize("empty", [False, True])
def test_typed_mixed_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str, *, empty: bool
) -> None:
    definitions, calls, accepted = definitions_for_graph(empty=empty)
    result_store_path = tmp_path / "cache" / "results.sqlite3"
    runner = LocalRunner.from_definitions(
        definitions, result_store_path=result_store_path
    )
    install_definitions(
        tmp_path, monkeypatch, definitions, result_store_path=result_store_path
    )
    native = native_job(monkeypatch)

    def run(label: str) -> dict:
        inputs = {
            "location": selection("b", "a").model_dump(mode="json"),
            "parameters": {"label": label},
        }
        if entry == "local":
            return serialize_outputs(
                "root",
                runner.run(
                    "root",
                    inputs={k: v for k, v in inputs.items() if k != "parameters"},
                    parameters=inputs.get("parameters"),
                ).tables,
            )

        envelope = accepted_envelope(job_id=native.id, metric="root", inputs=inputs)
        payload = run_and_read(worker.run_metric_task, envelope.model_dump(mode="json"))
        result = TableJobResult.model_validate(payload)
        decoded = decode_tables(result)
        assert list(decoded["vacant"].columns) == list(TABLE.columns)
        assert decoded["vacant"].empty
        return result.outputs

    try:
        fresh = run("first")
        assert sorted(calls) == ["places", "root", "source", "spatial"]
        calls.clear()
        assert run("second") == fresh
        assert calls == ["root"]  # Dependencies restore typed data before consumption.
        calls.clear()
        assert run("second") == fresh
        assert calls == []
        assert fresh["vacant"] == []
        if not empty:
            assert fresh["answer"] == [
                {"id": "b", "count": None, "observed": None},
                {"id": "a", "count": 7, "observed": "2026-01-01T00:00:00.000Z"},
            ]
        with pytest.raises(GraphExecutionError) as error:
            run("bad_consumer")
        assert error.value.node == "root"
        assert error.value.path == ("root",)
        assert isinstance(error.value.__cause__, KeyError)
        calls.clear()
        accepted[0] = False
        with pytest.raises(GraphExecutionError) as error:
            run("changed_check")
        assert error.value.node == "source"
        assert isinstance(error.value.__cause__, DamagedDataError)
        assert calls == []
    finally:
        worker.RUNNER_REGISTRY.clear()
        worker.set_runner_temp_base(None)
