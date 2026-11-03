"""Plain Python calculations, independent of Lyra decorators and execution."""

import geopandas as gpd
import pandas as pd


# docs:start lengths
def calculate_lengths(streets: gpd.GeoDataFrame) -> pd.DataFrame:
    """Measure the fixture's projected street geometry.

    Returns:
        Identified lengths in metres, preserving input order.
    """
    return pd.DataFrame(
        {"street_id": streets["id"], "length_m": streets.geometry.length}
    ).reset_index(drop=True)


# docs:end lengths


# docs:start maintenance
def calculate_maintenance(
    lengths: pd.DataFrame, conditions: pd.DataFrame, threshold: int
) -> pd.DataFrame:
    """Mark full street lengths whose condition falls below the threshold.

    Returns:
        One row per street, including zero workload for streets above the threshold.
    """
    result = lengths.merge(conditions, on="street_id", validate="one_to_one")
    result["needs_maintenance"] = result["condition_score"] < threshold
    result["maintenance_length_m"] = result["length_m"].where(
        result["needs_maintenance"], 0.0
    )
    return result


# docs:end maintenance
