"""Standalone location entry points for the unified typed IPC protocol."""

from lyra.sdk import ipc_codec
from lyra.sdk.location_snapshot import LocationSnapshot


class ArtifactDecodeError(ValueError):
    """Malformed or unsupported location artifact."""


def encode(snapshot: LocationSnapshot) -> bytes:
    """Return uncompressed typed location artifact bytes."""
    return ipc_codec.encode(snapshot)


def decode(source: bytes | bytearray | memoryview) -> LocationSnapshot:
    """Return verified, owned location contents.

    Raises:
        ArtifactDecodeError: If bytes are invalid or contain a metric snapshot.
    """
    try:
        snapshot = ipc_codec.decode(source)
    except ValueError as error:
        raise ArtifactDecodeError(str(error)) from error
    if snapshot.scope is None or snapshot.geometry is None or snapshot.scope.metric:
        message = "Location artifact requires geometry and scope"
        raise ArtifactDecodeError(message)
    result = LocationSnapshot(snapshot.table, snapshot.geometry, snapshot.scope)
    _set_frozen(result, "_fingerprints", snapshot.fingerprints)
    return result


_set_frozen = object.__setattr__
