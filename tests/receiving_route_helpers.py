"""Executable receiving routes with independent parameters and in-process resources."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated

import geopandas as gpd
import pandas as pd
import pytest
from pydantic import Field
from shapely.geometry import Point
from typing_extensions import override

from lyra.contracts.metric_scope import CachePolicy
from lyra.sdk import (
    Dependency,
    Input,
    LocationDependency,
    LocationProvider,
    PluginDefinition,
)
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import location_operation, metric
from lyra.sdk.resources import Resource
from tests.metric_execution_helpers import schema
from tests.provider_execution_helpers import RECORDS, RESULT
from tests.submission_execution_helpers import Memory, Unused


class Options(MetricParameters):
    weights: list[int] = Field(default_factory=lambda: [2], description="Weights")


class Session(Memory):
    parent: Annotated[Memory, Resource("memory")]


class HandlerResource(Memory):
    shared: Annotated[Memory, Resource("memory")]


@dataclass
class Trace:
    events: list = field(default_factory=list)
    frames: dict[str, list[gpd.GeoDataFrame]] = field(default_factory=dict)
    parameters: list[tuple[str, list[int]]] = field(default_factory=list)

    def capture(self, name: str, frame: gpd.GeoDataFrame) -> None:
        self.frames.setdefault(name, []).append(frame.copy(deep=True))


@dataclass
class Routes:
    trace: Trace
    policy: CachePolicy
    failure: str | None
    options: type[MetricParameters]

    def operation(
        self, name: str, start: str, end: str, *, canonical: bool = True
    ) -> Callable:
        def select(renamed: gpd.GeoDataFrame, *, parameters: MetricParameters) -> dict:
            self.trace.capture(name, renamed)
            values = parameters.model_dump()["weights"]
            self.trace.parameters.append((name, list(values)))
            parameters.__dict__["weights"].append(999)
            ids = tuple(dict.fromkeys(renamed.location_id))
            renamed.loc[:, "location_id"] = "mutated"
            renamed.loc[:, "value"] = -99
            renamed.attrs["nested"] = {"mutated": True}
            renamed.set_crs(3857, allow_override=True, inplace=True)
            if name == "alternate":
                pytest.fail("Execution replaced the accepted route preference")
            if self.failure == "operation":
                message = "mapping failed"
                raise RuntimeError(message)
            if self.failure == "mapping":
                return {"mutated": ("Y",)}
            if self.failure == "omitted":
                return {}
            if self.failure == "empty":
                return dict.fromkeys(ids, ())
            if start == "source" and end == "middle":
                return {
                    key: {"a": ("x", "y"), "b": ("y", "z")}.get(key, ()) for key in ids
                }
            if start == "middle" and end == "target":
                return {key: (key.upper(),) for key in ids}
            return {key: (key.lower(),) for key in ids}

        select.__annotations__["parameters"] = self.options
        return location_operation(
            name=name,
            description="Map represented source IDs",
            code_revision="1",
            cache=self.policy,
            inputs={"source": LocationDependency(provider=start)},
            target_provider=end,
            canonical=canonical,
        )(select)


def runtime_resources(trace: Trace) -> dict[str, type[Memory]]:
    class TracedMemory(Memory):
        @override
        def setup(self) -> None:
            self.open = True
            trace.events.append(("open", self))

        @override
        def close(self) -> None:
            trace.events.append(("close", self))
            self.open = False

    class TracedSession(Session):
        @override
        def setup(self) -> None:
            assert self.parent.open
            self.open = True
            trace.events.append(("open:session", self.parent))

        @override
        def close(self) -> None:
            assert self.parent.open
            trace.events.append(("close:session", self.parent))
            self.open = False

    class Handler(HandlerResource):
        @override
        def setup(self) -> None:
            assert self.shared.open
            self.open = True
            trace.events.append(("open:handler", self.shared))

        @override
        def close(self) -> None:
            assert self.shared.open
            trace.events.append(("close:handler", self.shared))
            self.open = False

    return {"memory": TracedMemory, "session": TracedSession, "handler": Handler}


def providers(trace: Trace, failure: str | None) -> dict[str, PluginDefinition]:
    class Source(LocationProvider):
        id = "source"
        code_revision = "1"
        schema = RECORDS
        session: Annotated[Session, Resource("session")]

        @override
        def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
            assert self.session.open
            trace.events.append((self.id, self.session.parent))
            return gpd.GeoDataFrame(
                {"location_id": ids, "value": [1] * len(ids)},
                geometry=[Point(0, 0)] * len(ids),
                crs=4326,
            )

        @override
        def close(self) -> None:
            assert self.session.open
            trace.events.append((f"close:{self.id}", self.session.parent))

    class Middle(Source):
        id = "middle"

    class Target(Source):
        id = "target"

        @override
        def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
            frame = super().resolve(ids)
            trace.capture("acquired", frame)
            if failure == "missing":
                return frame.iloc[:0]
            if failure == "provider":
                frame["extra"] = 1
            return frame.assign(value=10)

    return {
        "providers": PluginDefinition(
            resources={"source": Source, "middle": Middle, "target": Target}
        ),
        "resources": PluginDefinition(
            resources={**runtime_resources(trace), "unused": Unused}
        ),
    }


def metrics(trace: Trace, policy: CachePolicy, failure: str | None) -> list[Callable]:
    def measure(
        places: gpd.GeoDataFrame,
        *,
        parameters: Options,
        memory: Annotated[Memory, Resource("memory")],
    ) -> pd.DataFrame:
        assert memory.open
        trace.events.append(("measure", memory))
        trace.capture("measure", places)
        trace.parameters.append(("measure", list(parameters.weights)))
        weight = parameters.weights[0]
        parameters.weights.append(999)
        if failure == "metric":
            message = "measurement failed"
            raise RuntimeError(message)
        frame = pd.DataFrame(
            {
                "location_id": places.location_id,
                "row_key": places.get("source_location_id", places.location_id),
                "value": places.value * weight,
            }
        )
        if failure == "output":
            frame["location_id"] = "outside"
        places.loc[:, "value"] = -99
        return frame

    @metric(
        name="producer",
        description="Measure target-owned records",
        code_revision="1",
        cache=policy,
        inputs={"places": LocationDependency(provider="target", primary=True)},
        outputs={"rows": schema()},
    )
    def producer(
        places: gpd.GeoDataFrame,
        *,
        parameters: Options,
        handler: Annotated[HandlerResource, Resource("handler")],
    ) -> pd.DataFrame:
        assert handler.open
        return measure(places, parameters=parameters, memory=handler.shared)

    @metric(
        name="renamed",
        description="Measure through a renamed receiving argument",
        code_revision="1",
        cache=policy,
        inputs={"targets": LocationDependency(provider="target", primary=True)},
        outputs={"rows": schema()},
    )
    def renamed(
        targets: gpd.GeoDataFrame,
        *,
        parameters: Options,
        handler: Annotated[HandlerResource, Resource("handler")],
    ) -> pd.DataFrame:
        assert handler.open
        return measure(targets, parameters=parameters, memory=handler.shared)

    return [producer, renamed]


def dependency_binding(binding: str) -> Dependency:
    dependency = Dependency("rows", producer="producer", parameters={"weights": [3]})
    if binding == "explicit":
        dependency = dependency.bind(places=Input("places"))
    elif binding == "renamed":
        dependency = Dependency(
            "rows", producer="renamed", parameters={"weights": [3]}
        ).bind(targets=Input("places"))
    elif binding in {"operation_source", "nested", "same"}:
        supply = Input("places")
        if binding == "nested":
            supply = LocationDependency(
                "initial", parameters={"weights": Input("parameters.weights")}
            ).bind(source=supply)
        dependency = dependency.bind(
            places=LocationDependency(
                "same" if binding == "same" else "explicit",
                parameters={"weights": Input("parameters.weights")},
            ).bind(source=supply)
        )

    return dependency


def definitions(
    trace: Trace,
    *,
    binding: str = "matching",
    policy: CachePolicy = "off",
    failure: str | None = None,
    options: type[MetricParameters] = Options,
) -> dict[str, PluginDefinition]:
    routes = Routes(trace, policy, failure, options)
    first = routes.operation("first", "source", "middle")
    second = routes.operation("second", "middle", "target")
    alternate = routes.operation("alternate", "source", "middle")
    explicit = routes.operation("explicit", "target", "middle", canonical=False)
    same = routes.operation("same", "target", "target", canonical=False)
    initial = routes.operation("initial", "source", "middle", canonical=False)
    dependency = dependency_binding(binding)

    @metric(
        name="root",
        description="Summarize target measurements from source records",
        code_revision="1",
        cache="off",
        inputs={"places": LocationDependency(provider="source"), "data": dependency},
        outputs={"total": RESULT},
    )
    def root(
        places: gpd.GeoDataFrame, data: pd.DataFrame, *, parameters: Options
    ) -> pd.DataFrame:
        trace.capture("root", places)
        assert parameters.weights == [99]
        return pd.DataFrame({"total": [int(data.value.sum())]})

    return {
        "metrics": PluginDefinition(metrics=[root, *metrics(trace, policy, failure)]),
        "first": PluginDefinition(location_operations=[first, initial]),
        "second": PluginDefinition(
            location_operations=[second, explicit, same, alternate],
            route_preferences=[(first,), (first, second)],
        ),
        **providers(trace, failure),
    }


def request(ids: tuple[str, ...] = ("a", "b")) -> dict:
    return {
        "metric": "root",
        "inputs": {"places": {"provider": "source", "ids": list(ids)}},
        "parameters": {"weights": [99]},
    }
