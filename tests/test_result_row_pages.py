"""Bounded JSON pages over isolated retained SQL storage."""

import asyncio
from dataclasses import replace
from pathlib import Path

import geopandas as gpd
import httpx
import pandas as pd
import pandera.pandas as pda
import pyarrow as pa
import pytest
import shapely
from pydantic import BaseModel

from lyra.contracts.errors import RowTooLargeError
from lyra.contracts.results import ResultRowPage
from lyra.runtime.result_store import ResultStore
from lyra.server.mcp.server import LyraFastMCP
from lyra.server.response_budget import ResponseBudget, http_response_budget
from lyra.server.result_rows import RowPageBuilder
from tests.arrow_download_helpers import BASE, Retained, retained
from tests.retained_table_helpers import retained_service
from tests.row_page_helpers import budget
from tests.sqlite_helpers import database

__all__ = ["budget", "database", "retained"]
pytestmark = pytest.mark.usefixtures("database")


@pytest.mark.parametrize(
    "retained",
    [
        (spatial, state)
        for spatial in (False, True)
        for state in ("populated", "empty", "all-null")
    ],
    indirect=True,
)
def test_retained_cells_and_native_arrow(
    retained: Retained, budget: ResponseBudget[BaseModel]
) -> None:
    with ResultStore.open(retained.service.result_store_path) as store:
        page = RowPageBuilder(store, BASE, budget).build(retained.reference, "rows")
    assert page.next_cursor is None
    assert len(page.rows) == len(retained.frame)
    decoded = ResultRowPage.model_validate_json(page.model_dump_json())
    assert decoded == page
    for row, (_, native) in zip(page.rows, retained.frame.iterrows(), strict=True):
        assert set(row) == set(retained.frame.columns)
        for name, value in native.items():
            assert isinstance(name, str)
            if name == "geometry":
                if value is None:
                    assert row[name] is None
                else:
                    encoded = row[name]
                    assert isinstance(encoded, str)
                    assert shapely.equals_exact(shapely.from_wkt(encoded), value, 0)
                    if value.is_empty:
                        assert row[name] == "POINT EMPTY"
            elif pd.isna(value):
                assert row[name] is None
            elif name == "count":
                assert row[name] == str(value)
            else:
                assert row[name] == value
                if name == "flag":
                    assert type(row[name]) is bool
    body = b"".join(retained.service.stream_table(retained.reference, "rows"))
    table = pa.ipc.open_stream(body).read_all()
    native = (
        gpd.GeoDataFrame.from_arrow(table)
        if "geometry" in retained.frame
        else table.to_pandas()
    )
    pd.testing.assert_frame_equal(native, retained.frame, check_exact=True)


@pytest.mark.parametrize("limit", [1, 2, 3, 100])
def test_exact_transport_boundaries_and_traversal(
    retained: Retained, budget: ResponseBudget[BaseModel], limit: int
) -> None:
    with ResultStore.open(retained.service.result_store_path) as store:
        builder = RowPageBuilder(store, BASE, budget)
        complete = builder.build(retained.reference, "rows")
        singles = []
        cursor = None
        for _ in complete.rows:
            page = builder.build(retained.reference, "rows", 1, cursor)
            singles.append(page)
            cursor = page.next_cursor
        maximum = max(map(budget.size, singles))
        for bound in (maximum, maximum + 150, budget.size(complete)):
            bounded = replace(builder, budget=replace(budget, limit=bound))
            observed = []
            cursor = None
            for _ in range(len(complete.rows) + 1):
                page = bounded.build(retained.reference, "rows", limit, cursor)
                assert budget.size(page) <= bound
                assert len(page.rows) <= limit
                observed.extend(page.rows)
                cursor = page.next_cursor
                if cursor is None:
                    break
            else:
                pytest.fail("Traversal did not terminate")
            assert observed == complete.rows
        exact = replace(builder, budget=replace(budget, limit=budget.size(singles[0])))
        assert exact.build(retained.reference, "rows", 1) == singles[0]
        smaller = replace(
            builder, budget=replace(budget, limit=budget.size(singles[0]) - 1)
        )
        with pytest.raises(RowTooLargeError):
            smaller.build(retained.reference, "rows", 1)


