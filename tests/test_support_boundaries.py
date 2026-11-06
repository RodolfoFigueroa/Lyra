"""Check dependency direction for test fixtures, shared workloads, and ASV adapters."""

import ast
import re
from collections.abc import Iterator
from importlib.util import resolve_name
from pathlib import Path

import pytest

from test_support.imports import assert_fresh_imports

SHARED = Path("test_support")
BENCHMARKS = Path("benchmarks")
ADAPTER_TEST = Path("tests/test_benchmark_adapters.py")


def import_targets(tree: ast.Module, package: str) -> Iterator[str]:
    """Inspect imports and plugin factory references without executing test code.

    Yields:
        Absolute targets, including possible submodules in from-imports.
    """
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            # Manifest and pyproject fixtures load module:factory references at runtime.
            yield from re.findall(
                r"\b([A-Za-z_]\w*(?:\.\w+)*):[A-Za-z_]\w*\b", node.value
            )
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "pytest_plugins"
            for target in node.targets
        ):
            for value in ast.walk(node.value):
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    yield value.value
        elif isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name] = alias.name
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            module = resolve_name("." * node.level + (node.module or ""), package)
            yield module
            for alias in node.names:
                target = f"{module}.{alias.name}"
                aliases[alias.asname or alias.name] = target
                yield target

    yield from dynamic_imports(tree, package, aliases)


def dynamic_imports(
    tree: ast.Module, package: str, aliases: dict[str, str]
) -> Iterator[str]:
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        root, dot, attr = ast.unparse(call.func).partition(".")
        loader = aliases.get(root, root) + dot + attr
        if loader not in {
            "importlib.import_module",
            "__import__",
            "builtins.__import__",
            "runpy.run_module",
        }:
            continue
        arguments = {kw.arg: kw.value for kw in call.keywords}
        name = (
            call.args[0]
            if call.args
            else arguments.get("name") or arguments.get("mod_name")
        )
        if isinstance(name, ast.Constant) and isinstance(name.value, str):
            anchor = call.args[1] if len(call.args) > 1 else arguments.get("package")
            relative_to = (
                anchor.value
                if isinstance(anchor, ast.Constant) and isinstance(anchor.value, str)
                else package
            )
            yield resolve_name(name.value, relative_to)


def test_checkout_import_boundaries() -> None:
    paths = [
        *Path("tests").rglob("*.py"),
        *SHARED.rglob("*.py"),
        *BENCHMARKS.rglob("*.py"),
        *Path("examples").glob("*/tests/**/*.py"),
    ]
    collection = {
        path.stem
        for path in paths
        if path.match("test_*.py")
        or path.match("*_test.py")
        or path.name == "conftest.py"
    }
    for path in paths:
        shared = path.is_relative_to(SHARED)
        benchmark = path.is_relative_to(BENCHMARKS)
        package = ".".join(path.parent.parts)
        for target in import_targets(ast.parse(path.read_text()), package):
            parts = target.split(".")
            assert collection.isdisjoint(parts), (path, target)
            if shared or benchmark:
                assert {"pytest", "_pytest", "tests"}.isdisjoint(parts), (path, target)
            if not benchmark and path != ADAPTER_TEST:
                assert parts[0] not in {"asv", "benchmarks"}, (path, target)


@pytest.mark.parametrize("path", sorted(SHARED.rglob("*.py")))
def test_shared_module_imports_independently(path: Path) -> None:
    module = ".".join(path.with_suffix("").parts).removesuffix(".__init__")
    assert_fresh_imports(
        [module], blocked=("tests", "pytest", "_pytest", "benchmarks", "asv")
    )


def test_benchmark_adapters_import_without_pytest() -> None:
    modules = [".".join(path.with_suffix("").parts) for path in BENCHMARKS.glob("*.py")]
    assert_fresh_imports(modules, blocked=("tests", "pytest", "_pytest"))
