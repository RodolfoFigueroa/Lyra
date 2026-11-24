"""Shared schema-directed row encoding and transport-bounded SQL pages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from lyra.contracts.errors import RowTooLargeError
from lyra.contracts.results import ResultRowPage
from lyra.contracts.retrieval_paths import (
    absolute_api_url,
    location_input_download_path,
    table_download_path,
)
from lyra.data.relational import encode_row

if TYPE_CHECKING:
    from pydantic import BaseModel

    from lyra.runtime.pagination import CandidatePage
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
        job_id: str,
        output_name: str,
        limit: int = 100,
        cursor: str | None = None,
    ) -> ResultRowPage:
        """Select complete rows within SQL and actual transport response bounds.

        Returns:
            A page whose cursor advances only past rows included in the response.
        """
        with self.store.page_candidates(job_id, output_name, limit, cursor) as selected:
            return self._build(selected, job_id, output_name)

    def build_location_input(
        self,
        job_id: str,
        input_name: str,
        limit: int = 100,
        cursor: str | None = None,
    ) -> ResultRowPage:
        """Return complete input rows within the active transport's encoded budget."""
        with self.store.location_input_page_candidates(
            job_id, input_name, limit, cursor
        ) as selected:
            return self._build(selected, job_id, None, input_name=input_name)

    def _build(
        self,
        selected: CandidatePage,
        job_id: str,
        output_name: str | None,
        *,
        input_name: str | None = None,
    ) -> ResultRowPage:
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
                path = (
                    location_input_download_path(job_id, input_name)
                    if input_name is not None
                    else table_download_path(job_id, output_name or "")
                )
                raise RowTooLargeError(
                    job_id,
                    output_name,
                    absolute_api_url(self.public_api_base_url, path),
                    input_name=input_name,
                )
            page = proposed
        self.budget.require_fits(page)
        return page
