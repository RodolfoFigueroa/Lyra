"""Temporary authoring projects with live entry-point factories."""

from pathlib import Path


def write_project(project: Path) -> None:
    (project / "pyproject.toml").write_text(
        """
[project]
name = "example-plugin"
version = "1.2.3"

[project.entry-points."lyra.plugins"]
plugin = "example_plugin:create_plugin"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (project / "example_plugin.py").write_text(
        """from pydantic import Field
from lyra.sdk.parameters import MetricParameters
from lyra.data.locations import Locations
from lyra.sdk.plugin import PluginDefinition, metric
from lyra.contracts.units import Unit
import pandera.pandas as pa

from lyra.contracts.locations import LocationContract, LocationFamily
from lyra.sdk.location_schema import LocationOutputSchema

LOCATION_CONTRACT = LocationContract(
    family=LocationOutputSchema(
            pa.DataFrameSchema(
                {
                    "id": pa.Column(
                        str, description="Unit ID", metadata={"unit": None}
                    ),
                    "geometry": pa.Column(
                        "geometry", description="Unit geometry", metadata={"unit": None}
                    ),
                }
            )
        ).family("test:units", "lot"),
    source="fixture",
    geographical_edition="current",
    meaning="Provider locations",
)


class Parameters(MetricParameters):
    value: int = Field(default=2, description="Example input value.")


@metric(code_revision="1",
    locations={"location": LOCATION_CONTRACT},
    name="example",
    description="Example metric.",
    outputs={
        "example": pa.DataFrameSchema(
            {
                "value": pa.Column(
                    int, description="Example value.", metadata={"unit": Unit.COUNT}
                )
            }
        )
    },
)
def calculate(location: Locations, parameters: Parameters):
    raise AssertionError


def create_plugin() -> PluginDefinition:
    return PluginDefinition(metrics=[calculate])
""".lstrip(),
        encoding="utf-8",
    )
