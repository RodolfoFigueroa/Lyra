"""Registration and native producer invariants for fixed table schemas."""

from typing import Literal

import pandas as pd
import pandera.pandas as pa
import pytest
from pandera.api.pandas.model_config import BaseConfig
from pandera.typing import Series

from lyra.contracts.errors import MetricResultError
from lyra.sdk.output_schema import compile_output
from lyra.sdk.provider_schema import ProviderSchema
from lyra.sdk.results import normalize_native_result
from lyra.sdk.storage_schema import empty_frame
from test_support.location_schema import column
from tests.provider_schema_helpers import declaration
from tests.provider_schema_helpers import frame as provider_frame


class StrictConfig(BaseConfig):
    """Strictness inherited by author models."""

    strict = True


class BaseTable(pa.DataFrameModel):
    Config: type[BaseConfig] = StrictConfig
    value: Series[pd.Int64Dtype] = pa.Field(
        description="Value", metadata={"unit": None}, nullable=True
    )


class InheritedTable(BaseTable):
    pass


def test_inherited_configuration_and_independent_empty_frames() -> None:
    schema, metadata = compile_output(InheritedTable)
    first = empty_frame(schema)
    assert first.value.dtype == pd.Int64Dtype()
    assert list(first) == ["value"]
    first.loc[0] = 3
    assert empty_frame(schema).empty
    schema.columns["value"].description = "Changed"
    assert metadata.columns[0].description == "Value"


@pytest.mark.parametrize("strict", [False, "filter"])
def test_effective_model_strictness_is_required(
    *, strict: bool | Literal["filter"]
) -> None:
    class Config(BaseConfig):
        """An unsupported effective declaration."""

    Config.strict = strict

    class Parent(pa.DataFrameModel):
        value: Series[int] = pa.Field(description="Value", metadata={"unit": None})

    Parent.Config = Config

    class Child(Parent):
        pass

    with pytest.raises(ValueError, match="strict=True"):
        compile_output(Child)


@pytest.mark.parametrize("value", [2**63, -(2**63) - 1, str(2**63)])
@pytest.mark.parametrize("coerce", [False, True])
def test_integer_overflow_cannot_be_hidden_by_coercion(
    value: object, *, coerce: bool
) -> None:
    schema = pa.DataFrameSchema({"value": column("Int64", coerce=coerce)}, strict=True)
    with pytest.raises(MetricResultError, match="64-bit"):
        normalize_native_result(
            "producer", {"data": schema}, pd.DataFrame({"value": [value]})
        )


@pytest.mark.parametrize("dtype", ["Int64", "Float64", "boolean", "string"])
def test_all_null_and_empty_columns_keep_types(dtype: str) -> None:
    schema = pa.DataFrameSchema({"value": column(dtype, nullable=True)}, strict=True)
    for frame in [
        empty_frame(schema),
        pd.DataFrame({"value": pd.Series([None], dtype=dtype)}),
    ]:
        result = normalize_native_result("producer", {"data": schema}, frame)["data"]
        pd.testing.assert_frame_equal(result, frame)


def test_registration_retains_independent_storage_layout() -> None:
    author_schema = declaration()
    compiled = ProviderSchema(author_schema)
    storage = compiled.storage
    author_schema.columns.clear()
    assert storage.columns == tuple(compiled.schema.columns)
    layout = dict(zip(storage.columns, storage.sqlite_types, strict=True))
    assert layout["location_id"] == "TEXT"
    assert layout["count"] == "INTEGER"
    assert layout["geometry"] == "BLOB"
    assert storage.crs == "EPSG:4326"
    compiled.validate(provider_frame())
    assert compiled.storage is storage
    empty = compiled.empty_frame()
    assert empty.crs == storage.crs
    assert empty["count"].dtype == pd.Int64Dtype()
