"""Test import isolation for the extracted Webpage Sorter Hermes plugin."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
root = str(PLUGIN_ROOT)
if root in sys.path:
    sys.path.remove(root)
sys.path.insert(0, root)

# The Hermes plugin entrypoint is the repository root __init__.py. Load it as
# package name `source_lab` so existing compatibility tests can import it and
# its relative imports resolve correctly.
if "source_lab" in sys.modules:
    del sys.modules["source_lab"]
spec = importlib.util.spec_from_file_location(
    "source_lab",
    PLUGIN_ROOT / "__init__.py",
    submodule_search_locations=[str(PLUGIN_ROOT)],
)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules["source_lab"] = module
spec.loader.exec_module(module)

if "agent" not in sys.modules:
    agent_module = types.ModuleType("agent")
    auxiliary_module = types.ModuleType("agent.auxiliary_client")

    def _missing_call_llm(**_kwargs):
        raise RuntimeError("agent.auxiliary_client.call_llm was not monkeypatched")

    auxiliary_module.call_llm = _missing_call_llm
    agent_module.auxiliary_client = auxiliary_module
    sys.modules["agent"] = agent_module
    sys.modules["agent.auxiliary_client"] = auxiliary_module
