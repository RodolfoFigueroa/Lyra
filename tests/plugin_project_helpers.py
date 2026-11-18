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
import geopandas as gpd
from lyra.sdk.dependencies import LocationDependency
from tests.provider_helpers import FakeProvider
from lyra.sdk.plugin import PluginDefinition, metric
import pandera.pandas as pa



class Parameters(MetricParameters):
    value: int = Field(default=2, description="Example input value.")


@metric(code_revision="1",
    inputs={"location": LocationDependency(provider=FakeProvider.id)},
    name="example",
    description="Example metric.",
    outputs={
        "example": pa.DataFrameSchema(strict=True,
            columns={
                "value": pa.Column(
                    int, description="Example value.", metadata={"unit": "count"}
                )
            }
        )
    },
)
def calculate(location: gpd.GeoDataFrame, parameters: Parameters):
    raise AssertionError


def create_plugin() -> PluginDefinition:
    return PluginDefinition(metrics=[calculate], resources={"places": FakeProvider})
""".lstrip(),
        encoding="utf-8",
    )
