"""Helpers to start a real catalog using installed plugin metadata doubles."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from tests.plugin_helpers import plugin_config

if TYPE_CHECKING:
    from pathlib import Path

    from lyra_app.config import LyraConfig


def configure_catalog_plugins(config: LyraConfig, paths: list[Path]) -> None:
    overrides = {
        name: queue
        for plugin in config.plugins.installed
        for name, queue in plugin.routing.items()
    }
    plugins = []
    for path in paths:
        raw = json.loads((path / "lyra.plugin.json").read_text())
        names = {metric.get("name") for metric in raw.get("nodes", [])}
        plugins.append(
            plugin_config(
                path,
                routing={
                    name: queue for name, queue in overrides.items() if name in names
                },
            )
        )
    config.plugins.installed = plugins
