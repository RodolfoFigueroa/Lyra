"""Managed preparation and routing over registered native declarations."""

from collections.abc import Callable
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from pydantic import Field

from lyra.contracts.dependencies import Dependency, Input, LocationDependency
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.planning.discovery import Discovery
from lyra.runtime.planning.preparation import prepare_execution
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition, location_operation, metric
from tests.live_graph_helpers import operation
from tests.node_declaration_helpers import Tracts, provider


class Options(MetricParameters):
    weights: list[int] = Field(default_factory=lambda: [1], description="Weights.")


@metric(
    name="score",
    code_revision="1",
    description="Score counties.",
    inputs={"places": LocationDependency(provider="counties")},
    outputs={"value": pa.DataFrameSchema(strict=True)},
)
def score(places: gpd.GeoDataFrame, *, parameters: Options) -> pd.DataFrame:
    del places, parameters
    pytest.fail("Preparation invoked a metric")


def definitions(operations: list[Callable], preferences: tuple = ()) -> dict:
    result = {
        "providers": PluginDefinition(
            resources={
                "tracts": Tracts,
                "regions": provider(id="regions"),
                "counties": provider(id="counties"),
            }
        ),
        "metrics": PluginDefinition(metrics=[score]),
    }
    if operations:
        result["routes"] = PluginDefinition(
            location_operations=operations, route_preferences=preferences
        )
    return result


def test_live_routes_discovery_and_owned_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acquire = Mock(side_effect=AssertionError("acquisition during preparation"))
    monkeypatch.setattr(Tracts, "__init__", acquire)
    catalog = compile_live_catalog(
        definitions(
            [
                operation("first", "tracts", "regions"),
                operation("second", "regions", "counties"),
            ]
        )
    )
    inspection = Discovery(catalog).inspect("score")
    assert inspection.metric.accepted_selections == {
        "places": ["counties", "regions", "tracts"]
    }
    for source, route in [
        ("counties", ()),
        ("regions", ("second",)),
        ("tracts", ("first", "second")),
    ]:
        request = {
            "metric": "score",
            "inputs": {"places": {"provider": source, "ids": []}},
            "parameters": {"weights": [2]},
        }
        prepared = prepare_execution(catalog, request)
        assert prepared.inputs[0].binding.operations == route
        assert prepared.inputs[0].ids == ()
        request["parameters"]["weights"].append(3)
        request["inputs"]["places"]["ids"].append("later")
        assert prepared.request.parameters == {"weights": [2]}
        assert prepared.inputs[0].ids == ()
    acquire.assert_not_called()


@pytest.mark.parametrize("ids", [[""], [1], [None], "one"])
def test_strict_ids(ids: object) -> None:
    catalog = compile_live_catalog(definitions([]))
    with pytest.raises(ValueError, match="validation error"):
        prepare_execution(
            catalog,
            {
                "metric": "score",
                "inputs": {"places": {"provider": "counties", "ids": ids}},
            },
        )


@pytest.mark.parametrize("section", ["inputs", "parameters"])
def test_json_null_sections_are_invalid(section: str) -> None:
    catalog = compile_live_catalog(definitions([]))
    with pytest.raises(ValueError, match="validation error"):
        prepare_execution(catalog, {"metric": "score", section: None})


def test_required_selection_and_defaults() -> None:
    catalog = compile_live_catalog(definitions([]))
    with pytest.raises(ValueError, match="Expected inputs"):
        prepare_execution(catalog, {"metric": "score"})
    prepared = prepare_execution(
        catalog,
        {"metric": "score", "inputs": {"places": {"provider": "counties", "ids": []}}},
    )
    assert prepared.request.parameters == {"weights": [1]}


@pytest.mark.parametrize("reverse", [False, True])
def test_decorated_preferences_and_shortest_routes(*, reverse: bool) -> None:
    first = operation("first", "tracts", "counties")
    second = operation("second", "tracts", "counties")
    ops = [first, second]
    if reverse:
        ops.reverse()
    with pytest.raises(ValueError, match="Ambiguous"):
        compile_live_catalog(definitions(ops))
    catalog = compile_live_catalog(definitions(ops, ((second,),)))
    assert catalog.accepted_sources["score"]["places"]["tracts"].operations == (
        "second",
    )


