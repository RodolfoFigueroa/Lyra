"""Loading boundaries work without importing higher layers."""

from test_support.imports import assert_fresh_imports


def test_authoring_cli_builds_fixture_without_execution_layers() -> None:
    assert_fresh_imports(
        ["lyra.sdk", "lyra.sdk.plugin_cli"],
        blocked=(
            "ibis.backends.duckdb",
            "ibis.backends.postgres",
            "duckdb",
            "psycopg",
            "lyra.runtime.execution",
            "lyra.runtime.local",
            "lyra.runtime.locations",
            "lyra.api",
            "lyra.utils",
            "lyra.server",
        ),
        assertions="""
        from pathlib import Path
        from unittest.mock import Mock
        import lyra.runtime.resources as resources
        resources.resolve_secret = Mock(side_effect=AssertionError("Credential read"))
        from shutil import copyfile, copytree
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from lyra.contracts.plugins import PluginInspection
        from lyra.sdk import BaseIO
        from lyra._cli import plugin as main

        source = Path("examples/city-plugin")
        from lyra.sdk.plugin_cli import render_manifest
        expected = PluginInspection.model_validate_json(render_manifest(source))
        with TemporaryDirectory() as directory:
            project = Path(directory)
            copyfile(source / "pyproject.toml", project / "pyproject.toml")
            copytree(source / "city_example", project / "city_example")
            with patch.object(BaseIO, "__init__") as acquire:
                assert main(["describe", "--project", directory]) == 0
                acquire.assert_not_called()
            generated = render_manifest(project)
            actual = PluginInspection.model_validate_json(generated)
            assert actual == expected
            diagram = project / "diagram.mmd"
            args = ["diagram", "--project", directory, "--output", str(diagram)]
            assert main(args) == 0
            rendered = diagram.read_text()
            assert all(node["name"] in rendered for node in actual.nodes)
        resources.resolve_secret.assert_not_called()
        """,
    )


def test_catalog_discovery_and_acceptance_without_services() -> None:
    assert_fresh_imports(
        [
            "lyra.runtime.planning.catalog",
            "lyra.runtime.planning.discovery",
            "lyra.runtime.planning.preparation",
        ],
        blocked=(
            "ibis.backends.duckdb",
            "ibis.backends.postgres",
            "duckdb",
            "psycopg",
            "lyra.runtime.execution",
            "lyra.runtime.local",
            "lyra.runtime.context",
            "lyra.runtime.locations",
            "lyra.server",
            "lyra.api",
            "lyra.utils",
            "sqlalchemy",
            "redis",
            "fastapi",
        ),
        assertions="""
        from pathlib import Path
        from unittest.mock import Mock
        import lyra.runtime.resources as resources
        resources.resolve_secret = Mock(side_effect=AssertionError("Credential read"))
        from lyra.contracts.accepted import PreparedExecution
        from lyra.contracts.plugins import PluginInspection
        from lyra.runtime.planning.catalog import compile_live_catalog
        from lyra.runtime.planning.discovery import Discovery
        from lyra.runtime.planning.preparation import prepare_execution

        from lyra.sdk.plugin_cli import render_manifest
        manifest = PluginInspection.model_validate_json(
            render_manifest(Path("examples/city-plugin"))
        )
        import sys
        sys.path.insert(0, str(Path("examples/city-plugin").resolve()))
        from lyra.sdk.loading import load_plugin_definition
        catalog = compile_live_catalog({
            manifest.plugin.name: load_plugin_definition(manifest.factory)
        })
        view = Discovery(catalog)
        assert view.search("maintenance")
        assert view.inspect("city_street_maintenance").node == "city_street_maintenance"
        request = {
            "metric": "city_street_maintenance",
            "inputs": {"streets": {
                "provider": "city_blocks",
                "ids": ["block-1", "block-2"],
            }},
        }
        plan = prepare_execution(catalog, request)
        assert isinstance(plan, PreparedExecution)
        assert plan.inputs[0].binding.operations == ("city_streets_for_blocks",)
        assert plan.computation_identity
        assert plan.revisions.bindings
        declared = {item.declaration.resource for item in catalog.providers.values()}
        assert declared == {item.resource for item in manifest.providers}
        assert PreparedExecution.model_validate_json(plan.model_dump_json()) == plan
        manifest.nodes.clear()
        assert prepare_execution(catalog, request) == plan
        resources.resolve_secret.assert_not_called()
        """,
    )
