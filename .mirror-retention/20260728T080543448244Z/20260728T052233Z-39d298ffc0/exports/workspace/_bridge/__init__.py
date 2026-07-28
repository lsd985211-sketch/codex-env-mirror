"""Package compatibility for the workspace bridge script collection.

Ownership: make legacy sibling and ``_bridge`` imports resolve when bridge
modules or tests are invoked through ``workspace._bridge`` package names.
Non-goals: convert the script collection to package-relative imports, select
test suites, or mutate persistent runtime state.
State behavior: process-local ``sys.path`` compatibility only.
Caller context: Python package initialization before a bridge module import;
direct script execution keeps its existing import behavior.
"""

from __future__ import annotations

import sys
from pathlib import Path


BRIDGE_ROOT = Path(__file__).resolve().parent

# The bridge began as a directly executed script collection.  Package-mode
# callers still encounter both ``import sibling`` and ``from _bridge`` in the
# same dependency graph, so expose one package identity before loading either.
sys.modules.setdefault("_bridge", sys.modules[__name__])
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))
