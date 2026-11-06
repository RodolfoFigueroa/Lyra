"""Isolated, reproducible performance experiments for Lyra."""

import sys
from pathlib import Path

# ASV imports this package from a temporary directory. Expose its checkout siblings.
if (root := str(Path(__file__).resolve().parent.parent)) not in sys.path:  # ruff: ignore[non-empty-init-module]
    sys.path.insert(0, root)