@pytest.mark.parametrize(
    "case",
    ["empty", "unknown", "noncanonical", "disconnected", "longer", "conflicting"],
)
def test_invalid_live_preferences(case: str) -> None:
    direct = operation("direct", "tracts", "counties")
    first = operation("first", "tracts", "regions")
    second = operation("second", "regions", "counties")
    other = operation("other", "tracts", "counties", canonical=False)
    unknown = operation("unknown", "tracts", "counties")
    preferences = {
        "empty": ((),),
        "unknown": ((unknown,),),
        "noncanonical": ((other,),),
        "disconnected": ((second, first),),
        "longer": ((first, second),),
        "conflicting": ((direct,), (first, second)),
    }
    with pytest.raises(ValueError, match="preference"):
        compile_live_catalog(
            definitions([direct, first, second, other], preferences[case])
        )


def test_receiving_routes_and_derived_inputs() -> None:
    explicit = operation("explicit", "tracts", "regions", canonical=False)
    onward = operation("onward", "regions", "counties")

    @metric(
        name="consumer",
        code_revision="1",
        description="Combine inputs.",
        inputs={
            "seed": LocationDependency(provider="tracts"),
            "derived": LocationDependency("explicit").bind(source=Input("seed")),
            "table": Dependency("value", producer="score").bind(
                places=Input("derived")
            ),
        },
        outputs={"value": pa.DataFrameSchema(strict=True)},
    )
    def consumer(
        seed: gpd.GeoDataFrame, derived: gpd.GeoDataFrame, table: pd.DataFrame
    ) -> pd.DataFrame:
        del seed, derived, table
        pytest.fail("Preparation invoked a metric")

    plugins = definitions([explicit, onward])
    plugins["consumer"] = PluginDefinition(metrics=[consumer])
    catalog = compile_live_catalog(plugins)
    assert catalog.dependency_routes[
        "consumer", "inputs.table", "places"
    ].operations == ("onward",)
    assert Discovery(catalog).inspect("consumer").metric.accepted_selections == {
        "seed": ["tracts"]
    }
    request = {
        "metric": "consumer",
        "inputs": {"seed": {"provider": "tracts", "ids": []}},
    }
    assert prepare_execution(catalog, request).inputs[0].name == "seed"
    request["inputs"]["derived"] = {"provider": "regions", "ids": []}
    with pytest.raises(ValueError, match="Expected inputs"):
        prepare_execution(catalog, request)


@pytest.mark.parametrize("required", [False, True])
def test_canonical_parameters_must_be_satisfiable(*, required: bool) -> None:
    class Parameters(MetricParameters):
        count: int = (
            Field(ge=1, description="Count.")
            if required
            else Field(0, ge=1, description="Count.")
        )

    @location_operation(
        name="invalid",
        code_revision="1",
        description="Invalid parameters.",
        inputs={"source": LocationDependency(provider="tracts")},
        target_provider="counties",
        canonical=True,
    )
    def invalid(source: gpd.GeoDataFrame, *, parameters: Parameters) -> pd.DataFrame:
        del source, parameters
        pytest.fail("Preparation invoked an operation")

    if required:
        with pytest.raises(ValueError, match="unsatisfied parameters"):
            compile_live_catalog(definitions([invalid]))
        return
    catalog = compile_live_catalog(definitions([invalid]))
    for provider_id in ("counties", "tracts"):
        request = {
            "metric": "score",
            "inputs": {"places": {"provider": provider_id, "ids": []}},
        }
        if provider_id == "tracts":
            with pytest.raises(ValueError, match="greater than or equal"):
                prepare_execution(catalog, request)
        else:
            assert prepare_execution(catalog, request).node == "score"


def test_python_omission_and_private_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    @metric(
        name="private_score",
        code_revision="1",
        description="Private calculation.",
        private=True,
        outputs={"value": pa.DataFrameSchema(strict=True)},
    )
    def private_score(*, parameters: Options) -> pd.DataFrame:
        del parameters
        pytest.fail("Preparation invoked a metric")

    runner = LocalRunner.from_definitions(
        {"private": PluginDefinition(metrics=[private_score])}
    )
    execute = Mock()
    monkeypatch.setattr(runner, "_execute", execute)
    for options in (
        {},
        {"inputs": None, "parameters": None},
        {"inputs": {}, "parameters": {}},
    ):
        runner.run_development(
            "private_score",
            inputs=options.get("inputs"),
            parameters=options.get("parameters"),
        )
        prepared = execute.call_args.args[0]
        assert prepared.request.parameters == {"weights": [1]}
        assert prepared.inputs == ()
    with pytest.raises(ValueError, match="Unknown public metric"):
        runner.run("private_score")
    assert execute.call_count == 3
