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
    InvalidResultReferenceError,
    ResultAccessError,
    ResultIntegrityError,
    ResultNotFoundError,
)
from lyra.contracts.results import ResultDescriptionPreview
from lyra.contracts.retrieval_errors import error_payload, error_status

if TYPE_CHECKING:
    from collections.abc import Callable

_REFERENCE = "a" * 32
_DOWNLOAD = f"https://example.test/api/results/{_REFERENCE}/description/download"


@pytest.fixture(params=["sync", "async"])
def describe_client(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Callable[[list[httpx.Response]], None]:
    def run(responses: list[httpx.Response]) -> None:
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
                    client.results.describe(_REFERENCE)
            else:

                async def fetch() -> None:
                    async with AsyncLyraClient(
                        "example.test/api", agent_api_key="secret"
                    ) as client:
                        await client.results.describe(_REFERENCE)

                asyncio.run(fetch())

    return run


def _preview() -> httpx.Response:
    return httpx.Response(
        200,
        json=ResultDescriptionPreview(
            result_ref=_REFERENCE,
            execution_id="b" * 32,
            download_url=_DOWNLOAD,
        ).model_dump(mode="json"),
    )


@pytest.mark.parametrize("preview", [False, True])
@pytest.mark.parametrize(
    "error",
    [
        InvalidResultReferenceError(_REFERENCE),
        ResultNotFoundError(_REFERENCE),
        ResultIntegrityError(_REFERENCE),
    ],
)
def test_describe_preserves_whole_result_errors(
    describe_client: Callable[[list[httpx.Response]], None],
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
    describe_client: Callable[[list[httpx.Response]], None],
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
