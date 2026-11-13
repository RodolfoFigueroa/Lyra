from dataclasses import replace
from unittest.mock import Mock, create_autospec

import geopandas as gpd
import pandas as pd
import pytest
from pydantic import Field

from lyra.contracts.dependencies import Dependency, Input, LocationDependency
from lyra.contracts.origins import Origin
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.node_declarations import NodeDeclaration
from lyra.runtime.planning.preparation import PreparationError
from lyra.runtime.planning.provider_preparation import prepare_provider_request
from lyra.sdk.parameters import MetricParameters
from tests.node_declaration_helpers import (
    CENSUS,
    ELEVATION,
    Tracts,
    declarations,
    plugin,
    provider,
)
from tests.provider_execution_helpers import provider_execution_definitions


@pytest.fixture
def forbidden_provider(monkeypatch: pytest.MonkeyPatch) -> type[Tracts]:
    declared = provider(code_revision="v1")
    forbidden = Mock(side_effect=AssertionError("Acquisition during preparation"))
    for method in ("__init__", "setup", "resolve"):
        monkeypatch.setattr(
            declared,
            method,
            create_autospec(declared.resolve, side_effect=forbidden)
            if method == "resolve"
            else forbidden,
        )
    return declared


def test_empty_and_repeated_selections_capture_scope(
    forbidden_provider: type[Tracts],
) -> None:
    definitions = {"p": plugin(alias=forbidden_provider)}
    for ids, expected in [
        ([], frozenset()),
        (["001", "001", "002"], frozenset({"001", "002"})),
    ]:
        result = prepare_provider_request(
            definitions,
            declarations(),
            {
                "metric": "consumer",
                "inputs": {"tracts": {"provider": "tracts", "ids": ids}},
                "parameters": {"year": 2025},
            },
        )
        assert result.inputs[0].ids == expected
        scope = result.inputs[0].provider.scope
        assert scope.provider_id == "tracts"
        assert scope.code_revision == "v1"
        assert scope.origins == frozenset({CENSUS})
        assert result.uses[0].origins == frozenset({CENSUS, ELEVATION})
        assert {use.node: use.origins for use in result.uses} == {
            "consumer": frozenset({CENSUS, ELEVATION}),
            "population": frozenset({CENSUS}),
            "select": frozenset(),
        }
        assert next(use for use in result.uses if use.node == "select").parameters == {
            "year": 2025
        }
        assert set(result.providers) == {"tracts"}


@pytest.mark.parametrize(
    "selection",
    [
        {"provider": "tracts", "ids": [1]},
        {"provider": "tracts", "ids": [True]},
        {"provider": "tracts", "ids": [""]},
        {"provider": "tracts", "ids": [None]},
        {"provider": "tracts", "ids": ("001",)},
        {"provider": "tracts", "ids": "001"},
        {"provider": 1, "ids": []},
        {"provider": "unknown", "ids": []},
        {"provider": "counties", "ids": []},
        {"result": "previous"},
        gpd.GeoDataFrame(),
    ],
)
def test_invalid_selections_are_acquisition_free(
    forbidden_provider: type[Tracts], selection: object
) -> None:
    with pytest.raises((ValueError, TypeError)):
        prepare_provider_request(
            {"p": plugin(alias=forbidden_provider, counties=provider(id="counties"))},
            declarations()[:1],
            {"metric": "population", "inputs": {"tracts": selection}},
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"metric": "population"},
        {"metric": "population", "inputs": None},
        {"metric": "population", "parameters": None},
        {"metric": "missing"},
        {"metric": "population", "extra": True},
        {"metric": "population", "inputs": {"typo": {"provider": "tracts", "ids": []}}},
        {
            "metric": "consumer",
            "inputs": {
                "tracts": {"provider": "tracts", "ids": []},
                "table": {"result": "previous"},
            },
            "parameters": {"year": 2025},
        },
        {
            "metric": "consumer",
            "inputs": {"tracts": {"provider": "tracts", "ids": []}},
            "parameters": {"year": "bad"},
        },
        {"metric": "consumer", "inputs": {"tracts": {"provider": "tracts", "ids": []}}},
    ],
)
def test_invalid_requests(forbidden_provider: type[Tracts], payload: dict) -> None:
    with pytest.raises((ValueError, TypeError)):
        prepare_provider_request(
            {"p": plugin(alias=forbidden_provider)}, declarations(), payload
        )


class Options(MetricParameters):
    year: int = Field(2025, description="Year.")
    tags: list[str] = Field(default_factory=list, description="Tags.")
    label: str | None = Field(None, description="Label.")


def direct(*, parameters: Options) -> None:
    pass


def test_direct_origins_without_locations_and_parameter_defaults() -> None:
    # Preparation has no cache dependency; direct origins survive regardless of reuse.
    node = NodeDeclaration("direct", direct, origins=(ELEVATION, CENSUS))
    result = prepare_provider_request({}, [node], {"metric": "direct"})
    assert result.inputs == ()
    assert result.providers == {}
    assert result.uses[0].origins == frozenset({CENSUS, ELEVATION})
    assert result.uses[0].parameters == {"year": 2025, "tags": [], "label": None}


