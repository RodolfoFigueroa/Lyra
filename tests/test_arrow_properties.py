"""Lossless Arrow identity and scalar behavior."""

from typing import Any, cast

import pyarrow as pa
import pytest
from lyra.sdk.property_buffers import decode, encode

SCOPE: dict[str, Any] = {
    "collection": "test:units",
    "unit_type": "unit",
    "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
}


def test_round_trip() -> None:
    values = [True, 1, 1.0, "1", None, 2**100, -(2**100), "雪", "", -0.0]
    rows = [{"mixed": value, "null": None} for value in values] + [{}]
    table = encode(map(str, range(len(rows))), rows, **SCOPE)
    for source in (table, pa.concat_tables([table.slice(0, 3), table.slice(3)])):
        result = decode(source)
        assert result == {
            **SCOPE,
            "ids": list(map(str, range(len(rows)))),
            "properties": rows,
        }
        for actual, expected in zip(result["properties"][:-1], values, strict=True):
            assert type(actual["mixed"]) is type(expected)
        result["properties"][0]["mixed"] = "changed"
        assert decode(source)["properties"] == rows
    assert decode(table.slice(2, 2))["properties"] == rows[2:4]
    assert decode(encode(["a"], [{"b": 1, "a": 2}], **SCOPE)) == decode(
        encode(["a"], [{"a": 2, "b": 1}], **SCOPE)
    )
    assert decode(encode([], [], **SCOPE))["properties"] == []


@pytest.mark.parametrize("value", [[], {}, float("nan"), float("inf"), float("-inf")])
def test_invalid_scalar(value: object) -> None:
    with pytest.raises(ValueError, match=r".+"):
        encode(["a"], [{"x": cast("Any", value)}], **SCOPE)


@pytest.mark.parametrize("ids", [[""], [1], ["a", "a"]])
def test_invalid_ids(ids: list[str]) -> None:
    with pytest.raises(ValueError, match=r".+"):
        encode(ids, [{}] * len(ids), **SCOPE)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("collection", "bad"),
        ("unit_type", "bad unit"),
        ("crs", {"type": "name", "properties": {"name": "bad-crs"}}),
    ],
)
def test_invalid_metadata(field: str, value: object) -> None:
    with pytest.raises(ValueError, match=r".+"):
        encode([], [], **{**SCOPE, field: cast("Any", value)})


def test_invalid_buffers() -> None:
    table = encode(["a"], [{"x": 1}], **SCOPE)
    with pytest.raises(ValueError, match=r".+"):
        decode(table.replace_schema_metadata({}))
    with pytest.raises(ValueError, match=r".+"):
        decode(table.set_column(0, "id", pa.array([""], type=pa.large_string())))
    cells = table.column(1).to_pylist()
    cells[0]["tag"] = 8
    with pytest.raises(ValueError, match=r".+"):
        decode(
            table.set_column(
                1, "property_0", pa.array(cells, type=table.column(1).type)
            )
        )


def test_unbounded_integer_round_trip() -> None:
    rows = [{"value": value} for value in (2**16000, -(2**16000))]
    assert decode(encode(["a", "b"], rows, **SCOPE))["properties"] == rows
