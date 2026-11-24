"""Owned metric outputs and completion at the shared executor boundary."""

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from shapely.geometry import Point

from lyra.runtime.execution import GraphExecutionError
from lyra.sdk import Dependency, Input, LocationDependency, metric
from lyra.sdk.storage_schema import empty_frame
from test_support.location_schema import column
from tests.metric_execution_helpers import declaration, schema, setup


@pytest.mark.parametrize("spatial", [False, True])
def test_named_outputs_complete_zero_row_owners_and_detach(*, spatial: bool) -> None:
    retained = []
    output_schema = schema(spatial=spatial)

    @declaration({"first": output_schema, "second": output_schema})
    def findings(main: gpd.GeoDataFrame, reference: gpd.GeoDataFrame) -> object:
        assert reference.location_id.tolist() == ["reference"]
        main.loc[:, "location_id"] = "edited"
        frame = pd.DataFrame(
            {
                "location_id": ["a", "b"],
                "row_key": ["same", "same"],
                "value": pd.array([1, None], dtype="Int64"),
            }
        )
        if spatial:
            frame = gpd.GeoDataFrame(frame, geometry=[Point(0, 0)] * 2, crs=4326)
        frame.attrs = {"nested": [1]}
        retained.append(frame)
        return {"first": frame, "second": frame}

    executor, invocation = setup(findings)
    outcome = executor.execute(invocation)
    retained[0].loc[0, "location_id"] = "changed"
    retained[0].attrs["nested"].append(2)
    for frame in outcome.outputs.values():
        assert frame.location_id.tolist() == ["a", "b"]
        assert str(frame.value.dtype) == "Int64"
        assert frame.attrs["nested"] == [1]
    assert len(outcome.completions) == 2
    for completion in outcome.completions:
        assert completion.partitions == frozenset({"a", "b", "c"})
        assert completion.provider_scope == invocation.locations["main"].contract
        assert completion.scope == "location"


@pytest.mark.parametrize(
    "fault",
    [
        "reference",
        "edited",
        "duplicate",
        "extra",
        "missing",
        "duplicate_column",
        "none",
        "dictionary",
        "check",
    ],
)
def test_invalid_results_never_complete(fault: str) -> None:
    @declaration({"data": schema()})
    def findings(main: gpd.GeoDataFrame, reference: gpd.GeoDataFrame) -> object:
        assert reference.location_id.tolist() == ["reference"]
        main.loc[:, "location_id"] = "edited"
        frame = pd.DataFrame(
            {
                "location_id": [fault if fault in {"reference", "edited"} else "a"],
                "row_key": ["row"],
                "value": pd.array([-1 if fault == "check" else 1], dtype="Int64"),
            }
        )
        if fault == "duplicate":
            return pd.concat([frame, frame])
        if fault == "extra":
            frame["extra"] = 1
        if fault == "missing":
            frame = frame.drop(columns="row_key")
        if fault == "duplicate_column":
            frame = pd.concat([frame, frame[["value"]]], axis=1)
        if fault == "none":
            return None
        if fault == "dictionary":
            return {"data": frame}
        return frame

    executor, invocation = setup(findings)
    with pytest.raises(GraphExecutionError):
        executor.execute(invocation)
    assert executor.provenance is not None
    assert not executor.provenance.execution.succeeded
    assert all(
        item.outcome != "completed"
        for item in executor.provenance.execution.observations
    )


@pytest.mark.parametrize("spatial", [False, True])
def test_no_partitions_skip_handler_and_preserve_empty_schema(*, spatial: bool) -> None:
    output_schema = schema(spatial=spatial)

    @declaration({"data": output_schema})
    def findings(main: gpd.GeoDataFrame, reference: gpd.GeoDataFrame) -> object:
        assert reference.location_id.tolist() == ["reference"]
        assert main.empty
        pytest.fail("Zero partitions must not invoke author code")

    executor, invocation = setup(findings, ids=())
    outcome = executor.execute(invocation)
    pd.testing.assert_frame_equal(outcome.outputs["data"], empty_frame(output_schema))
    assert outcome.completions[0].partitions == frozenset()
    assert outcome.provenance.execution.observations == ()


