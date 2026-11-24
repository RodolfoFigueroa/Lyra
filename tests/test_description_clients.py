"""Complete model validation and lookup errors through both client entry points."""

from __future__ import annotations

import asyncio
from functools import partial
from typing import TYPE_CHECKING

import httpx
import pytest

from lyra.api import AsyncLyraClient, LyraClient
from lyra.api.exceptions import DownloadError
from lyra.contracts.errors import (
    ResultAccessError,
    ResultIntegrityError,
    ResultNotFoundError,
)
from lyra.contracts.results import ResultDescription
from lyra.contracts.retrieval_errors import error_payload, error_status
from tests.result_description_helpers import location_descriptions

if TYPE_CHECKING:
    from collections.abc import Callable

_REFERENCE = "a" * 32


@pytest.fixture(params=["sync", "async"])
def describe_client(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Callable[[list[httpx.Response]], ResultDescription]:
    def run(responses: list[httpx.Response]) -> ResultDescription:
        def handle(request: httpx.Request) -> httpx.Response:
            assert request.headers["authorization"] == "Bearer secret"
            return responses.pop(0)

        with monkeypatch.context() as patch:
            patch.setattr(
                httpx,
                "Client",
                partial(httpx.Client, transport=httpx.MockTransport(handle)),
            )
            patch.setattr(
                httpx,
                "AsyncClient",
                partial(httpx.AsyncClient, transport=httpx.MockTransport(handle)),
            )
            if request.param == "sync":
                with LyraClient("example.test/api", agent_api_key="secret") as client:
                    return client.results.describe(_REFERENCE)
            else:

                async def fetch() -> ResultDescription:
                    async with AsyncLyraClient(
                        "example.test/api", agent_api_key="secret"
                    ) as client:
                        return await client.results.describe(_REFERENCE)

                return asyncio.run(fetch())

    return run


@pytest.mark.parametrize(
    "error",
    [
        ResultNotFoundError(_REFERENCE),
        ResultIntegrityError(_REFERENCE),
    ],
)
def test_describe_preserves_whole_result_errors(
    describe_client: Callable[[list[httpx.Response]], ResultDescription],
    error: ResultAccessError,
) -> None:
    payload = error_payload(error)
    responses = []
    responses.append(
        httpx.Response(error_status(payload), json={"detail": payload.model_dump()})
    )
    with pytest.raises(type(error)) as caught:
        describe_client(responses)
    assert vars(caught.value) == vars(error)
    assert not responses


@pytest.mark.parametrize("failure", ["model", "json", "media_type"])
def test_describe_validates_complete_model(
    describe_client: Callable[[list[httpx.Response]], ResultDescription],
    failure: str,
) -> None:
    malformed = {}
    response = (
        httpx.Response(200, json=malformed)
        if failure == "model"
        else httpx.Response(
            200, content="{", headers={"Content-Type": "application/json"}
        )
        if failure == "json"
        else httpx.Response(200, text="not JSON")
    )
    responses = [response]
    with pytest.raises(DownloadError):
        describe_client(responses)
    assert not responses


def test_describe_returns_complete_independent_location_metadata(
    describe_client: Callable[[list[httpx.Response]], ResultDescription],
) -> None:
    expected = ResultDescription(
        job_id=_REFERENCE,
        metric_name="metric",
        parameters={},
        outputs={},
        location_inputs=location_descriptions(),
    )

    def read() -> ResultDescription:
        payload = expected.model_dump(mode="json")
        responses = []
        responses.append(
            httpx.Response(
                200,
                json=payload,
            )
        )
        description = describe_client(responses)
        assert not responses
        return description

    first = read()
    assert first.model_dump(mode="json") == expected.model_dump(mode="json")
    first.location_inputs["blocks"].table_schema.columns.clear()
    first.location_inputs["blocks"].provider_scope = first.location_inputs[
        "blocks"
    ].provider_scope.model_copy(update={"description": "Caller edit"})
    first.location_inputs["reference_blocks"].availability = "available"
    first.location_inputs.pop("nearby_trees")
    assert read().model_dump(mode="json") == expected.model_dump(mode="json")
