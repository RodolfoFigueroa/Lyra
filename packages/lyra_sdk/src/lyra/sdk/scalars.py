"""Scalar normalization shared by table and location serialization."""

import math
from datetime import date, datetime

import numpy as np
import pandas as pd


def date_property(value: object) -> str:
    """Encode supported date values as ISO strings.

    Returns:
        The ISO representation.

    Raises:
        TypeError: If the value is not a date or datetime.
    """
    if isinstance(value, date | datetime):
        return value.isoformat()
    msg = f"unsupported value of type {type(value).__name__}"
    raise TypeError(msg)


def normalize_property(value: object, column: str) -> str | bool | int | float | None:
    """Normalize supported pandas/NumPy property scalars without string coercion.

    Returns:
        A finite JSON scalar, with missing values represented by null.

    Raises:
        ValueError: If the column contains an unsupported value.
    """
    if (
        value is None
        or value is pd.NA
        or value is pd.NaT
        or (isinstance(value, np.datetime64 | np.timedelta64) and np.isnat(value))
    ):
        return None
    if isinstance(value, np.datetime64):
        value = pd.Timestamp(value)
    elif isinstance(value, np.generic) and not isinstance(value, np.timedelta64):
        # Sub-microsecond durations become integers through item(), losing units.
        # Leave nonmissing durations intact so they fail scalar validation below.
        value = value.item()
    if isinstance(value, date | datetime):
        return date_property(value)
    if isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if math.isfinite(value):
            return value
    msg = (
        f"Column {column!r} contains unsupported property value "
        f"of type {type(value).__name__}"
    )
    raise ValueError(msg)
