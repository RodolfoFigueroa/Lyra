from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from lyra_app.routes import admin
from tests.config_helpers import load_test_config
from tests.runtime_helpers import make_runtime

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def _assert_http_error(
    status_code: int,
    func: Callable[..., object],
    *args: object,
) -> HTTPException:
    with pytest.raises(HTTPException) as exc_info:
        func(*args)
    assert exc_info.value.status_code == status_code
    return exc_info.value


def test_require_admin_key_reads_configured_secret_file(tmp_path: Path) -> None:
    runtime = make_runtime(load_test_config(tmp_path))

    admin.require_admin_key(
        runtime,
        HTTPAuthorizationCredentials(scheme="Bearer", credentials="admin-secret"),
    )

    with pytest.raises(HTTPException) as exc_info:
        admin.require_admin_key(
            runtime, HTTPAuthorizationCredentials(scheme="Bearer", credentials="wrong")
        )

    assert exc_info.value.status_code == 403


def test_admin_router_requires_bearer_key_for_all_routes() -> None:
    assert admin.router.prefix == "/admin"
    assert any(
        dependency.dependency is admin.require_admin_key
        for dependency in admin.router.dependencies
    )
