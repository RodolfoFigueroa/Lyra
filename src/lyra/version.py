"""Resolve the installed Lyra distribution version."""

from importlib.metadata import version

APP_VERSION = version("lyra")
