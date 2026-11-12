"""Inspect and load plugin authoring factories."""

from __future__ import annotations

import importlib
import inspect
import re
from importlib import metadata

from lyra.contracts.config import normalize_distribution_name
from lyra.sdk.plugin import PluginDefinition

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_FACTORY_PATTERN = re.compile(
    rf"^(?P<module>{_IDENTIFIER}(?:\.{_IDENTIFIER})*):(?P<attribute>{_IDENTIFIER})$"
)


class PluginLoadError(RuntimeError):
    """Raised when a configured plugin factory cannot produce a definition."""


def load_installed_plugin_definition(distribution: str) -> PluginDefinition:
    """Load one explicitly selected installed distribution's plugin entry point.

    Returns:
        The live definition returned by its ``lyra.plugins:plugin`` factory.

    Raises:
        PluginLoadError: If selection, registration, import, or factory validation
            fails. No manifest or custom project metadata is required.
    """
    try:
        name = normalize_distribution_name(distribution)
    except ValueError as exc:
        msg = f"Invalid plugin distribution {distribution!r}: {exc}"
        raise PluginLoadError(msg) from exc
    try:
        installed = metadata.distribution(name)
    except metadata.PackageNotFoundError as exc:
        msg = f"Plugin distribution {name!r} is not installed"
        raise PluginLoadError(msg) from exc
    entries = [
        entry
        for entry in installed.entry_points
        if entry.group == "lyra.plugins" and entry.name == "plugin"
    ]
    if len(entries) != 1:
        msg = (
            f"Plugin distribution {name!r} must provide exactly one entry point "
            f"named 'plugin' in group 'lyra.plugins'; found {len(entries)}"
        )
        raise PluginLoadError(msg)
    entry = entries[0]
    context = f"{entry.value} (distribution {name!r}, lyra.plugins:plugin)"
    try:
        factory = entry.load()
    except Exception as exc:
        msg = f"Could not import plugin factory {context!r}: {exc}"
        raise PluginLoadError(msg) from exc
    return _invoke_factory(factory, context)


def load_plugin_definition(factory_ref: str) -> PluginDefinition:
    """Import and invoke one synchronous, parameterless plugin factory.

    Returns:
        The plugin definition produced by the referenced factory.

    Raises:
        PluginLoadError: If the reference, factory, or returned definition is invalid.
    """
    match = _FACTORY_PATTERN.fullmatch(factory_ref)
    if match is None:
        msg = f"Plugin factory must use 'module:attribute' format: {factory_ref!r}"
        raise PluginLoadError(msg)

    try:
        module = importlib.import_module(match.group("module"))
        factory = getattr(module, match.group("attribute"))
    except (ImportError, AttributeError) as exc:
        msg = f"Could not import plugin factory {factory_ref!r}: {exc}"
        raise PluginLoadError(msg) from exc

    return _invoke_factory(factory, factory_ref)


def _invoke_factory(factory: object, factory_ref: str) -> PluginDefinition:
    if not callable(factory):
        msg = f"Plugin factory {factory_ref!r} must resolve to a callable"
        raise PluginLoadError(msg)
    if (
        inspect.iscoroutinefunction(factory)
        or inspect.isasyncgenfunction(factory)
        or inspect.iscoroutinefunction(factory.__call__)
        or inspect.isasyncgenfunction(factory.__call__)
    ):
        msg = f"Plugin factory {factory_ref!r} must be synchronous"
        raise PluginLoadError(msg)
    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError) as exc:
        msg = f"Could not inspect plugin factory {factory_ref!r}: {exc}"
        raise PluginLoadError(msg) from exc
    if signature.parameters:
        msg = f"Plugin factory {factory_ref!r} must declare no parameters"
        raise PluginLoadError(msg)

    try:
        definition = factory()
    except Exception as exc:
        msg = f"Plugin factory {factory_ref!r} failed: {exc}"
        raise PluginLoadError(msg) from exc
    if inspect.isawaitable(definition) or inspect.isasyncgen(definition):
        if inspect.iscoroutine(definition):
            definition.close()
        msg = f"Plugin factory {factory_ref!r} must be synchronous"
        raise PluginLoadError(msg)
    if not isinstance(definition, PluginDefinition):
        msg = f"Plugin factory {factory_ref!r} must return PluginDefinition"
        raise PluginLoadError(msg)
    return definition
