import pandas as pd
from lyra.sdk import Unit
from lyra.sdk.models.plugin import TableColumn, TableOutput
from lyra.sdk.results import normalize_native_result, serialize_outputs


def test_native_dataframe_accepts_pandas_and_preserves_column_types() -> None:
    frame = pd.DataFrame({"integer": [1], "number": [0.5]}, index=["area"])
    output = TableOutput(
        columns=[
            TableColumn(
                name="integer", type="integer", unit=Unit.COUNT, description="Count."
            ),
            TableColumn(
                name="number", type="number", unit=Unit.RATIO, description="Ratio."
            ),
        ]
    )
    native = normalize_native_result("typed_frame", {"values": output}, frame)
    assert native["values"] is frame
    result = serialize_outputs("typed_frame", native)["values"]
    assert result == [{"integer": 1, "number": 0.5}]
    assert isinstance(result, list)
    row = result[0]
    assert isinstance(row, dict)
    assert type(row["integer"]) is int
    assert type(row["number"]) is float
