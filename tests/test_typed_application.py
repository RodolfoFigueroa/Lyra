"""Typed mixed graphs across local, worker, cache, and presentation boundaries."""

from pathlib import Path

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from lyra.api.client.results import decode_tables
from lyra.sdk import (
    Dependency,
    Input,
    LocalRunner,
    LocationDependency,
    Locations,
    PluginDefinition,
    location_operation,
    metric,
)
from lyra.sdk.execution import GraphExecutionError
from lyra.sdk.models.job import JobEnvelope, TableJobResult
from lyra.sdk.results import serialize_outputs

from lyra_app import worker
from tests.test_graph_execution import Label
from tests.test_location_resolution import Provider, selection
from tests.test_worker_graph_integration import install_definitions, native_job


def column(dtype: object, *, nullable: bool = False) -> pa.Column:
    return pa.Column(
        dtype, nullable=nullable, description="Observation", metadata={"unit": None}
    )


TABLE = pa.DataFrameSchema(
    {
        "id": column(str),
        "count": column("Int64", nullable=True),
        "observed": column(pd.DatetimeTZDtype(unit="us", tz="UTC"), nullable=True),
    }
)
SPATIAL = TABLE.add_columns({"geometry": column("geometry")})


def definitions_for_graph(
    *, empty: bool
) -> tuple[dict[str, PluginDefinition], list[str], list[bool]]:
    calls: list[str] = []
    accepted = [True]
    checked = pa.DataFrameSchema(
        TABLE.columns, checks=pa.Check(lambda _: accepted[0], name="current_source")
    )

    @metric(
        name="source", description="Source", outputs={"rows": checked, "empty": TABLE}
    )
    def source() -> dict[str, pd.DataFrame]:
        calls.append("source")
        frame = pd.DataFrame(
            {
                "id": pd.Series([] if empty else ["b", "a"], dtype="str"),
                "count": pd.Series([] if empty else [None, 7], dtype="Int64"),
                "observed": pd.Series(
                    [] if empty else [None, pd.Timestamp("2026-01-01", tz="UTC")],
                    dtype="datetime64[us, UTC]",
                ),
            }
        )
        frame.index = pd.Index(range(10, 10 + len(frame)), name="incidental")
        return {"rows": frame.iloc[:, ::-1], "empty": frame.iloc[:0]}

    @location_operation(
        name="places",
        description="Places",
        output=SPATIAL,
        scope_from=Input("location"),
        inputs={"rows": Dependency("rows")},
    )
    def places(location: Locations, rows: pd.DataFrame) -> gpd.GeoDataFrame:
        calls.append("places")
        assert list(location.ids) == ["b", "a"]
        assert isinstance(rows.index, pd.RangeIndex)
        shapes = location.to_geodataframe().set_index("id").geometry
        return gpd.GeoDataFrame(
            rows.copy(), geometry=list(shapes.loc[rows.id]), crs=shapes.crs
        )

    @metric(
        name="root",
        description="Root",
        outputs={"answer": TABLE, "vacant": TABLE},
        inputs={
            "rows": Dependency("rows"),
            "vacant": Dependency("empty"),
            "spatial": Dependency(
                "spatial_rows",
                locations={
                    "location": LocationDependency(
                        "places", locations={"location": Input("location")}
                    )
                },
            ),
        },
    )
    def root(
        location: Locations,
        rows: pd.DataFrame,
        vacant: pd.DataFrame,
        spatial: pd.DataFrame,
        parameters: Label,
    ) -> dict[str, pd.DataFrame]:
        calls.append("root")
        assert list(location.ids) == ["b", "a"]
        pd.testing.assert_frame_equal(rows, spatial)
        pd.testing.assert_frame_equal(vacant, rows.iloc[:0])
        assert list(rows.columns) == ["id", "count", "observed"]
        assert rows.dtypes.tolist() == [
            pd.StringDtype(na_value=float("nan")),
            pd.Int64Dtype(),
            pd.DatetimeTZDtype(unit="us", tz="UTC"),
        ]
        if parameters.label == "bad_consumer":
            return {"answer": rows[["undeclared"]], "vacant": vacant}
        return {"answer": rows, "vacant": vacant}

    @metric(name="spatial", description="Spatial", outputs={"spatial_rows": TABLE})
    def spatial(location: Locations) -> pd.DataFrame:
        calls.append("spatial")
        assert location.collection == "test:units"
        assert location.unit_type == "lot"
        crs = location.to_geodataframe().crs
        assert crs is not None
        assert crs.to_epsg() == 4326
        assert location.to_geodataframe().geometry.x.tolist() == [1.0] * len(location)
        return location.to_dataframe()

    definitions = {
        "graph": PluginDefinition(
            metrics=[source, root, spatial], location_operations=[places]
        ),
        "provider": PluginDefinition(
            resources={"locations": Provider}, location_provider="locations"
        ),
    }
    return definitions, calls, accepted


@pytest.mark.parametrize("entry", ["local", "worker"])
@pytest.mark.parametrize("empty", [False, True])
def test_typed_mixed_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str, *, empty: bool
) -> None:
    definitions, calls, accepted = definitions_for_graph(empty=empty)
    cache_dir = tmp_path / "cache"
    runner = LocalRunner.from_definitions(definitions, cache_dir=cache_dir)
    install_definitions(tmp_path, monkeypatch, definitions, cache_dir=cache_dir)
    native = native_job(monkeypatch)

    def run(label: str) -> dict:
        inputs = {
            "location": selection("b", "a").model_dump(mode="json"),
            "parameters": {"label": label},
        }
        if entry == "local":
            return serialize_outputs("root", runner.run("root", inputs))
        native.meta["provenance"]["input"] = inputs
        envelope = JobEnvelope(job_id=native.id, metric="root", input=inputs)
        payload = worker.run_metric_task(envelope.model_dump(mode="json"))
        result = TableJobResult.model_validate(payload)
        decoded = decode_tables(result)
        assert list(decoded["vacant"].columns) == list(TABLE.columns)
        assert decoded["vacant"].empty
        return result.outputs

    try:
        fresh = run("first")
        assert calls == ["source", "places", "spatial", "root"]
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
        assert calls == ["source"]  # Rejected cache and fresh result never reach root.
    finally:
        worker.RUNNER_REGISTRY.clear()
        worker.set_runner_temp_base(None)
