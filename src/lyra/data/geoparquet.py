"""Shared preparation and atomic writing of retained spatial tables."""

import tempfile
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import geopandas as gpd

from lyra.contracts.errors import ResultIntegrityError

if TYPE_CHECKING:
    import pandas as pd

    from lyra.contracts.outputs import OutputMetadata
    from lyra.contracts.results import ResultDescription
    from lyra.contracts.types import JsonObject


def prepare_geoparquet(
    frame: "pd.DataFrame", schema: "OutputMetadata", metadata: "JsonObject"
) -> gpd.GeoDataFrame:
    """Return an independent declared-column frame with trusted export metadata.

    Raises:
        ValueError: If the declared table is nonspatial.
    """
    if schema.geometry is None:
        msg = "GeoParquet export requires a spatial output"
        raise ValueError(msg)
    prepared = gpd.GeoDataFrame(
        frame.loc[:, [column.name for column in schema.columns]].copy(deep=True),
        geometry=schema.geometry,
    )
    prepared.attrs = {"lyra": deepcopy(metadata)}
    return prepared


def write_geoparquet(
    frame: "pd.DataFrame",
    schema: "OutputMetadata",
    metadata: "JsonObject",
    path: str | Path,
) -> None:
    """Write a complete GeoParquet file before replacing the local destination.

    The parent must exist. Library and filesystem failures propagate unchanged;
    temporary files are removed on exceptions, including cancellation.
    """
    prepared = prepare_geoparquet(frame, schema, metadata)
    destination = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as file:
            temporary = Path(file.name)
            prepared.to_parquet(file, index=False)
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def export_retained_geoparquet(
    frame: "pd.DataFrame",
    description: "ResultDescription",
    kind: Literal["metric_output", "location_input"],
    name: str,
    path: str | Path,
) -> None:
    """Write the named spatial table with its complete retained description.

    Raises:
        ResultIntegrityError: If the frame has no matching retained description.
    """
    if kind == "metric_output":
        selected = description.outputs.get(name)
        if selected is None:
            raise ResultIntegrityError(description.job_id, name)
        name_key = "output_name"
    else:
        selected = description.location_inputs.get(name)
        if selected is None:
            raise ResultIntegrityError(description.job_id, input_name=name)
        name_key = "input_name"
    write_geoparquet(
        frame,
        selected.table_schema,
        {
            "kind": kind,
            "job_id": description.job_id,
            name_key: name,
            "description": selected.model_dump(mode="json"),
        },
        path,
    )
