"""Date encoding for result serialization."""

from datetime import date, datetime


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