def test_unpartitioned_empty_inputs_execute_and_complete_whole_computation() -> None:
    @declaration(
        {"data": pa.DataFrameSchema({"count": column(int)}, strict=True)}, primary=False
    )
    def findings(main: gpd.GeoDataFrame, reference: gpd.GeoDataFrame) -> object:
        assert reference.location_id.tolist() == ["reference"]
        return pd.DataFrame({"count": [len(main)]})

    executor, invocation = setup(findings, ids=())
    outcome = executor.execute(invocation)
    assert outcome.outputs["data"].to_dict("list") == {"count": [0]}
    assert outcome.completions[0].scope == "unpartitioned"
    assert outcome.completions[0].provider_scope is None


def test_downstream_named_inputs_and_producer_references_are_independent() -> None:
    retained = []

    @metric(
        name="producer",
        description="Producer",
        code_revision="1",
        inputs={"main": LocationDependency(provider="tracts", primary=True)},
        outputs={"data": schema()},
    )
    def producer(main: gpd.GeoDataFrame) -> pd.DataFrame:
        assert "a" in main.location_id.tolist()
        frame = pd.DataFrame(
            {
                "location_id": ["a"],
                "row_key": ["row"],
                "value": pd.array([1], dtype="Int64"),
            }
        )
        retained.append(frame)
        return frame

    dependency = Dependency("data", producer="producer").bind(main=Input("main"))

    @declaration(
        {"data": schema()}, dependencies={"left": dependency, "right": dependency}
    )
    def findings(
        main: gpd.GeoDataFrame,
        reference: gpd.GeoDataFrame,
        left: pd.DataFrame,
        right: pd.DataFrame,
    ) -> pd.DataFrame:
        assert "a" in main.location_id.tolist()
        assert reference.location_id.tolist() == ["reference"]
        retained[0].loc[0, "value"] = 9
        left.loc[0, "value"] = 8
        assert right.value.tolist() == [1]
        return right

    executor, invocation = setup(findings, producer)
    assert executor.execute(invocation).outputs["data"].value.tolist() == [1]


@pytest.mark.parametrize("fault", ["missing", "extra", "none"])
def test_multiple_outputs_require_every_explicit_table(fault: str) -> None:
    @declaration({"first": schema(), "second": schema()})
    def findings(main: gpd.GeoDataFrame, reference: gpd.GeoDataFrame) -> object:
        assert len(main) == 3
        assert len(reference) == 1
        values = {"first": empty_frame(schema()), "second": empty_frame(schema())}
        if fault == "missing":
            del values["second"]
        elif fault == "extra":
            values["extra"] = empty_frame(schema())
        else:
            return {"first": values["first"], "second": None}
        return values

    executor, invocation = setup(findings)
    with pytest.raises(GraphExecutionError):
        executor.execute(invocation)


@pytest.mark.parametrize("spatial", [False, True])
@pytest.mark.parametrize("empty", [False, True])
def test_explicit_empty_and_all_null_values_preserve_types_and_completion(
    *, spatial: bool, empty: bool
) -> None:
    output_schema = schema(spatial=spatial)

    @declaration({"data": output_schema})
    def findings(main: gpd.GeoDataFrame, reference: gpd.GeoDataFrame) -> pd.DataFrame:
        assert len(main) == 3
        assert len(reference) == 1
        if empty:
            return empty_frame(output_schema)
        frame = pd.DataFrame(
            {
                "location_id": ["a"],
                "row_key": ["row"],
                "value": pd.Series([pd.NA], dtype="Int64"),
            }
        )
        if spatial:
            return gpd.GeoDataFrame(frame, geometry=[Point(0, 0)], crs=4326)
        return frame

    executor, invocation = setup(findings)
    outcome = executor.execute(invocation)
    frame = outcome.outputs["data"]
    assert str(frame.value.dtype) == "Int64"
    assert frame.value.isna().all()
    assert len(frame) == (0 if empty else 1)
    if spatial:
        assert isinstance(frame, gpd.GeoDataFrame)
        assert frame.crs == "EPSG:4326"
    assert outcome.completions[0].partitions == frozenset({"a", "b", "c"})
    assert outcome.provenance.execution.observations[-1].outcome == "completed"
