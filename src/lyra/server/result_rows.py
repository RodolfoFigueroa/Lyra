"""Shared schema-directed row encoding and transport-bounded SQL pages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from lyra.contracts.errors import RowTooLargeError
from lyra.contracts.results import ResultRowPage
from lyra.contracts.retrieval_paths import absolute_api_url, table_download_path
from lyra.data.relational import encode_row

if TYPE_CHECKING:
    from pydantic import BaseModel

    from lyra.runtime.result_store import ResultStore
    from lyra.server.response_budget import ResponseBudget


@dataclass(frozen=True)
class RowPageContext:
    """Transport-specific URL and complete-envelope byte accounting."""

    public_api_base_url: str
    budget: ResponseBudget[BaseModel]


@dataclass(frozen=True)
class RowPageBuilder:
    """Build pages using a retained store and the active transport's encoder."""

    store: ResultStore
    public_api_base_url: str
    budget: ResponseBudget[BaseModel]

    def build(
        self,
        result_ref: str,
        output_name: str,
        limit: int = 100,
        cursor: str | None = None,
    ) -> ResultRowPage:
        """Select complete rows within SQL and actual transport response bounds.

        Returns:
            A page whose cursor advances only past rows included in the response.

        Raises:
            RowTooLargeError: If the next row cannot fit in an otherwise empty page.
        """
        with self.store.page_candidates(
            result_ref, output_name, limit, cursor
        ) as selected:
            description = selected.registration.storage_description()
            page = ResultRowPage(rows=[], next_cursor=None)
            for candidate in selected.rows:
                proposed = ResultRowPage(
                    rows=[*page.rows, encode_row(candidate.cells, description)],
                    next_cursor=candidate.next_cursor,
                )
                if not self.budget.fits(proposed):
                    if page.rows:
                        return page
                    raise RowTooLargeError(
                        result_ref,
                        output_name,
                        absolute_api_url(
                            self.public_api_base_url,
                            table_download_path(result_ref, output_name),
                        ),
                    )
                page = proposed
            self.budget.require_fits(page)
            return page
