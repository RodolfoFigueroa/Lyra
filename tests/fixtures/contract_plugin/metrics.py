from pathlib import Path
from typing import Self

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
from pydantic import BaseModel, ConfigDict, Field, model_validator

from lyra.contracts.units import Unit
from lyra.sdk import LocationDependency, MetricParameters, RunContext, metric
from test_support.location_resolution import Provider

from .calculation import calculate_capacity


class CapacityParameters(MetricParameters):
    units: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Capacity assigned to each selected feature.",
        examples=[5, 10],
    )


CAPACITY_OUTPUT = pa.DataFrameSchema(
    {
        "capacity": pa.Column(
            "int64",
            description="Capacity of the selected feature.",
            metadata={"unit": Unit.COUNT},
        )
    },
    strict=True,
)


@metric(
    code_revision="1",
    inputs={"location": LocationDependency(provider=Provider.id)},
    name="capacity",
    description="Assign a capacity to each selected feature.",
    outputs={"capacity": CAPACITY_OUTPUT},
)
def capacity(
    parameters: CapacityParameters, location: gpd.GeoDataFrame
) -> pd.DataFrame:
    return calculate_capacity(
        location.location_id.tolist(),
        units_per_feature=parameters.units,
    )


class ExistingSettings(BaseModel):
    units: int = Field(default=4, ge=1, description="Units per selected feature.")


class CompatibleSettings(ExistingSettings):
    model_config = ConfigDict(extra="forbid", validate_default=True)


@metric(
    code_revision="1",
    inputs={"location": LocationDependency(provider=Provider.id)},
    name="reused_capacity",
    description="Use parameters adapted from an existing library.",
    outputs={"reused_capacity": CAPACITY_OUTPUT},
)
def reused_capacity(
    parameters: CompatibleSettings, location: gpd.GeoDataFrame
) -> pd.DataFrame:
    return calculate_capacity(
        location.location_id.tolist(),
        units_per_feature=parameters.units,
    )


class Selection(MetricParameters):
    activity_codes: list[str] = Field(
        min_length=1, description="Activity codes included in the selection."
    )


class SelectionParameters(MetricParameters):
    selection: Selection = Field(description="Activity selection settings.")
    threshold: int | None = Field(
        ge=0, description="Required threshold; null disables the threshold."
    )
    label: str | None = Field(default=None, description="Optional descriptive label.")


@metric(
    code_revision="1",
    inputs={"location": LocationDependency(provider=Provider.id)},
    name="selection_size",
    description="Count selected activity codes above an optional threshold.",
    outputs={
        "selection_size": pa.DataFrameSchema(
            {
                "selected_count": pa.Column(
                    "int64",
                    description="Number of selected codes passing the threshold.",
                    metadata={"unit": Unit.COUNT},
                )
            },
            strict=True,
        )
    },
)
def selection_size(
    parameters: SelectionParameters, location: gpd.GeoDataFrame
) -> pd.DataFrame:
    count = len(parameters.selection.activity_codes)
    if parameters.threshold is not None and count < parameters.threshold:
        count = 0
    return pd.DataFrame(
        {"selected_count": [count] * len(location)},
        index=location.location_id.tolist(),
    )


@metric(
    code_revision="1",
    inputs={
        "location": LocationDependency(provider=Provider.id),
        "bounds": LocationDependency(provider=Provider.id),
    },
    name="bounded_features",
    description="Describe the supplied bounds for each selected location.",
    outputs={
        "bounded_features": pa.DataFrameSchema(
            {
                "bounds_type": pa.Column(
                    "str",
                    description="Geometry type of the supplied bounding feature.",
                    metadata={"unit": None},
                )
            },
            strict=True,
        )
    },
)
def bounded_features(
    location: gpd.GeoDataFrame, bounds: gpd.GeoDataFrame
) -> pd.DataFrame:
    return pd.DataFrame(
        {"bounds_type": [bounds.geometry.iloc[0].geom_type] * len(location)},
        index=location.location_id.tolist(),
    )


def feature_report(location: gpd.GeoDataFrame, context: RunContext) -> Path:
    context.logger.info("Writing %d feature identifiers", len(location))
    destination = context.temp_dir / "features.txt"
    destination.write_text(
        "".join(f"{feature.location_id}\n" for feature in location.itertuples()),
        encoding="utf-8",
    )
    return destination


class IntervalParameters(MetricParameters):
    lower: int = Field(ge=0, description="Lower interval boundary.")
    upper: int = Field(ge=0, description="Upper interval boundary.")

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.lower > self.upper:
            message = "lower must not exceed upper"
            raise ValueError(message)
        return self


@metric(
    code_revision="1",
    inputs={"location": LocationDependency(provider=Provider.id)},
    name="interval_width",
    description="Return the width of a valid interval for each location.",
    outputs={
        "interval_width": pa.DataFrameSchema(
            {
                "width": pa.Column(
                    "int64",
                    description="Difference between the upper and lower boundary.",
                    metadata={"unit": Unit.DIMENSIONLESS},
                )
            },
            strict=True,
        )
    },
)
def interval_width(
    parameters: IntervalParameters, location: gpd.GeoDataFrame
) -> pd.DataFrame:
    return pd.DataFrame(
        {"width": [parameters.upper - parameters.lower] * len(location)},
        index=location.location_id.tolist(),
    )
