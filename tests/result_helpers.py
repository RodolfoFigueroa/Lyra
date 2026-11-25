"""Isolated retained-result fixtures using current native artifact boundaries."""

from datetime import UTC, datetime

import geopandas as gpd
import pandas as pd

from lyra.contracts.inspection import TerminalResultOutput
from lyra.contracts.jobs import (
    FailedJobResult,
    JobRunProvenance,
    JobStatusInfo,
    TableJobResult,
    TerminalJobResult,
)
from lyra.data.inspection import summarize_outputs
from lyra.server.job_store import JobObservation
from lyra.server.result_projection import project_observation
from tests.provenance_helpers import result_provenance


def result_observation(
    *,
    snapshot: JobStatusInfo,
    result: TerminalJobResult | None = None,
    provenance: JobRunProvenance | None = None,
) -> JobObservation:
    if isinstance(result, FailedJobResult):
        snapshot = snapshot.model_copy(update={"error": result.error})
    if result is not None:
        provenance = result.provenance or provenance
    if isinstance(result, TableJobResult):
        provenance = provenance or result_provenance(
            {
                "metric": snapshot.metric or "fixture",
                "outputs": result.output_metadata,
                "plugin": {"name": "fixture", "version": "1"},
            }
        )
        inspection = summarize_outputs(observed_tables(result), result.output_metadata)
    else:
        inspection = None
    return JobObservation(
        snapshot=snapshot,
        inspection=inspection,
        declarations=result.output_metadata
        if isinstance(result, TableJobResult)
        else {},
        provenance=provenance,
    )


def inspection_of(
    result: TableJobResult | FailedJobResult,
    *,
    completed_at: datetime | None = None,
    provenance: JobRunProvenance | None = None,
) -> TerminalResultOutput:
    now = completed_at or datetime.now(UTC)
    inspection = project_observation(
        result.job_id,
        result_observation(
            snapshot=JobStatusInfo(
                job_id=result.job_id,
                status=result.status,
                created_at=now,
                updated_at=now,
                completed_at=now,
            ),
            result=result,
            provenance=provenance,
        ),
    )
    assert isinstance(inspection, TerminalResultOutput)
    return inspection


def observed_tables(result: TableJobResult) -> dict[str, pd.DataFrame]:
    """Construct observed test records independently of declared type conversion."""
    tables = {}
    for name, payload in result.outputs.items():
        declaration = result.output_metadata[name]
        if isinstance(payload, list):
            tables[name] = (
                pd.DataFrame(payload)
                if payload
                else pd.DataFrame(
                    columns=[column.name for column in declaration.columns]
                )
            )
        else:
            geometry = declaration.geometry or "geometry"
            features = payload["features"]
            assert isinstance(features, list)
            assert all(isinstance(feature, dict) for feature in features)
            if features:
                frame = gpd.GeoDataFrame.from_features(
                    [feature for feature in features if isinstance(feature, dict)],
                    crs=4326,
                )
            else:
                frame = gpd.GeoDataFrame(
                    columns=[
                        column.name
                        for column in declaration.columns
                        if column.name != geometry
                    ],
                    geometry=gpd.GeoSeries([], crs=4326),
                )
            tables[name] = (
                frame.rename_geometry(geometry) if geometry != "geometry" else frame
            )
    return tables
