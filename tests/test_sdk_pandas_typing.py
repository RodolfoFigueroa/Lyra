import pandas as pd
import pandera.pandas as pa
from lyra.sdk import Unit
from lyra.sdk.results import normalize_native_result, serialize_outputs


def test_native_dataframe_accepts_pandas_and_preserves_column_types() -> None:
    frame = pd.DataFrame({"integer": [1], "number": [0.5]}, index=["area"])
    output = pa.DataFrameSchema(
        {
            "integer": pa.Column(
                "int64", description="Count.", metadata={"unit": Unit.COUNT}
            ),
            "number": pa.Column(
                "float64", description="Ratio.", metadata={"unit": Unit.RATIO}
            ),
        }
    )
    native = normalize_native_result("typed_frame", {"values": output}, frame)
    pd.testing.assert_frame_equal(native["values"], frame.reset_index(drop=True))
    result = serialize_outputs("typed_frame", native)["values"]
    assert result == [{"integer": 1, "number": 0.5}]
    assert isinstance(result, list)
    row = result[0]
    assert isinstance(row, dict)
    assert type(row["integer"]) is int
    assert type(row["number"]) is float
