"""Direct primary relationships preserve shared context under reduced demand."""

from dataclasses import replace

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from lyra.contracts.metric_scope import CachePolicy
from lyra.runtime.native_inputs import NativeLocationInput
from lyra.runtime.planning.preparation import prepare_execution
from lyra.sdk import Dependency, Input, LocationDependency, metric
from lyra.sdk.storage_schema import empty_frame
from tests.metric_execution_helpers import declaration, schema, setup


@pytest.mark.parametrize("policy", ["off", "full", "per_location"])
@pytest.mark.parametrize("binding_style", ["matching", "explicit", "renamed"])
@pytest.mark.parametrize("references", [("x", "y"), ("a", "b")])
@pytest.mark.parametrize("zero_rows", [False, True])
def test_related_selection_and_complete_independent_uses(
    policy: CachePolicy,
    binding_style: str,
    references: tuple[str, ...],
    *,
    zero_rows: bool,
) -> None:
    seen = []
    primary_name = "items" if binding_style == "renamed" else "main"

    def calculate(primary: gpd.GeoDataFrame, shared: gpd.GeoDataFrame) -> pd.DataFrame:
        owners = set(primary.location_id)
        context = set(shared.location_id)
        seen.append((owners, context))
        if zero_rows:
            return empty_frame(schema())
        return pd.DataFrame(
            [(owner, key, 1) for owner in owners for key in ("first", "second")],
            columns=["location_id", "row_key", "value"],
        )

    def named(main: gpd.GeoDataFrame, shared: gpd.GeoDataFrame) -> pd.DataFrame:
        return calculate(main, shared)

    def renamed(items: gpd.GeoDataFrame, shared: gpd.GeoDataFrame) -> pd.DataFrame:
        return calculate(items, shared)

    producer = metric(
        name="producer",
        description="Measurements with independent context",
        code_revision="1",
        cache=policy,
        inputs={
            primary_name: LocationDependency(provider="tracts", primary=True),
            "shared": LocationDependency(provider="tracts"),
        },
        outputs={"data": schema()},
    )(renamed if binding_style == "renamed" else named)
    main_bindings = {"shared": Input("reference")}
    if binding_style != "matching":
        main_bindings[primary_name] = Input("main")
    related = Dependency("data", producer="producer").bind(**main_bindings)
    independent = Dependency("data", producer="producer").bind(
        **{primary_name: Input("reference"), "shared": Input("main")}
    )

    @declaration(
        {"data": schema()},
        dependencies={"measurements": related, "references": independent},
    )
    def findings(
        main: gpd.GeoDataFrame,
        reference: gpd.GeoDataFrame,
        measurements: pd.DataFrame,
        references: pd.DataFrame,
    ) -> pd.DataFrame:
        assert set(main.location_id) == {"b"}
        assert set(reference.location_id) == set(reference_ids)
        assert set(measurements.location_id) == (set() if zero_rows else {"b"})
        assert set(references.location_id) == (
            set() if zero_rows else set(reference_ids)
        )
        if not zero_rows:
            assert set(measurements.row_key) == {"first", "second"}
            assert len(references) == 2 * len(reference_ids)
        return measurements

    reference_ids = references
    executor, invocation = setup(findings, producer, ids=("a", "b"))
    locations = dict(invocation.locations)
    locations["reference"] = NativeLocationInput(
        gpd.GeoDataFrame(
            {"location_id": references},
            geometry=[Point(0, 0)] * len(references),
            crs=4326,
        ),
        invocation.locations["reference"].contract,
    )
    bound = executor.validate_plan(
        prepare_execution(
            executor.catalog,
            {
                "metric": "findings",
                "inputs": {
                    "main": {"provider": "tracts", "ids": ["a", "b"]},
                    "reference": {"provider": "tracts", "ids": list(references)},
                },
            },
        )
    )
    if references != ("a", "b"):
        assert (
            bound.dependencies["measurements"].identity
            != bound.dependencies["references"].identity
        )
    invocation = replace(
        invocation, parameters=None, locations=locations, bound_use=bound
    )
    reduced = invocation.select_primary("main", frozenset({"b"}))
    outcome = executor.execute(reduced)
    expected = {"b"} if policy == "per_location" else {"a", "b"}
    assert (expected, set(reference_ids)) in seen
    assert (set(reference_ids), {"a", "b"}) in seen
    root_completion = next(
        c for c in outcome.completions if c.invocation == reduced.identity
    )
    assert root_completion.partitions == {"b"}
    assert root_completion.provider_scope == invocation.locations["main"].contract
    assert any(c.partitions == expected for c in outcome.completions)


def test_original_context_participates_in_reduced_identity() -> None:
    @declaration({"data": schema()})
    def findings(main: gpd.GeoDataFrame, reference: gpd.GeoDataFrame) -> pd.DataFrame:
        assert len(main) > 0
        assert len(reference) > 0
        return empty_frame(schema())

    _, first = setup(findings, ids=("a", "b"))
    _, second = setup(findings, ids=("b", "c"))
    demand = frozenset({"b"})
    assert (
        first.select_primary("main", demand).identity
        != second.select_primary("main", demand).identity
    )
    assert first.select_primary("main", demand).original().identity == first.identity
    with pytest.raises(ValueError, match="exceeds"):
        first.select_primary("main", frozenset({"missing"}))
