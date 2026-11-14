"""Concrete provider scopes constrain retained records and dependent partitions."""

from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pytest
from pydantic import Field
from shapely.geometry import Point
from typing_extensions import override

from lyra.contracts.provider_scope import ProviderScope
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.bindings import bind_execution
from lyra.runtime.planning.preparation import prepare_execution
from lyra.sdk import Dependency, Input, LocationDependency, LocationProvider, Origin
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition, metric
from tests.metric_execution_helpers import schema
from tests.provider_schema_helpers import declaration

Work = dict[str, set[str]]


class Parameters(MetricParameters):
    offset: int = Field(default=0, description="Value offset")


def definitions(
    work: Work, change: str = "original", offset: int = 5
) -> dict[str, PluginDefinition]:
    census = Origin(id="census", revision="2" if change == "census" else "1")
    origins = (census, Origin(id="catalog"))
    if change == "editorial":
        origins = (Origin(id="catalog", description="Edited"), census, census)

    class Blocks(LocationProvider):
        id = "blocks"
        code_revision = "2" if change == "blocks" else "1"
        schema = declaration()
        cache = True

        @override
        def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
            work.setdefault(self.id, set()).update(ids)
            return gpd.GeoDataFrame(
                {"location_id": ids, "count": pd.array([1] * len(ids), dtype="Int64")},
                geometry=[Point(0, 0)] * len(ids),
                crs=4326,
            )

    class Tracts(Blocks):
        id = "tracts"
        code_revision = "1"

    Blocks.origins = origins
    Tracts.origins = origins

    def values(places: gpd.GeoDataFrame, offset: int) -> pd.DataFrame:
        return pd.DataFrame(
            {"location_id": places.location_id, "row_key": "value", "value": offset + 1}
        )

    @metric(
        name="block_value",
        description="Block value",
        cache="per_location",
        inputs={"places": LocationDependency(provider="blocks", primary=True)},
        outputs={"result": schema()},
        origins=[Origin(id="raster", revision="2" if change == "raster" else "1")],
    )
    def block_value(places: gpd.GeoDataFrame, parameters: Parameters) -> pd.DataFrame:
        work.setdefault("block_value", set()).update(places.location_id)
        return values(places, parameters.offset)

    @metric(
        name="tract_value",
        description="Tract value",
        cache="per_location",
        inputs={"places": LocationDependency(provider="tracts", primary=True)},
        outputs={"result": schema()},
    )
    def tract_value(places: gpd.GeoDataFrame) -> pd.DataFrame:
        work.setdefault("tract_value", set()).update(places.location_id)
        return values(places, 10)

    @metric(
        name="block_dependent",
        description="Dependent block value",
        cache="per_location",
        inputs={
            "places": LocationDependency(provider="blocks", primary=True),
            "data": Dependency("result", producer="block_value"),
        },
        outputs={"result": schema()},
    )
    def block_dependent(places: gpd.GeoDataFrame, data: pd.DataFrame) -> pd.DataFrame:
        work.setdefault("block_dependent", set()).update(places.location_id)
        return data

    @metric(
        name="uses",
        description="Distinct uses",
        cache="full",
        inputs={
            "left": LocationDependency(provider="blocks"),
            "right": LocationDependency(provider="blocks"),
            "first": Dependency("result", producer="block_value").bind(
                places=Input("left")
            ),
            "second": Dependency(
                "result", producer="block_value", parameters={"offset": offset}
            ).bind(places=Input("right")),
        },
        outputs={
            "result": schema().rename_columns(
                {"location_id": "place", "row_key": "use"}
            )
        },
    )
    def uses(
        left: gpd.GeoDataFrame,
        right: gpd.GeoDataFrame,
        first: pd.DataFrame,
        second: pd.DataFrame,
    ) -> pd.DataFrame:
        assert set(first.location_id) == set(left.location_id)
        assert set(second.location_id) == set(right.location_id)
        work.setdefault("uses", set()).update(left.location_id)
        return pd.concat(
            [first.assign(row_key="first"), second.assign(row_key="second")],
            ignore_index=True,
        ).rename(columns={"location_id": "place", "row_key": "use"})

    prefix = "renamed" if change == "editorial" else "resource"
    return {
        "test": PluginDefinition(
            metrics=[block_value, tract_value, block_dependent, uses],
            resources={f"{prefix}_blocks": Blocks, f"{prefix}_tracts": Tracts},
        )
    }


