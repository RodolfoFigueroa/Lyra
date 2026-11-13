"""Every native receiving boundary validates independent working data."""

import pandas as pd
import pandera.pandas as pa
import pytest

from lyra.contracts.errors import MetricResultError
from test_support.location_schema import column, contract, declaration, locations


def test_current_checks_execute_on_every_validation() -> None:
    source = locations({"count": 1})
    calls = []

    def check(series: pd.Series) -> bool:
        calls.append(len(series))
        return len(calls) == 1

    current = contract(declaration(count=column(int, checks=pa.Check(check))))
    result = current.prepare_frame(source)
    pd.testing.assert_frame_equal(result, source)
    with pytest.raises(MetricResultError):
        current.prepare_frame(source)
    assert calls == [1, 1]


def test_coercion_is_owned_and_receiving_schema_checked() -> None:
    source = locations({"count": "2"})
    result = contract(declaration(count=column(int, coerce=True))).prepare_frame(source)
    assert result["count"].tolist() == [2]
    assert source["count"].tolist() == ["2"]
    result.loc[0, "count"] = 99
    assert source["count"].tolist() == ["2"]
    with pytest.raises(ValueError, match="columns"):
        contract(declaration(other=column(int))).prepare_frame(result)
