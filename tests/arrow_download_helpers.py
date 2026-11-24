"""Isolated retained Arrow outputs shared by HTTP and native client tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pda
import pytest
from pandera.engines.geopandas_engine import Geometry
from shapely.geometry import Point
from starlette.applications import Starlette
from starlette.routing import Mount

from lyra.contracts.retrieval_paths import absolute_api_url, table_download_path
from lyra.runtime.output_selection import OutputSelection
from lyra.runtime.result_store import ResultStore
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.server.main import create_app
from lyra.server.results import ResultService
from lyra.server.runtime import ApplicationRuntime
from tests.config_helpers import api_credentials, load_test_config
from tests.retained_table_helpers import mixed_result, retained_service

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI

    from tests.sqlite_helpers import Database

BASE = "https://lyra.example.test/public/api"


@dataclass
class Retained:
    app: FastAPI
    public: Starlette
    service: ResultService
    reference: str
    frame: pd.DataFrame
    output_name: str = "rows"

    @property
    def url(self) -> str:
        return absolute_api_url(
            BASE, table_download_path(self.reference, self.output_name)
        )


@pytest.fixture
def retained(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> Retained:
    spatial, state = getattr(request, "param", (True, "populated"))
    frame = pd.DataFrame(
        {
            "label": pd.Series([" 001 雪 ", "1e3", "", None], dtype="string"),
            "count": pd.Series([-(2**63), 2**63 - 1, 2**60 + 1, None], dtype="Int64"),
            "ratio": pd.Series([1.2345678901234567, -2.5, 0.0, None], dtype="Float64"),
            "flag": pd.Series([True, False, True, None], dtype="boolean"),
        }
    )
    if state == "python-string":
        frame["label"] = frame.label.astype("string[python]")
    if state == "all-null":
        frame.iloc[:] = pd.NA
    if spatial:
        frame = gpd.GeoDataFrame(
            frame,
            geometry=gpd.GeoSeries(
                [None] * 4
                if state == "all-null"
                else [Point(1234.123456789, -4567.987654321), Point(), None, None]
            ),
            crs=3857,
        )
    if state == "empty":
        frame = frame.iloc[:0]
    elif state == "large":
        frame = pd.concat([frame] * 600, ignore_index=True)
    if state.startswith("oversized"):
        frame.loc[0 if state == "oversized-first" else 2, "label"] = "雪" * 100000
    declaration = pda.DataFrameSchema(
        {
            name: pda.Column(
                Geometry(crs=3857) if name == "geometry" else series.dtype,
                nullable=True,
                checks=pda.Check(
                    lambda values: values.dropna().is_valid.all(), name="valid_geometry"
                )
                if name == "geometry"
                else None,
                description=f"Declared {name}",
                metadata={"unit": "m" if name == "ratio" else None},
            )
            for name, series in frame.items()
        },
        strict=True,
    )
    config = load_test_config(tmp_path)
    config.api.public_base_url = BASE
    config.mcp.enabled = True
    if state == "mixed":
        _, reference = mixed_result(SQLiteStore(config.result_store_path))
        config.result_store_path.touch()
        service = ResultService(Mock(), config.result_store_path)
        frame = pd.DataFrame(
            {
                "location_id": ["same", "same", "next"],
                "row_key": ["a", "z", "r"],
                "value": pd.array([10, 30, 2], dtype="Int64"),
            }
        )
    else:
        service, reference = retained_service(
            config.result_store_path, declaration, frame
        )
    service.jobs.read_job = Mock(side_effect=AssertionError("Expired job"))
    app = create_app(config, credentials=api_credentials())
    app.state.runtime = ApplicationRuntime(
        config, Mock(spec=[]), Mock(), Mock(), service.jobs, api_credentials(), service
    )
    database.rows["final_results"][0]["report"] = "{"
    for method in ("read_result", "read_report", "read_table"):
        monkeypatch.setattr(
            ResultStore, method, Mock(side_effect=AssertionError("Read isolation"))
        )
    monkeypatch.setattr(
        OutputSelection,
        "frame",
        Mock(side_effect=AssertionError("Bounded batches only")),
    )
    return Retained(
        app,
        Starlette(routes=[Mount("/public/api", app)]),
        service,
        reference,
        frame,
        "a" if state == "mixed" else "rows",
    )