@pytest.mark.parametrize(
    "change", ["original", "editorial", "blocks", "census", "raster"]
)
def test_concrete_provider_invalidation(tmp_path: Path, change: str) -> None:
    work: Work = {}
    path = tmp_path / "values"

    def run(runner: LocalRunner, ids: list[str], revision: str = "original") -> None:
        for provider, node, expected in (
            ("blocks", "block_dependent", 1),
            ("tracts", "tract_value", 11),
        ):
            result = runner.run(
                node, inputs={"places": {"provider": provider, "ids": ids}}
            )
            assert dict(
                zip(
                    result.tables["result"].location_id,
                    result.tables["result"].value,
                    strict=True,
                )
            ) == dict.fromkeys(ids, expected)
            assert result.metadata.outputs["result"].owner_scope == ProviderScope(
                provider_id=provider,
                code_revision="2"
                if revision == "blocks" and provider == "blocks"
                else "1",
                origins=[
                    Origin(id="census", revision="2" if revision == "census" else "1"),
                    Origin(id="catalog"),
                ],
            )

    runner = LocalRunner.from_definitions(definitions(work), result_store_path=path)
    run(runner, ["a", "b"])
    assert work == {
        key: {"a", "b"}
        for key in ("blocks", "tracts", "block_value", "tract_value", "block_dependent")
    }
    work.clear()
    run(runner, ["b", "c"])
    assert work == {
        key: {"c"}
        for key in ("blocks", "tracts", "block_value", "tract_value", "block_dependent")
    }
    work.clear()
    runner = LocalRunner.from_definitions(
        definitions(work, change), result_store_path=path
    )
    run(runner, ["c", "b"], change)
    affected = {
        "original": (),
        "editorial": (),
        "blocks": ("blocks", "block_value", "block_dependent"),
        "census": ("blocks", "tracts", "block_value", "tract_value", "block_dependent"),
        "raster": ("block_value", "block_dependent"),
    }[change]
    assert work == {key: {"b", "c"} for key in affected}
    work.clear()
    run(runner, ["b", "c"], change)
    run(runner, [], change)
    assert work == {}


@pytest.mark.parametrize("offset", [0, 5])
def test_distinct_metric_uses_and_normalized_defaults(
    tmp_path: Path, offset: int
) -> None:
    work: Work = {}
    runner = LocalRunner.from_definitions(
        definitions(work, offset=offset), result_store_path=tmp_path / "values"
    )
    inputs = {
        "left": {"provider": "blocks", "ids": ["a", "b"]},
        "right": {"provider": "blocks", "ids": ["b", "c"]},
    }
    for attempt in range(2):
        result = runner.run("uses", inputs=inputs)
        assert set(result.tables["result"].itertuples(index=False, name=None)) == {
            ("a", "first", 1),
            ("b", "first", 1),
            ("b", "second", offset + 1),
            ("c", "second", offset + 1),
        }
        if attempt == 1:
            assert work == {}
        else:
            assert work == {
                "blocks": {"a", "b", "c"},
                "block_value": {"a", "b", "c"},
                "uses": {"a", "b"},
            }
        work.clear()
    assert work == {}
    result = runner.run(
        "block_value", inputs={"places": inputs["left"]}, parameters={"offset": 0}
    )
    assert result.tables["result"].value.tolist() == [1, 1]
    assert work == {}


def test_empty_scope_and_direct_origins_are_bound_without_acquisition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work: Work = {}
    identities = []
    for change in ("original", "blocks", "census", "raster"):
        plugins = definitions(work, change)
        for resource in plugins["test"].resources.values():
            monkeypatch.setattr(
                resource,
                "setup",
                Mock(side_effect=AssertionError("resource setup during binding")),
            )
        runner = LocalRunner.from_definitions(plugins)
        prepared = prepare_execution(
            runner.catalog,
            {
                "metric": "block_dependent",
                "inputs": {"places": {"provider": "blocks", "ids": []}},
            },
        )
        bound = bind_execution(runner.catalog, prepared)
        assert (
            Origin(id="raster", revision="2" if change == "raster" else "1")
            in bound.origins
        )
        identities.append(bound.identity)
    assert len(set(identities)) == 4
    assert work == {}
