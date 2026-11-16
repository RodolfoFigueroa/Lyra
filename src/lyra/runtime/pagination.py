"""Internal SQL page candidates and opaque retained-output continuations."""

from __future__ import annotations

import base64
import binascii
from contextlib import closing
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lyra.contracts.errors import InvalidPaginationError
from lyra.runtime.output_selection import RowAddress

if TYPE_CHECKING:
    from collections.abc import Generator

    from lyra.contracts.provider_scope import ProviderScope
    from lyra.data.relational import Cell
    from lyra.runtime.output_selection import OutputSelection
    from lyra.runtime.sqlite_schema import Registration


class _Cursor(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    version: Literal[1] = 1
    result_ref: str
    output_name: str
    computation: int = Field(ge=0)
    key: tuple[str | Annotated[int, Field(ge=-(2**63), le=2**63 - 1)], ...]

    def encode(self) -> str:
        return base64.urlsafe_b64encode(self.model_dump_json().encode()).decode()


def validate_pagination(
    result_ref: str, output_name: str, limit: int, cursor: str | None
) -> RowAddress | None:
    """Validate request bounds and decode an exact result/output-bound cursor.

    Returns:
        The exclusive private position, or None for an initial request.

    Raises:
        InvalidPaginationError: If the limit or cursor is invalid.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise InvalidPaginationError(result_ref, output_name, "limit")
    if cursor is None:
        return None
    try:
        if not isinstance(cursor, str):
            raise InvalidPaginationError(result_ref, output_name, "cursor")
        decoded = _Cursor.model_validate_json(
            base64.b64decode(cursor, altchars=b"-_", validate=True)
        )
        if (
            decoded.result_ref != result_ref
            or decoded.output_name != output_name
            or decoded.encode() != cursor
        ):
            raise InvalidPaginationError(result_ref, output_name, "cursor")
    except (ValueError, ValidationError, binascii.Error) as error:
        raise InvalidPaginationError(result_ref, output_name, "cursor") from error
    return RowAddress(decoded.computation, decoded.key)


def validate_position(
    selected: OutputSelection,
    result_ref: str,
    output_name: str,
    after: RowAddress | None,
) -> None:
    """Require a continuation to address a row in the selected retained scope.

    Raises:
        InvalidPaginationError: If the address is not part of this output.
    """
    if after is None:
        return
    if after.computation >= len(selected.computations):
        raise InvalidPaginationError(result_ref, output_name, "cursor")
    computation = selected.computations[after.computation]
    expected = (
        (str, str) if computation.registration.output_scope == "location" else (int,)
    )
    if len(after.key) != len(expected) or any(
        type(value) is not kind for value, kind in zip(after.key, expected, strict=True)
    ):
        raise InvalidPaginationError(result_ref, output_name, "cursor")
    query = (
        computation.select(*computation.keys)
        .where(
            *(
                column == value
                for column, value in zip(computation.keys, after.key, strict=True)
            )
        )
        .limit(1)
    )
    result = selected.connection.execute(query)
    try:
        if result.mappings().one_or_none() is None:
            raise InvalidPaginationError(result_ref, output_name, "cursor")
    finally:
        result.close()


@dataclass(frozen=True)
class RowCandidate:
    """Declared SQL cells and the continuation valid only after returning them.

    A consumer that rejects this candidate keeps the preceding returned
    candidate's next_cursor (or the request cursor if no rows were returned).
    None means the retained output ends immediately after this candidate.
    """

    cells: tuple[Cell, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class CandidatePage:
    """Retained encoding metadata and a context-owned, single-pass row iterator."""

    registration: Registration
    owner_scope: ProviderScope | None
    rows: Generator[RowCandidate]


def candidates(
    selected: OutputSelection,
    result_ref: str,
    output_name: str,
    limit: int,
    after: RowAddress | None,
) -> Generator[RowCandidate]:
    """Yield at most limit candidates using bounded SQL and one lookahead row.

    Each yielded continuation advances only past that candidate, irrespective
    of prefetched rows. Closing the generator releases its batch iterator.
    """
    with closing(selected.batches(after=after, limit=limit + 1)) as batches:
        rows = (
            (cells, address)
            for batch in batches
            for cells, address in zip(batch.rows, batch.addresses, strict=True)
        )
        current = next(rows, None)
        for _ in range(limit):
            if current is None:
                break
            following = next(rows, None)
            cells, address = current
            continuation = (
                _Cursor(
                    result_ref=result_ref,
                    output_name=output_name,
                    computation=address.computation,
                    key=address.key,
                ).encode()
                if following is not None
                else None
            )
            yield RowCandidate(cells, continuation)
            current = following