@pytest.mark.parametrize("prefix", [[], ['雪 "\\\n', '雪 "\\\n']])
def test_oversized_row_retry_and_complete_fallback(
    tmp_path: Path, budget: ResponseBudget[BaseModel], prefix: list[str]
) -> None:
    values = [*prefix, "oversized-private-cell" * 10000, "last"]
    frame = pd.DataFrame({"value": pd.Series(values, dtype="string")})
    service, reference = retained_service(
        tmp_path / "results.sqlite",
        pda.DataFrameSchema(
            {
                "value": pda.Column(
                    pd.StringDtype(), description="Text", metadata={"unit": None}
                )
            },
            strict=True,
        ),
        frame,
    )
    with ResultStore.open(service.result_store_path) as store:
        builder = RowPageBuilder(store, BASE, budget)
        cursor = None
        if prefix:
            page = builder.build(reference, "rows")
            assert page.rows == [{"value": value} for value in prefix]
            assert page.next_cursor is not None
            cursor = page.next_cursor
        for _ in range(2):
            with pytest.raises(RowTooLargeError) as caught:
                builder.build(reference, "rows", cursor=cursor)
            assert caught.value.result_ref == reference
            assert caught.value.output_name == "rows"
            assert (
                caught.value.download_url
                == f"{BASE}/results/{reference}/tables/rows/download"
            )
        body = b"".join(store.stream_table(reference, "rows"))
        pd.testing.assert_frame_equal(
            pa.ipc.open_stream(body).read_all().to_pandas(), frame
        )


@pytest.mark.parametrize("limit", [1, 2, 100])
def test_small_integers_and_equal_rows_remain_distinct(
    tmp_path: Path, limit: int
) -> None:
    values = [0, 12, -3, 12, 12]
    frame = pd.DataFrame({"value": pd.Series(values, dtype="Int64")})
    service, reference = retained_service(
        tmp_path / "results.sqlite",
        pda.DataFrameSchema(
            {
                "value": pda.Column(
                    pd.Int64Dtype(), description="Count", metadata={"unit": None}
                )
            },
            strict=True,
        ),
        frame,
    )
    with ResultStore.open(service.result_store_path) as store:
        builder = RowPageBuilder(store, BASE, http_response_budget())
        observed = []
        cursor = None
        for _ in range(len(values)):
            page = builder.build(reference, "rows", limit, cursor)
            observed.extend(page.rows)
            cursor = page.next_cursor
            if cursor is None:
                break
        assert observed == [{"value": str(value)} for value in values]
        assert cursor is None


def test_sdk_wire_page_matches_measured_budget(retained: Retained) -> None:
    server = LyraFastMCP(
        json_response=True, stateless_http=True, streamable_http_path="/"
    )
    measured = []

    @server.tool()
    def rows(bound: int = 65536) -> ResultRowPage:
        budget = replace(server.retrieval_budget("rows"), limit=bound)
        with ResultStore.open(retained.service.result_store_path) as store:
            page = RowPageBuilder(store, BASE, budget).build(
                retained.reference, "rows", 1
            )
        measured.append(budget.size(page))
        return page

    async def run() -> None:
        app = server.streamable_http_app()
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
            ) as client,
        ):

            async def call(bound: int) -> httpx.Response:
                response = await client.post(
                    "/",
                    headers={"Accept": "application/json, text/event-stream"},
                    json={
                        "jsonrpc": "2.0",
                        "id": '雪"\\',
                        "method": "tools/call",
                        "params": {"name": "rows", "arguments": {"bound": bound}},
                    },
                )
                assert response.status_code == 200
                assert not response.json()["result"]["isError"]
                assert len(response.content) == measured[-1] <= bound
                return response

            complete = await call(65536)
            exact = await call(len(complete.content))
            assert exact.content == complete.content
            page = ResultRowPage.model_validate(
                exact.json()["result"]["structuredContent"]
            )
            assert page.next_cursor is not None
            assert len(page.rows) == 1

    asyncio.run(run())
