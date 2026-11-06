"""Fresh interpreter imports with transitive dependency blockers."""

import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import textwrap
from collections.abc import Sequence


def assert_fresh_imports(
    modules: Sequence[str],
    *,
    blocked: Sequence[str],
    unavailable: Sequence[str] = (),
    assertions: str = "",
) -> None:
    """Import modules in a fresh process with forbidden dependencies unavailable."""
    script = textwrap.dedent(
        f"""
        import importlib
        import importlib.abc
        import sys

        blocked = {tuple(blocked)!r}
        unavailable = {tuple(unavailable)!r}

        class Blocker(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if any(fullname == name or fullname.startswith(name + '.')
                       for name in blocked):
                    raise AssertionError('Forbidden import: ' + fullname)
                if any(fullname == name or fullname.startswith(name + '.')
                       for name in unavailable):
                    raise ModuleNotFoundError('Unavailable: ' + fullname, name=fullname)

        assert not any(name == prefix or name.startswith(prefix + '.')
                       for name in sys.modules for prefix in blocked + unavailable)
        sys.meta_path.insert(0, Blocker())
        for module in {tuple(modules)!r}:
            importlib.import_module(module)
        """
    )
    result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        [sys.executable, "-c", script + "\n" + textwrap.dedent(assertions)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
