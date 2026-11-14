"""Small parameterized plugin for retained-execution lifecycle checks."""

from typing import Literal

import pandas as pd
import pandera.pandas as pa
from pydantic import Field

from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition, metric


class Options(MetricParameters):
    items: list[int] = Field(default_factory=lambda: [1], description="Items")


def definition(
    calls: list,
    *,
    cache: Literal["full", "off"] = "full",
    private: bool = False,
    empty: bool = False,
) -> PluginDefinition:
    @metric(
        name="root",
        description="Result",
        cache=cache,
        private=private,
        outputs={
            "answer": pa.DataFrameSchema(
                {"value": pa.Column(int, description="Value", metadata={"unit": None})},
                strict=True,
            )
        },
    )
    def root(parameters: Options) -> pd.DataFrame:
        calls.append(parameters.items[:])
        return pd.DataFrame({"value": pd.Series([] if empty else [1], dtype="int64")})

    return PluginDefinition(metrics=[root])
