"""Source-level dependency rules for the migrated Lyra packages."""

import ast
import sys
from importlib.util import resolve_name
from pathlib import Path

import pytest

# The longest matching package owns a module (planning has a narrower boundary).
LYRA_DEPENDENCIES = {
    "lyra.contracts": (),
    "lyra.data": ("lyra.contracts",),
    "lyra.sdk": ("lyra.contracts", "lyra.data"),
    "lyra.runtime.planning": ("lyra.contracts",),
    "lyra.runtime": ("lyra.contracts", "lyra.data", "lyra.sdk"),
    "lyra.server": ("lyra.contracts", "lyra.data", "lyra.runtime"),
    "lyra.api": ("lyra.contracts",),
    "lyra.utils": ("lyra.contracts", "lyra.data"),
}
CONTRACT_LIBS = {"jsonschema", "pydantic", "typing_extensions"}
NATIVE_LIBS = {
    "geopandas",
    "numpy",
    "pandas",
    "pyarrow",
    "pyogrio",
    "pyproj",
    "shapely",
}
THIRD_PARTY = {
    "lyra.contracts": CONTRACT_LIBS,
    "lyra.data": NATIVE_LIBS | {"pydantic"},
    "lyra.sdk": CONTRACT_LIBS | NATIVE_LIBS | {"pandera", "pydantic_core"},
    "lyra.runtime.planning": {"jsonschema", "pydantic"},
    "lyra.runtime": CONTRACT_LIBS
    | NATIVE_LIBS
    | {"filelock", "pandera", "pydantic_core"},
    "lyra.api": {
        "aiofiles",
        "aiohttp",
        "geopandas",
        "pandas",
        "pydantic",
        "pyproj",
        "shapely",
        "typing_extensions",
    },
    "lyra.utils": {"ee", "geopandas", "pandas", "shapely"},
    "lyra.server": {
        "fastapi",
        "mcp",
        "pydantic",
        "redis",
        "rq",
        "starlette",
        "typing_extensions",
        "uvicorn",
    },
}


def _imports(tree: ast.Module, module: str, package: str) -> list[str]:
    names = []
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)
                if alias.asname:
                    aliases[alias.asname] = alias.name
        elif isinstance(node, ast.ImportFrom):
            source = resolve_name("." * node.level + (node.module or ""), package)
            for alias in node.names:
                target = f"{source}.{alias.name}"
                names.append(target)
                aliases[alias.asname or alias.name] = target

    # Resolve literal dynamic imports under the same rules as import statements.
    # The SDK loader alone may load the module from the selected factory reference.
    factory_loading = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and module == "lyra.sdk.loading"
            and node.name == "load_plugin_definition"
        ),
        None,
    )
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        root, dot, attr = ast.unparse(call.func).partition(".")
        loader = aliases.get(root, root) + dot + attr
        if loader not in {
            "importlib.import_module",
            "__import__",
            "builtins.__import__",
        }:
            continue
        assert len(call.args) == 1, (module, call.lineno)
        assert not call.keywords, (module, call.lineno)
        target = call.args[0]
        if (
            factory_loading
            and call in ast.walk(factory_loading)
            and ast.unparse(target) == "match.group('module')"
        ):
            continue
        assert isinstance(target, ast.Constant), (
            module,
            call.lineno,
            "unreviewed dynamic import",
        )
        assert isinstance(target.value, str)
        assert not target.value.startswith("."), (module, call.lineno)
        names.append(target.value)
    return names


def test_sources_outside_feature_packages_follow_root_rules() -> None:
    root = Path("src")
    feature_paths = [root.joinpath(*name.split(".")) for name in LYRA_DEPENDENCIES]
    allowed = {
        Path("lyra/__init__.py"): set(),
        Path("lyra/version.py"): set(),
        Path("lyra/_cli.py"): {"lyra.sdk.plugin_cli", "lyra.api.admin_cli"},
    }
    for path in root.rglob("*.py"):
        if any(layer in path.parents for layer in feature_paths):
            continue  # Checked below under the owning feature's rules.
        relative = path.relative_to(root)
        assert relative in allowed, f"No layer rule for {path}"
        module = ".".join(relative.with_suffix("").parts)
        for target in _imports(ast.parse(path.read_text()), module, "lyra"):
            assert target.partition(".")[0] in sys.stdlib_module_names or any(
                target == prefix or target.startswith(prefix + ".")
                for prefix in allowed[relative]
            ), (module, target)


@pytest.mark.parametrize("layer", LYRA_DEPENDENCIES)
def test_production_imports_follow_layer_rules(layer: str) -> None:
    root = Path("src")
    directory = root.joinpath(*layer.split("."))
    assert directory.is_dir()
    for path in directory.rglob("*.py"):
        package = ".".join(path.parent.relative_to(root).parts)
        module = package if path.stem == "__init__" else f"{package}.{path.stem}"
        if layer == "lyra.runtime" and path.is_relative_to(directory / "planning"):
            continue  # Covered by the planning rule.
        imports = _imports(ast.parse(path.read_text()), module, package)
        allowed = (layer, *LYRA_DEPENDENCIES[layer])
        if layer != "lyra.contracts":
            allowed += ("lyra.version",)
        external = sys.stdlib_module_names | THIRD_PARTY[layer]
        for target in imports:
            assert target.partition(".")[0] in external or any(
                target == prefix or target.startswith(prefix + ".")
                for prefix in allowed
            ), (module, target)
