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
from lyra.contracts.results import ResultDescription, ResultDescriptionPreview
from lyra.contracts.retrieval_errors import error_payload, error_status
from tests.result_description_helpers import location_descriptions

if TYPE_CHECKING:
    from collections.abc import Callable

_REFERENCE = "a" * 32
_DOWNLOAD = f"https://example.test/api/results/{_REFERENCE}/description/download"


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


def _preview() -> httpx.Response:
    return httpx.Response(
        200,
        json=ResultDescriptionPreview(
            job_id=_REFERENCE,
            download_url=_DOWNLOAD,
        ).model_dump(mode="json"),
    )


@pytest.mark.parametrize("preview", [False, True])
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
    *,
    preview: bool,
) -> None:
    payload = error_payload(error)
    responses = [_preview()] if preview else []
    responses.append(
        httpx.Response(error_status(payload), json={"detail": payload.model_dump()})
    )
    with pytest.raises(type(error)) as caught:
        describe_client(responses)
    assert vars(caught.value) == vars(error)
    assert not responses


@pytest.mark.parametrize("preview", [False, True])
@pytest.mark.parametrize("failure", ["model", "json", "media_type"])
def test_describe_validates_complete_model(
    describe_client: Callable[[list[httpx.Response]], ResultDescription],
    failure: str,
    *,
    preview: bool,
) -> None:
    malformed = {"kind": "complete", "description": {}} if not preview else {}
    response = (
        httpx.Response(200, json=malformed)
        if failure == "model"
        else httpx.Response(
            200, content="{", headers={"Content-Type": "application/json"}
        )
        if failure == "json"
        else httpx.Response(200, text="not JSON")
    )
    responses = [_preview(), response] if preview else [response]
    with pytest.raises(DownloadError):
        describe_client(responses)
    assert not responses


@pytest.mark.parametrize("preview", [False, True])
def test_describe_returns_complete_independent_location_metadata(
    describe_client: Callable[[list[httpx.Response]], ResultDescription],
    *,
    preview: bool,
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
        responses = [_preview()] if preview else []
        responses.append(
            httpx.Response(
                200,
                json=payload
                if preview
                else {"kind": "complete", "description": payload},
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
