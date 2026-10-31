"""Validate reusable contracts without requiring future storage behavior."""

from pathlib import Path

import pandas as pd
import pandera.errors
import pytest

from tests.storage_data import declarations, frame, named_outputs
from tests.storage_roundtrip import assert_equal, fidelity


@pytest.mark.parametrize("spatial", [False, True])
@pytest.mark.parametrize(
    "state", ["populated", "empty", "all-null", "shuffled", "indexed"]
)
def test_declared_inputs(state: str, *, spatial: bool) -> None:
    value = frame(state, spatial=spatial)
    declarations(spatial=spatial).validate(value)
    if state == "all-null":
        assert value.drop(columns="geometry", errors="ignore").isna().all().all()


def test_invalid_dtype() -> None:
    value = frame()
    value["small"] = value["small"].astype("Float64")
    with pytest.raises(pandera.errors.SchemaError):
        declarations().validate(value)


def test_named_outputs() -> None:
    for name, value in named_outputs().items():
        declarations(spatial=name == "places").validate(value)


def test_storage_boundary_fidelity(tmp_path: Path) -> None:

    observations = fidelity(tmp_path / "fidelity")
    assert len(observations) == 15
    assert all("error" in observation for observation in observations)
    assert all(
        observation["error"] is None
        for observation in observations
        if observation["state"] != "empty"
    )
    supported = fidelity(tmp_path / "supported", categories=False)
    assert len(supported) == 15
    assert all(observation["error"] is None for observation in supported)


@pytest.mark.parametrize("state", ["populated", "empty", "all-null"])
def test_fidelity_accepts_declared_values(state: str) -> None:
    expected = frame(state)
    assert_equal(expected.copy(deep=True), expected)


@pytest.mark.parametrize("column", ["single", "double"])
def test_fidelity_rejects_changed_zero_sign(column: str) -> None:
    expected = frame()
    actual = expected.copy(deep=True)
    actual.loc[0, column] = 0.0
    with pytest.raises(AssertionError, match="Signed zero changed"):
        assert_equal(actual, expected)


def test_fidelity_rejects_changed_dtype() -> None:
    expected = frame()
    actual = expected.copy(deep=True)
    actual["small"] = actual["small"].astype(pd.Int64Dtype())
    with pytest.raises(AssertionError):
        assert_equal(actual, expected)
