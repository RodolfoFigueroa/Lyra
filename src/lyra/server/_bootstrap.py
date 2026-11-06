"""Dependency diagnostics for server module commands."""

from collections.abc import Generator
from contextlib import contextmanager


@contextmanager
def command_imports(module: str) -> Generator[None, None, None]:
    """Report missing server dependencies when bootstrapping a module command.

    Yields:
        Control to the module's implementation imports.

    Raises:
        SystemExit: If the command needs an absent server dependency.
        ModuleNotFoundError: For ordinary imports and unrelated failures.
    """
    try:
        yield
    except ModuleNotFoundError as exc:
        if module != "__main__" or exc.name not in {
            "fastapi",
            "uvicorn",
            "redis",
            "rq",
            "mcp",
            "starlette",
            "filelock",
            "pandera",
            "pyarrow",
            "geopandas",
            "pandas",
            "numpy",
            "shapely",
            "pyproj",
        }:
            raise
        message = f"The server requires {exc.name}. Install 'lyra[server]'."
        raise SystemExit(message) from exc
