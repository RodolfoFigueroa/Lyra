"""Helpers to start a real catalog using installed plugin metadata doubles."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.plugin_helpers import plugin_config

if TYPE_CHECKING:
    from pathlib import Path

    from lyra.contracts.config import LyraConfig


def configure_catalog_plugins(config: LyraConfig, paths: list[Path]) -> None:
    config.plugins.installed = [plugin_config(path) for path in paths]
