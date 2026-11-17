"""Shared retained input streams with nullable schemas and isolated storage."""

from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pda
import pytest
from pandera.engines.geopandas_engine import Geometry
from shapely.geometry import Point

from lyra.contracts.results import LocationInputDescription
from lyra.runtime.location_inputs import InputSelection
from lyra.runtime.provider_records import ProviderRecords, provider_registration
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.server.results import ResultService
from tests.acquisition_helpers import acquisition_fixture
from tests.location_input_helpers import publish_inputs


@dataclass
class RetainedInput:
    store: SQLiteStore
    reference: str
    frame: gpd.GeoDataFrame
    selection: InputSelection

    def stream(self) -> Generator[bytes]:
        yield from ResultService(Mock(spec=[]), self.store.path).stream_location_input(
            self.reference, "places"
        )


@pytest.fixture
def retained_input(tmp_path: Path, request: pytest.FixtureRequest) -> RetainedInput:
    state = getattr(request, "param", "populated")
    frame = gpd.GeoDataFrame(
        {
            "location_id": pd.Series(["0012", "002", "003", "004"], dtype="string"),
            "label": pd.Series(['雪 "\\\n', "1e3", "", None], dtype="string"),
            "count": pd.Series([-(2**63), 2**63 - 1, 12, None], dtype="Int64"),
            "ratio": pd.Series([1.2345678901234567, -2.5, 0.0, None], dtype="Float64"),
            "flag": pd.Series([True, False, True, None], dtype="boolean"),
        },
        geometry=[Point(1234.123456789, -4567.987654321)] * 4,
        crs=3857,
    )
    if state == "all-null":
        frame.loc[:, ["label", "count", "ratio", "flag"]] = pd.NA
    if state == "large":
        frame = pd.concat([frame] * 300, ignore_index=True)
        frame["location_id"] = pd.Series(
            [f"id-{i:06}" for i in range(len(frame))], dtype="string"
        )
    if state.startswith("oversized"):
        frame.loc[0 if state == "oversized-first" else 2, "label"] = "雪" * 100000
    schema = pda.DataFrameSchema(
        {
            name: pda.Column(
                Geometry(crs=3857) if name == "geometry" else series.dtype,
                nullable=name not in {"geometry", "location_id"},
                description=f"Declared {name}",
                metadata={"unit": "m" if name == "ratio" else None},
                unique=name == "location_id",
                checks=pda.Check(
                    lambda values: values.is_valid & ~values.is_empty, name="usable"
                )
                if name == "geometry"
                else None,
            )
            for name, series in frame.items()
        },
        strict=True,
    )
    schema.columns["location_id"].checks = [
        pda.Check(lambda values: values.str.len().gt(0), name="nonempty")
    ]
    prepared, _ = acquisition_fixture(
        Mock(side_effect=AssertionError("acquisition")), schema=schema
    )
    provider = prepared.provider
    store = SQLiteStore(tmp_path / "inputs.sqlite")
    ProviderRecords(store).publish(provider, provider.schema.validate(frame))
    selection = InputSelection(
        description=LocationInputDescription(
            kind="provider",
            table_schema=provider.schema.metadata,
            provider_scope=provider.scope,
            availability="available",
        ),
        registration=provider_registration(provider),
        records=tuple(frame.location_id),
    )
    if state == "empty":
        selection = selection.model_copy(update={"records": ()})
        frame = frame.iloc[:0]
    reference = publish_inputs(store, {"places": selection})
    store.path.touch()
    return RetainedInput(store, reference, frame, selection)