def test_mutations_and_origin_descriptions_do_not_change_capture(
    forbidden_provider: type[Tracts],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = ["001"]
    tags = ["selected"]
    origins = [CENSUS, ELEVATION]
    monkeypatch.setattr(forbidden_provider, "origins", origins)
    nodes = [NodeDeclaration("direct", direct, origins=origins)]
    request = {"metric": "direct", "parameters": {"tags": tags}}
    prepared = prepare_provider_request({}, nodes, request)
    selected = prepare_provider_request(
        {"p": plugin(alias=forbidden_provider)},
        declarations()[:1],
        {
            "metric": "population",
            "inputs": {"tracts": {"provider": "tracts", "ids": ids}},
        },
    )
    ids.append("002")
    tags.append("changed")
    origins.clear()
    monkeypatch.setattr(forbidden_provider, "code_revision", "v2")
    assert prepared.uses[0].parameters["tags"] == ["selected"]
    assert prepared.uses[0].origins == frozenset({CENSUS, ELEVATION})
    assert selected.inputs[0].ids == frozenset({"001"})
    assert selected.inputs[0].provider.scope.code_revision == "v1"
    monkeypatch.setattr(forbidden_provider, "code_revision", "v1")
    monkeypatch.setattr(
        forbidden_provider,
        "origins",
        (
            ELEVATION,
            Origin(id="census", revision="2025", description="Updated"),
            CENSUS,
        ),
    )
    other = prepare_provider_request(
        {"p": plugin(alias=forbidden_provider)},
        declarations()[:1],
        {
            "metric": "population",
            "inputs": {"tracts": {"provider": "tracts", "ids": ["001"]}},
        },
    )
    assert selected.inputs[0].provider.scope == other.inputs[0].provider.scope


def derived(seed: gpd.GeoDataFrame, result: gpd.GeoDataFrame) -> None:
    pass


def test_derived_input_and_nested_parameters(forbidden_provider: type[Tracts]) -> None:
    nodes = [
        *declarations()[:2],
        NodeDeclaration(
            "derived",
            derived,
            inputs={
                "seed": LocationDependency(provider="tracts"),
                "result": LocationDependency(
                    operation="select", parameters={"year": 2025}
                ).bind(
                    region=LocationDependency(
                        operation="select", parameters={"year": 2024}
                    ).bind(region=Input("seed"))
                ),
            },
        ),
    ]
    inputs = {"seed": {"provider": "tracts", "ids": []}}
    result = prepare_provider_request(
        {"p": plugin(alias=forbidden_provider)},
        nodes,
        {"metric": "derived", "inputs": inputs},
    )
    assert [use.parameters for use in result.uses] == [
        {},
        {"year": 2025},
        {"year": 2024},
    ]
    inputs["result"] = {"provider": "tracts", "ids": []}
    with pytest.raises(ValueError, match="Expected inputs"):
        prepare_provider_request(
            {"p": plugin(alias=forbidden_provider)},
            nodes,
            {"metric": "derived", "inputs": inputs},
        )


@pytest.mark.parametrize("value", ["bad", Input("parameters.missing")])
def test_dependency_parameter_errors(
    forbidden_provider: type[Tracts], value: object
) -> None:
    nodes = declarations()
    nodes[-1] = replace(
        nodes[-1],
        inputs={
            "tracts": LocationDependency(provider="tracts"),
            "table": Dependency("population", producer="population").bind(
                tracts=LocationDependency(
                    operation="select", parameters={"year": value}
                ).bind(region=Input("tracts"))
            ),
        },
    )
    with pytest.raises(ValueError, match=r"int_parsing|undeclared input reference"):
        prepare_provider_request(
            {"p": plugin(alias=forbidden_provider)},
            nodes,
            {
                "metric": "consumer",
                "inputs": {"tracts": {"provider": "tracts", "ids": []}},
                "parameters": {"year": 2025},
            },
        )


@pytest.mark.parametrize(
    "inputs",
    [
        {},
        {"places": {"provider": "unknown", "ids": ["a"]}},
        {"places": {"provider": "target", "ids": [1]}},
        {"places": {"provider": "target", "ids": [""]}},
        {"places": {"provider": "target", "ids": "a"}},
    ],
)
def test_invalid_request_never_acquires(inputs: dict) -> None:
    events: list[str] = []
    runner = LocalRunner.from_definitions(provider_execution_definitions(events))
    with pytest.raises(PreparationError):
        runner.run("count", inputs=inputs)
    assert events == []


def test_caller_supplied_derived_input_rejected_before_acquisition() -> None:
    events: list[str] = []
    runner = LocalRunner.from_definitions(provider_execution_definitions(events))
    with pytest.raises(PreparationError):
        runner.run(
            "derived",
            inputs={
                "source": {"provider": "source", "ids": []},
                "places": {"provider": "target", "ids": []},
            },
        )
    assert events == []


@pytest.mark.parametrize("development", [False, True])
@pytest.mark.parametrize(
    "replacement",
    [pd.DataFrame({"total": [999]}), {"result": "previous"}, {"total": [999]}],
)
def test_dependency_replacements_fail_before_acquisition(
    replacement: object, *, development: bool
) -> None:
    events: list[str] = []
    runner = LocalRunner.from_definitions(provider_execution_definitions(events))
    run = runner.run_development if development else runner.run
    with pytest.raises(PreparationError):
        run(
            "derived",
            inputs={
                "source": {"provider": "source", "ids": ["a"]},
                "data": replacement,
            },
        )
    assert events == []
