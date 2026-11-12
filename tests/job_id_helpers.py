"""Invalid job identifiers shared by contract and client tests."""

import pytest


class ConversionTrap:
    def __str__(self) -> str:
        message = "Identifier validation must not call str"
        raise AssertionError(message)

    def __repr__(self) -> str:
        message = "Identifier validation must not call repr"
        raise AssertionError(message)


INVALID_IDS = [
    pytest.param("", id="empty"),
    pytest.param(None, id="null"),
    pytest.param(True, id="boolean"),
    pytest.param(42, id="integer"),
    pytest.param(1.5, id="float"),
    pytest.param(b"job", id="bytes"),
    pytest.param(bytearray(b"job"), id="bytearray"),
    pytest.param([], id="list"),
    pytest.param({}, id="dict"),
    pytest.param(("job",), id="tuple"),
    pytest.param({"job"}, id="set"),
    pytest.param(ConversionTrap(), id="conversion-trap"),
]
