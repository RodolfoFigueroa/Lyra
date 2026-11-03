"""Keep the standalone example's tests independent of service availability."""

import socket

import pytest


@pytest.fixture
def no_external_services(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject network connections and name resolution in example tests."""

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Example tests must use local data and in-process fakes")

    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, forbidden)
    for name in ("create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, name, forbidden)
