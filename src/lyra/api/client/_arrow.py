"""Optional ordinary Arrow reconstruction for retained client tables."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lyra.api.exceptions import DownloadError

if TYPE_CHECKING:
    import pandas as pd

try:
    import geopandas as gpd
    import pyarrow as pa
    from pyproj.exceptions import CRSError
    from shapely.errors import ShapelyError
except ModuleNotFoundError as exc:
    if exc.name not in {"pandas", "geopandas", "numpy", "shapely", "pyproj", "pyarrow"}:
        raise
    message = (
        f"Arrow table conversion requires {exc.name}. Install 'lyra[client,data]'."
    )
    raise ImportError(message) from exc


def decode_table(content: bytes) -> pd.DataFrame | gpd.GeoDataFrame:
    """Restore nullable scalars and native geometry from standard Arrow metadata.

    Returns:
        An independent caller-owned frame, without result identity attributes.

    Raises:
        DownloadError: If the stream or its native metadata cannot be decoded.
    """
    try:
        with pa.ipc.open_stream(content) as reader:
            table = reader.read_all()
        geometry = next(
            (
                field.name
                for field in table.schema
                if (field.metadata or {}).get(b"ARROW:extension:name")
                == b"geoarrow.wkb"
            ),
            None,
        )
        if geometry is not None:
            return gpd.GeoDataFrame.from_arrow(table, geometry=geometry)
        return table.to_pandas()
    except (
        pa.ArrowException,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        AttributeError,
        OverflowError,
        CRSError,
        ShapelyError,
    ) as exc:
        message = "Failed to decode result table: invalid Arrow response."
        raise DownloadError(message) from exc
