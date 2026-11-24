"""Effective shared inputs and unchanged base records across partition reuse."""

from dataclasses import replace
from pathlib import Path

import geopandas as gpd
import pytest
from pandera.engines.geopandas_engine import Geometry
from typing_extensions import override

from lyra.contracts.metric_scope import CachePolicy
from lyra.data.interventions import Intervention
from lyra.runtime.execution import GraphExecutionError
from lyra.runtime.local import LocalRunner
from lyra.runtime.results import ResultStore, RunResult
from lyra.sdk import (
    LocationDependency,
    Origin,
    PluginDefinition,
    location_operation,
    metric,
)
from lyra.sdk.resources import IOConfig
from test_support.location_operations import Places
from tests.frame_assertions import assert_logical_equal
from tests.metric_execution_helpers import schema
from tests.sqlite_helpers import database

__all__ = ["database"]


@pytest.mark.usefixtures("database")
@pytest.mark.parametrize("reference_policy", [None, "full", "per_location"])
def test_partition_reuse_depends_on_complete_effective_shared_input(
    tmp_path: Path,
    reference_policy: CachePolicy | None,
) -> None:
    base_reads = []
    handler_inputs = []

    class Survey(Places):
        id = "survey"
        cache = True

        @override
        def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
            base_reads.append(set(ids))
            return super().resolve(ids)

        @override
        def existing_ids(self, ids: list[str]) -> set[str]:
            return {"a", "b", "c", "outside"}.intersection(ids)

    class Reference(Survey):
        id = "reference"

    class ReferenceList(Places):
        id = "reference-list"

    @location_operation(
        name="reference_records",
        description="Select the reference records named by the listing",
        canonical=True,
        cache=reference_policy or "off",
        inputs={"listing": LocationDependency(provider=ReferenceList.id)},
        target_provider=Reference.id,
    )
    def reference_records(listing: gpd.GeoDataFrame) -> dict:
        return {key: (key,) for key in listing.location_id}

    @metric(
        name="comparison",
        description="Compare each place with a complete reference selection",
        cache="per_location",
        inputs={
            "places": LocationDependency(provider=Survey.id, primary=True),
            "references": LocationDependency(provider=Reference.id),
        },
        outputs={
            "data": schema(spatial=True).update_column(
                "geometry", dtype=Geometry(crs=4326)
            )
        },
    )
    def comparison(
        places: gpd.GeoDataFrame, references: gpd.GeoDataFrame
    ) -> gpd.GeoDataFrame:
        handler_inputs.append((set(places.location_id), set(references.location_id)))
        return gpd.GeoDataFrame(
            {
                "location_id": places.location_id,
                "row_key": "sum",
                "value": references["count"].sum(),
            },
            geometry=places.geometry,
        )

    definitions = {
        "test": PluginDefinition(
            metrics=[comparison],
            location_operations=[reference_records],
            resources={
                "survey": Survey,
                "reference": Reference,
                "listing": ReferenceList,
            },
        )
    }
    additions = Places(IOConfig(), {}).resolve(["r", "s"])
    proposal = Intervention(id="plan", catalog="reference", revision="1", add=additions)
    survey = Intervention(
        id="survey-plan", catalog="survey", revision="1", remove=["outside"]
    )

    def run(
        ids: str,
        proposal: Intervention,
        survey: Intervention,
        reference_ids: tuple[str, ...] = ("r", "s"),
    ) -> RunResult:
        runner = LocalRunner.from_definitions(
            definitions, result_store_path=tmp_path / "values"
        )
        return runner.run(
            "comparison",
            inputs={
                "places": {"provider": "survey", "ids": list(ids)},
                "references": {
                    "provider": ReferenceList.id if reference_policy else Reference.id,
                    "ids": list(reference_ids),
                },
            },
            interventions=[proposal, survey],
        )

    assert run("ab", proposal, survey).tables["data"].value.tolist() == [8, 8]
    mixed = run("bc", proposal, survey)
    assert mixed.tables["data"].value.tolist() == [8, 8]
    assert handler_inputs == [({"a", "b"}, {"r", "s"}), ({"c"}, {"r", "s"})]
    handler_inputs.clear()
    hit = run("cb", replace(proposal, add=additions.iloc[::-1]), survey)
    assert hit.tables["data"].value.tolist() == [8, 8]
    assert not handler_inputs

    revised = replace(proposal, revision="2", add=additions.assign(count=[1, 2]))
    updated = run("bc", revised, survey)
    assert updated.tables["data"].value.tolist() == [3, 3]
    assert run("bc", replace(revised, id="alternative"), survey).tables[
        "data"
    ].value.tolist() == [3, 3]
    assert run("bc", revised, replace(survey, revision="2")).tables[
        "data"
    ].value.tolist() == [3, 3]
    Reference.origins = (Origin(id="catalog", revision="new"),)
    assert run("bc", revised, survey).tables["data"].value.tolist() == [3, 3]
    assert handler_inputs == [({"b", "c"}, {"r", "s"})] * 4
    # A direct input explicitly names b even when the metric has no primary work.
    # On the routed path the operation derives b when its consumer needs records.
    removed = replace(proposal, revision="3", add=None, remove=["b"])
    with pytest.raises(GraphExecutionError, match=r"removed.*'b'"):
        run("b" if reference_policy else "", removed, survey, ("b",))
    assert base_reads == [{"a", "b"}, {"c"}]

    mixed.metadata.store_path.touch()
    with ResultStore.open(mixed.metadata.store_path) as reader:
        for result, counts in ((mixed, [4, 4]), (hit, [4, 4]), (updated, [1, 2])):
            assert_retained_comparison(
                reader,
                result,
                counts,
                tmp_path / "comparison.parquet",
                routed=reference_policy is not None,
            )


def assert_retained_comparison(
    reader: ResultStore,
    result: RunResult,
    counts: list[int],
    destination: Path,
    *,
    routed: bool,
) -> None:
    ref = result.metadata.job_id
    primary = reader.read_location_input(ref, "places")
    assert sorted(primary.location_id) == ["b", "c"]
    shared = reader.read_location_input(ref, "references").sort_values("location_id")
    assert shared.location_id.tolist() == ["r", "s"]
    assert shared["count"].tolist() == counts
    if routed:
        assert shared.source_location_id.tolist() == ["r", "s"]
    reader.export_geoparquet(ref, "data", destination)
    exported = gpd.read_parquet(destination)
    assert_logical_equal(
        exported.sort_values("location_id").reset_index(drop=True),
        result.tables["data"].sort_values("location_id").reset_index(drop=True),
    )
    scope = result.metadata.location_inputs["places"].provider_scope
    assert exported.attrs["lyra"]["description"]["owner_scope"] == scope.model_dump(
        mode="json"
    )
    assert exported.attrs["lyra"]["job_id"] == ref
