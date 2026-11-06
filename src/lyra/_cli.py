"""Console commands installed with the base distribution."""


def plugin(argv: list[str] | None = None) -> int:
    """Run the plugin authoring CLI with its SDK dependencies.

    Returns:
        The authoring command's exit code.

    Raises:
        ModuleNotFoundError: If an unrelated implementation import fails.
        SystemExit: If the SDK dependencies are missing.
    """
    try:
        from lyra.sdk.plugin_cli import main  # ruff: ignore[import-outside-top-level]
    except ModuleNotFoundError as exc:
        if exc.name not in {
            "pandera",
            "pyarrow",
            "numpy",
            "pandas",
            "geopandas",
            "shapely",
            "pyproj",
        }:
            raise
        message = f"lyra-plugin requires {exc.name}. Install 'lyra[sdk]'."
        raise SystemExit(message) from exc

    return main(argv)


def admin(argv: list[str] | None = None) -> int:
    """Run the administrative CLI with its client dependencies.

    Returns:
        The administrative command's exit code.

    Raises:
        ModuleNotFoundError: If an unrelated implementation import fails.
        SystemExit: If the client dependencies are missing.
    """
    try:
        from lyra.api.admin_cli import main  # ruff: ignore[import-outside-top-level]
    except ModuleNotFoundError as exc:
        if exc.name not in {"aiofiles", "aiohttp"}:
            raise
        message = f"lyra-admin requires {exc.name}. Install 'lyra[client]'."
        raise SystemExit(message) from exc

    return main(argv)
