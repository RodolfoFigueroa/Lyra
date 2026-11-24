"""Job ID fixtures shared by contract, transport, and client tests."""

import pytest

TEST_JOB_ID = "1" * 32
CLIENT_JOB_ID = "2" * 32


class ConversionTrap:
    def __str__(self) -> str:
        message = "Identifier validation must not call str"
        raise AssertionError(message)

    def __repr__(self) -> str:
        message = "Identifier validation must not call repr"
        raise AssertionError(message)


INVALID_IDS = [
    pytest.param("", id="empty"),
    "x" * 32,
    "A" * 32,
    "a" * 31,
    "a" * 33,
    "a" * 32 + "\n",
    " ",
    pytest.param(None, id="null"),
    pytest.param(True, id="boolean"),
    pytest.param(42, id="integer"),
    pytest.param(1.5, id="float"),
    pytest.param(TEST_JOB_ID.encode(), id="bytes"),
    pytest.param(bytearray(TEST_JOB_ID.encode()), id="bytearray"),
    pytest.param([], id="list"),
    pytest.param({}, id="dict"),
    pytest.param((TEST_JOB_ID,), id="tuple"),
    pytest.param({TEST_JOB_ID}, id="set"),
    pytest.param(ConversionTrap(), id="conversion-trap"),
]
