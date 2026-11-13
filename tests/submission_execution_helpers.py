"""Live native graphs with shared in-process resources for transport tests."""

from typing import Annotated

import geopandas as gpd
import httpx
import pandas as pd
from pydantic import Field
from shapely.geometry import Point
from typing_extensions import override

from lyra.sdk import (
    Dependency,
    Input,
    LocationDependency,
    LocationProvider,
    PluginDefinition,
    location_operation,
    metric,
)
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.resources import BaseIO, Resource
from lyra.server.main import create_app
from lyra.server.mcp.tools import InProcessLyraBackend
from lyra.server.runtime import ApplicationRuntime
from test_support.mcp import call_tool
from tests.config_helpers import api_credentials
from tests.metric_execution_helpers import schema
from tests.provider_execution_helpers import RECORDS, RESULT


async def submit(runtime: ApplicationRuntime, transport: str, payload: dict) -> bool:
    if transport == "mcp":
        result = await call_tool(
            InProcessLyraBackend(lambda: runtime), "lyra_run_metric", payload
        )
        return not result.get("isError", False)
    app = create_app(runtime.config, credentials=api_credentials())
    app.state.runtime = runtime
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/jobs", json=payload, headers={"Authorization": "Bearer agent-secret"}
        )
        return response.status_code == 202


class Parameters(MetricParameters):
    weights: list[int] = Field(default_factory=lambda: [3], description="Weights")
    optional: str | None = Field(default=None, description="Optional label")


class Memory(BaseIO):
    open: bool = False


class Unused(BaseIO):
    @override
    def setup(self) -> None:
        message = "Unused resources must remain uninitialized"
        raise AssertionError(message)


def resources(
    events: list,
) -> tuple[type[BaseIO], type[LocationProvider], type[LocationProvider]]:
    class TracedMemory(Memory):
        @override
        def setup(self) -> None:
            self.open = True
            events.append(("open", self))

        @override
        def close(self) -> None:
            events.append(("close", self))
            self.open = False

    class Source(LocationProvider):
        id = "source"
        code_revision = "1"
        schema = RECORDS
        memory: Annotated[Memory, Resource("memory")]

        @override
        def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
            assert self.memory.open
            events.append((self.id, self.memory))
            return gpd.GeoDataFrame(
                {"location_id": ids, "value": [1] * len(ids)},
                geometry=[Point(0, 0)] * len(ids),
                crs=4326,
            )

        @override
        def close(self) -> None:
            assert self.memory.open
            events.append((f"close:{self.id}", self.memory))

    class Target(Source):
        id = "target"

    return TracedMemory, Source, Target


def definitions(events: list, *, fail: bool = False) -> dict[str, PluginDefinition]:
    memory_type, source_type, target_type = resources(events)

    @location_operation(
        name="convert",
        description="Convert",
        code_revision="1",
        inputs={"source": LocationDependency(provider="source")},
        target_provider="target",
        canonical=True,
    )
    def convert(source: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        return {key: (key,) for key in source.location_id}

    @location_operation(
        name="identity",
        description="Preserve explicitly selected targets",
        code_revision="1",
        inputs={"source": LocationDependency(provider="target")},
        target_provider="target",
    )
    def identity(renamed: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        return {key: (key,) for key in renamed.location_id}

    @metric(
        name="measure",
        description="Measure",
        code_revision="1",
        cache="off",
        inputs={"places": LocationDependency(provider="target", primary=True)},
        outputs={"rows": schema()},
    )
    def measure(
        places: gpd.GeoDataFrame,
        parameters: Parameters,
        memory: Annotated[Memory, Resource("memory")],
    ) -> pd.DataFrame:
        assert memory.open
        events.append(("measure", memory))
        value = parameters.weights[0]
        parameters.weights.append(999)
        if fail:
            message = "measurement failed"
            raise ValueError(message)
        return pd.DataFrame(
            {
                "location_id": places.location_id,
                "row_key": "one",
                "value": value,
            }
        )

    @metric(
        name="summary",
        description="Summary",
        private=True,
        code_revision="1",
        inputs={
            "places": LocationDependency(provider="target"),
            "rows": Dependency("rows", producer="measure"),
        },
        outputs={"total": RESULT},
    )
    def summary(places: gpd.GeoDataFrame, rows: pd.DataFrame) -> pd.DataFrame:
        assert len(rows) == len(places)
        return pd.DataFrame({"total": [len(rows)]})

    @metric(
        name="hybrid",
        description="Hybrid",
        code_revision="1",
        cache="off",
        inputs={
            "places": LocationDependency(provider="target", primary=True),
            "rows": Dependency("rows", producer="measure"),
            "total": Dependency("total", producer="summary"),
        },
        outputs={"rows": schema(), "flags": schema()},
    )
    def hybrid(
        places: gpd.GeoDataFrame,
        rows: pd.DataFrame,
        total: pd.DataFrame,
        parameters: Parameters,
    ) -> dict[str, pd.DataFrame]:
        assert parameters.weights == [3]
        assert total.total.tolist() == [len(places)]
        return {"rows": rows, "flags": rows.assign(value=len(places))}

    @metric(
        name="derived",
        description="Derived",
        code_revision="1",
        cache="off",
        inputs={
            "source": LocationDependency(provider="source"),
            "places": LocationDependency(operation="convert", primary=True).bind(
                source=Input("source")
            ),
            "rows": Dependency("rows", producer="measure").bind(places=Input("places")),
        },
        outputs={"rows": schema()},
    )
    def derived(
        source: gpd.GeoDataFrame,
        places: gpd.GeoDataFrame,
        rows: pd.DataFrame,
        parameters: Parameters,
    ) -> pd.DataFrame:
        assert parameters.weights == [3]
        assert len(source) == len(places)
        return rows

    @metric(
        name="receiving",
        description="Summarize measurements routed at a dependency input",
        code_revision="1",
        cache="off",
        inputs={
            "places": LocationDependency(provider="source"),
            "rows": Dependency("rows", producer="measure"),
        },
        outputs={"total": RESULT},
    )
    def receiving(
        places: gpd.GeoDataFrame, rows: pd.DataFrame, parameters: Parameters
    ) -> pd.DataFrame:
        assert len(rows) == len(places)
        assert parameters.weights == [3]
        return pd.DataFrame({"total": [int(rows.value.sum())]})

    @metric(
        name="receiving_primary",
        description="Own targets routed into an explicit operation source",
        code_revision="1",
        cache="off",
        inputs={
            "source": LocationDependency(provider="source"),
            "places": LocationDependency(operation="identity", primary=True).bind(
                source=Input("source")
            ),
            "rows": Dependency("rows", producer="measure").bind(places=Input("places")),
        },
        outputs={"rows": schema()},
    )
    def receiving_primary(
        source: gpd.GeoDataFrame,
        places: gpd.GeoDataFrame,
        rows: pd.DataFrame,
        parameters: Parameters,
    ) -> pd.DataFrame:
        return derived(source, places, rows, parameters)

    return {
        "calculations": PluginDefinition(
            metrics=[measure, summary, hybrid, derived, receiving, receiving_primary],
            location_operations=[convert, identity],
        ),
        "providers": PluginDefinition(
            resources={"source": source_type, "target": target_type}
        ),
        "resources": PluginDefinition(
            resources={"memory": memory_type, "unused": Unused}
        ),
    }
