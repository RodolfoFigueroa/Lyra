import pandas as pd
import pytest

from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.preparation import PreparationError, prepare_execution
from tests.provider_execution_helpers import provider_execution_definitions


@pytest.mark.parametrize("ids", [[], ["a", "a", "b"]])
def test_preparation_captures_order_and_duplicates_without_acquisition(
    ids: list[str],
) -> None:
    events: list[str] = []
    runner = LocalRunner.from_definitions(provider_execution_definitions(events))
    expected = tuple(ids)
    plan = prepare_execution(
        runner.catalog,
        {"metric": "count", "inputs": {"places": {"provider": "target", "ids": ids}}},
    )
    ids.append("later")
    assert plan.inputs[0].ids == expected
    assert plan.inputs[0].binding.source.collection == "target"
    assert events == []


@pytest.mark.parametrize(
    "inputs",
    [
        {},
        {"places": {"provider": "unknown", "ids": ["a"]}},
        {"places": {"provider": "target", "ids": [1]}},
        {"places": {"provider": "target", "ids": [True]}},
        {"places": {"provider": "target", "ids": [None]}},
        {"places": {"provider": "target", "ids": ("a",)}},
        {"places": {"provider": 1, "ids": []}},
        {"places": {"result": "previous"}},
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
