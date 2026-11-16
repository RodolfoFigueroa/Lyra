"""Exact transport sizing and whole-candidate selection for retained retrievals."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from pydantic import BaseModel
from starlette.responses import JSONResponse

MAX_RETRIEVAL_BYTES = 64 * 1024
_T = TypeVar("_T")


class ResponseTooLargeError(ValueError):
    """A fixed preview, link, error, or page cannot fit without losing fields."""


@dataclass(frozen=True)
class ResponseBudget(Generic[_T]):
    """Use a transport's actual encoder; never trim or mutate candidate bodies."""

    size: Callable[[_T], int]
    limit: int = MAX_RETRIEVAL_BYTES

    def fits(self, candidate: _T) -> bool:
        """Return whether the complete encoded response fits, including its envelope."""
        return self.size(candidate) <= self.limit

    def require_fits(self, candidate: _T) -> _T:
        """Return an intact bounded response.

        Raises:
            ResponseTooLargeError: If mandatory fields exceed the transport limit.
        """
        if not self.fits(candidate):
            msg = "The fixed retrieval response exceeds the encoded response limit."
            raise ResponseTooLargeError(msg)
        return candidate

    def choose(self, complete: _T, preview: _T) -> _T:
        """Choose the complete body or fixed preview; validate the preview itself.

        Feature handlers supply fully formed typed variants, including download
        links and omission markers. Required fields are never shortened. Row-page
        handlers use fits/require_fits with the same transport encoder.

        Returns:
            One unchanged candidate within the limit.
        """
        self.require_fits(preview)
        return complete if self.fits(complete) else preview


def http_json_response(payload: BaseModel, *, status_code: int = 200) -> JSONResponse:
    """Return the typed JSON response measured by http_response_size."""
    return JSONResponse(
        payload.model_dump(mode="json", by_alias=True), status_code=status_code
    )


def http_response_size(payload: BaseModel) -> int:
    """Return encoded UTF-8 HTTP response-body bytes, including JSON escaping."""
    return len(http_json_response(payload).body)


def http_response_budget(
    *, limit: int = MAX_RETRIEVAL_BYTES
) -> ResponseBudget[BaseModel]:
    """Return the common HTTP inspection/page budget."""
    return ResponseBudget(http_response_size, limit)
