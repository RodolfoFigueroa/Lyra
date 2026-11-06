"""Loading boundaries work without importing higher layers."""

from test_support.imports import assert_fresh_imports


def test_manifest_reading_without_implementations() -> None:
    assert_fresh_imports(
        ["lyra.runtime.planning.manifests"],
        blocked=(
            "lyra.sdk",
            "lyra.data",
            "lyra.server",
            "numpy",
            "pandas",
            "geopandas",
            "pyarrow",
            "pandera",
            "shapely",
            "pyproj",
            "rasterio",
        ),
        assertions="""
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from lyra.contracts.plugins import PluginManifest
        from lyra.runtime.planning import manifests

        assert "lyra.runtime.loading" not in sys.modules
        with TemporaryDirectory() as directory:
            for source in Path("examples").glob("*/lyra.plugin.json"):
                expected = PluginManifest.model_validate_json(source.read_text())
                with (
                    patch.object(manifests.metadata, "version",
                                 return_value=expected.plugin.version),
                    patch.object(manifests.sys, "prefix", directory),
                ):
                    path = manifests.installed_manifest_path(expected.plugin.name)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(source.read_text())
                    name = expected.plugin.name
                    assert manifests.load_plugin_manifest(name) == expected
                    loaded = manifests.load_plugin_manifest(name, manifest_path=path)
                    assert loaded == expected
        """,
    )


def test_authoring_cli_builds_fixture_without_execution_layers() -> None:
    assert_fresh_imports(
        ["lyra.sdk", "lyra.sdk.plugin_cli"],
        blocked=("lyra.runtime", "lyra.api", "lyra.utils", "lyra.server"),
        assertions="""
        from pathlib import Path
        from shutil import copyfile, copytree
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from lyra.contracts.plugins import PluginManifest
        from lyra.sdk import BaseIO
        from lyra._cli import plugin as main

        source = Path("examples/city-plugin")
        manifest = "lyra.plugin.json"
        expected = PluginManifest.model_validate_json((source / manifest).read_text())
        with TemporaryDirectory() as directory:
            project = Path(directory)
            copyfile(source / "pyproject.toml", project / "pyproject.toml")
            copytree(source / "city_example", project / "city_example")
            with patch.object(BaseIO, "__init__") as acquire:
                assert main(["build-manifest", "--project", directory]) == 0
                acquire.assert_not_called()
            generated = (project / manifest).read_text()
            actual = PluginManifest.model_validate_json(generated)
            assert actual == expected
            diagram = project / "diagram.mmd"
            args = ["diagram", "--project", directory, "--output", str(diagram)]
            assert main(args) == 0
            rendered = diagram.read_text()
            assert all(node.name in rendered for node in actual.nodes)
        """,
    )


def test_catalog_discovery_and_acceptance_without_implementations() -> None:
    assert_fresh_imports(
        [
            "lyra.runtime.planning.catalog",
            "lyra.runtime.planning.discovery",
            "lyra.runtime.planning.preparation",
        ],
        blocked=(
            "lyra.sdk",
            "lyra.data",
            "lyra.runtime.loading",
            "lyra.runtime.execution",
            "lyra.runtime.local",
            "lyra.runtime.context",
            "lyra.runtime.locations",
            "lyra.runtime.resources",
            "lyra.server",
            "lyra.api",
            "lyra.utils",
            "numpy",
            "pandas",
            "geopandas",
            "pyarrow",
            "pandera",
            "shapely",
            "pyproj",
            "rasterio",
            "sqlalchemy",
            "redis",
            "fastapi",
            "city_example",
        ),
        assertions="""
        from pathlib import Path
        from lyra.contracts.accepted import PreparedExecution
        from lyra.contracts.plugins import PluginManifest
        from lyra.runtime.planning.catalog import compile_catalog
        from lyra.runtime.planning.discovery import Discovery
        from lyra.runtime.planning.preparation import prepare_execution

        manifest = PluginManifest.model_validate_json(
            Path("examples/city-plugin/lyra.plugin.json").read_text()
        )
        catalog = compile_catalog({manifest.plugin.name: manifest})
        view = Discovery(catalog)
        assert view.search("maintenance")
        assert view.inspect("city_street_maintenance").node == "city_street_maintenance"
        request = {
            "metric": "city_street_maintenance",
            "inputs": {"streets": {
                "collection": "example:city", "unit_type": "block",
                "ids": ["block-1", "block-2"],
            }},
        }
        plan = prepare_execution(catalog, request)
        assert isinstance(plan, PreparedExecution)
        assert plan.inputs[0].binding.operations == ("city_streets_for_blocks",)
        assert plan.computation_identity
        assert plan.revisions.bindings
        assert catalog.location_provider.declaration == manifest.location_provider
        assert PreparedExecution.model_validate_json(plan.model_dump_json()) == plan
        manifest.nodes.clear()
        assert prepare_execution(catalog, request) == plan
        """,
    )
