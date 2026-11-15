"""Error translation confined to managed persistence operations."""

from collections.abc import Generator
from contextlib import contextmanager

from lyra.contracts.errors import ExecutionPersistenceError


@contextmanager
def persistence_boundary(execution_id: str) -> Generator[None, None, None]:
    """Translate a storage failure while preserving its original cause.

    Yields:
        Control to a required storage operation.

    Raises:
        ExecutionPersistenceError: If storage fails.
    """
    try:
        yield
    except ExecutionPersistenceError:
        raise
    except Exception as error:
        raise ExecutionPersistenceError(execution_id) from error
